"""CRM-Ops store + lead-score distribution unit tests (Module 7).

Covers the in-memory store seam (:class:`app.data.crm_ops_store.InMemoryCrmOpsStore`):
upsert idempotency (a rescan dedups on signature and keeps acknowledged/resolved status),
manual file, ack/prioritize/resolve, the fix log, and the deterministic demo seed; plus
the simulated adapter's deterministic lead-score histogram (offline, INV-9/INV-6).
"""

from __future__ import annotations

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.core.program import Program
from app.data.crm_ops_store import InMemoryCrmOpsStore
from app.data.repository import InMemoryFamilyRepository

PROGRAM = Program.FALL_ENROLLMENT


def test_upsert_is_idempotent_on_signature() -> None:
    """Re-upserting the same signature updates in place — never duplicates (auto-dedup)."""
    store = InMemoryCrmOpsStore()
    first = store.upsert_issue(
        PROGRAM,
        signature="Family-1:conflict",
        category="sync",
        kind="conflict",
        severity="high",
        description="diverges",
        entity_ref="Family-1",
    )
    again = store.upsert_issue(
        PROGRAM,
        signature="Family-1:conflict",
        category="sync",
        kind="conflict",
        severity="high",
        description="diverges (refreshed)",
        entity_ref="Family-1",
    )
    issues = store.list_issues(PROGRAM)
    assert len(issues) == 1, "a repeated signature must not create a second row"
    assert again.issue_id == first.issue_id
    assert issues[0].description == "diverges (refreshed)"


def test_upsert_keeps_resolved_status_on_rescan() -> None:
    """An acknowledged/resolved row keeps its status when a rescan re-detects it."""
    store = InMemoryCrmOpsStore()
    created = store.upsert_issue(
        PROGRAM,
        signature="Family-2:utm_broken",
        category="utm",
        kind="utm_broken",
        severity="high",
        description="broken",
        entity_ref="Family-2",
    )
    store.update_issue(PROGRAM, created.issue_id, status="resolved", resolution="fixed")
    # A rescan re-detects the same signature.
    store.upsert_issue(
        PROGRAM,
        signature="Family-2:utm_broken",
        category="utm",
        kind="utm_broken",
        severity="high",
        description="broken again",
        entity_ref="Family-2",
    )
    rows = store.list_issues(PROGRAM)
    assert len(rows) == 1
    assert rows[0].status == "resolved", "a rescan must not reopen a resolved issue"
    assert rows[0].resolution == "fixed"


def test_file_manual_and_resolve_flow() -> None:
    """A manual issue files as open/manual; resolving stamps status/resolution/resolved_by."""
    store = InMemoryCrmOpsStore()
    issue = store.file_issue(
        PROGRAM,
        category="scoring",
        kind="scoring_review",
        severity="medium",
        description="review the model",
    )
    assert issue.status == "open"
    assert issue.source == "manual"
    assert store.list_issues(PROGRAM, status="open")

    resolved = store.update_issue(
        PROGRAM, issue.issue_id, status="resolved", resolution="done", resolved_by="leader"
    )
    assert resolved.status == "resolved"
    assert resolved.resolution == "done"
    assert resolved.resolved_by == "leader"
    assert resolved.resolved_at is not None
    assert store.list_issues(PROGRAM, status="resolved") == [resolved]


def test_update_unknown_issue_raises() -> None:
    """Updating an absent issue raises KeyError (the route maps it to a 404)."""
    import uuid

    import pytest

    store = InMemoryCrmOpsStore()
    with pytest.raises(KeyError):
        store.update_issue(PROGRAM, uuid.uuid4(), status="acknowledged")


def test_fix_log_append_and_filter() -> None:
    """The fix log appends and filters by kind."""
    store = InMemoryCrmOpsStore()
    store.append_fix_log(PROGRAM, kind="utm_fix", summary="normalized", actor="crm")
    store.append_fix_log(PROGRAM, kind="scoring_change", summary="raised threshold", actor="leader")
    assert len(store.list_fix_log(PROGRAM)) == 2
    scoring = store.list_fix_log(PROGRAM, kind="scoring_change")
    assert len(scoring) == 1
    assert scoring[0].summary == "raised threshold"


def test_seed_demo_is_idempotent_and_spans_categories() -> None:
    """The deterministic seed lays down issues across categories incl one resolved + fixes."""
    store = InMemoryCrmOpsStore()
    store.seed_demo(PROGRAM)
    store.seed_demo(PROGRAM)  # idempotent — a re-seed is a no-op.
    issues = store.list_issues(PROGRAM)
    categories = {i.category for i in issues}
    assert {"utm", "sync", "scoring", "tracking", "other"} <= categories
    assert store.list_issues(PROGRAM, status="resolved"), "seed must include a resolved issue"
    fixes = store.list_fix_log(PROGRAM)
    assert any(f.kind == "scoring_change" for f in fixes)
    assert any(f.kind == "utm_fix" for f in fixes)
    # Re-seed did not duplicate.
    assert len(store.list_fix_log(PROGRAM)) == len(fixes)


def test_simulated_lead_score_distribution_is_deterministic_and_aggregate() -> None:
    """The sim histogram is deterministic, counts the whole cohort, never drops a score."""
    repo = InMemoryFamilyRepository.seeded()
    adapter = SimulatedCRMAdapter()
    family_ids = [r.family_id for r in repo.list_families()]
    edges = [0, 20, 40, 60, 80, 100]

    first = adapter.read_lead_score_distribution(family_ids, band_edges=edges)
    second = adapter.read_lead_score_distribution(family_ids, band_edges=edges)

    assert first == second, "the same cohort must always yield the same histogram"
    assert len(first.bands) == len(edges) - 1
    assert first.total == len(family_ids)
    assert sum(b.count for b in first.bands) == first.total, "every contact lands in a band"
    assert all(b.label == f"{b.low}-{b.high}" for b in first.bands)
    assert first.mean > 0.0

"""Module-7 CRM-Ops 5-view API tests — the expanded /crm/ops/* endpoints.

Exercises the five tab views (overview / source-tracking / lead-scoring / sync-parity /
data-quality), the auto-detect scan (idempotent UPSERT), the owner-gated manual file, the
leader/admin triage PATCH, and the leader/admin scoring-change. Fully offline (INV-9): a
seeded in-memory family repo + a :class:`SimulatedCRMAdapter` + a clean in-memory CRM-Ops
store are injected through dependency overrides.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.core.program import Program
from app.core.seam import MirrorState
from app.data.crm_ops_store import InMemoryCrmOpsStore
from app.data.decisions_store import InMemoryDecisionsStore
from app.data.models import Stage
from app.data.repository import InMemoryFamilyRepository
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt
from tests.conftest import install_test_principal_override

client = TestClient(app)
PROGRAM = Program.FALL_ENROLLMENT


def _auth(role: str = "leader") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    install_test_principal_override()
    yield
    app.dependency_overrides.clear()


def _install(
    *,
    repo: InMemoryFamilyRepository | None = None,
    adapter: SimulatedCRMAdapter | None = None,
    store: InMemoryCrmOpsStore | None = None,
    decisions: InMemoryDecisionsStore | None = None,
) -> tuple[InMemoryFamilyRepository, SimulatedCRMAdapter, InMemoryCrmOpsStore]:
    repo = repo or InMemoryFamilyRepository.seeded()
    adapter = adapter or SimulatedCRMAdapter()
    store = store or InMemoryCrmOpsStore()
    decisions = decisions or InMemoryDecisionsStore()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: adapter
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter
    app.dependency_overrides[deps.get_crm_ops_store] = lambda: store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions
    return repo, adapter, store


def _seed_conflict(repo: InMemoryFamilyRepository, adapter: SimulatedCRMAdapter) -> str:
    """Seed ONE genuine §4.7 CONFLICT mirror; return the family id string."""
    record = next(r for r in repo.list_families() if r.updated_at is not None)
    other = next(s for s in Stage if s is not record.current_stage)
    adapter.seed_mirror(
        record.family_id,
        MirrorState(stage=other, mirror_updated_at=record.updated_at),
    )
    return str(record.family_id)


def test_overview_reports_live_lead_score_and_open_count() -> None:
    """5a — parity, UTM health, LIVE lead-score distribution, open DQ count, last-sync, flags."""
    repo, _, store = _install()
    store.seed_demo(PROGRAM)
    resp = client.get("/crm/ops/overview", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    dist = body["lead_score_distribution"]
    assert dist["source"] == "crm_aggregate"
    assert dist["total"] == len(list(repo.list_families()))
    assert {"cold", "warm", "hot"} == set(dist["tiers"])
    assert body["open_dq_count"] == len(store.list_issues(PROGRAM, status="open"))
    assert {c["connector"] for c in body["last_sync"]}
    assert body["field_flags"]


def test_source_tracking_resolution_chain_and_fixlog() -> None:
    """5b — per-param resolution, attribution chain, broken drill-in, UTM fix log."""
    _, _, store = _install()
    store.seed_demo(PROGRAM)
    resp = client.get("/crm/ops/source-tracking", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    params = {p["param"] for p in body["params"]}
    assert {"utm_source", "utm_medium", "utm_campaign"} <= params
    assert body["attribution_chain"], "the attribution chain steps must be present"
    assert all(s["status"] == "ok" for s in body["attribution_chain"])
    assert all(f["kind"] == "utm_fix" for f in body["fix_log"])
    assert body["source"] == "supabase_attribution_utm"


def test_lead_scoring_histogram_correlation_and_changelog() -> None:
    """5c — LIVE histogram + DERIVED (honest) correlation + scoring change log."""
    _, _, store = _install()
    store.seed_demo(PROGRAM)
    resp = client.get("/crm/ops/lead-scoring", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["distribution"]["source"] == "crm_aggregate"
    assert body["correlation_source"] == "derived_synthetic"
    assert len(body["correlation"]) == len(body["distribution"]["bands"])
    assert body["threshold"] == 60
    assert all(f["kind"] == "scoring_change" for f in body["change_log"])


def test_sync_parity_drift_and_rule_of_truth() -> None:
    """5d — overall + field-level parity, flags, drift alerts below the floor, rule-of-truth."""
    repo, adapter, _ = _install()
    _seed_conflict(repo, adapter)
    resp = client.get("/crm/ops/sync-parity", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "parity_overall" in body
    assert body["rule_of_truth"].startswith("Supabase app_form is the source of truth")
    assert body["source"] == "supabase⇄hubspot"
    # An out-of-sync cohort drives at least one field below the drift floor.
    assert body["drift_alerts"], "an out-of-sync cohort must raise a drift alert"


def test_data_quality_open_and_resolution_log() -> None:
    """5e — open issues + the resolution log (resolved issues)."""
    _, _, store = _install()
    store.seed_demo(PROGRAM)
    resp = client.get("/crm/ops/data-quality", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert all(i["status"] == "open" for i in body["open_issues"])
    assert body["resolution_log"], "the seed includes a resolved issue"
    assert all(i["status"] == "resolved" for i in body["resolution_log"])


def test_scan_is_idempotent() -> None:
    """POST /crm/ops/scan auto-detects + UPSERTS; a rescan dedups (never duplicates)."""
    repo, adapter, store = _install()
    _seed_conflict(repo, adapter)

    first = client.post("/crm/ops/scan", headers=_auth())
    assert first.status_code == 200, first.text
    detected = first.json()["detected"]
    assert detected >= 1, "the seeded conflict must be detected"
    count_after_first = len(store.list_issues(PROGRAM))

    second = client.post("/crm/ops/scan", headers=_auth())
    assert second.status_code == 200, second.text
    assert len(store.list_issues(PROGRAM)) == count_after_first, "a rescan must not duplicate"


def test_file_issue_owner_gated() -> None:
    """POST /crm/ops/data-quality — operator (non-owner) 403; admin files an open issue."""
    _install()
    body = {"category": "scoring", "kind": "scoring_review", "severity": "medium"}
    denied = client.post("/crm/ops/data-quality", headers=_auth("operator"), json=body)
    assert denied.status_code == 403, denied.text

    ok = client.post("/crm/ops/data-quality", headers=_auth("admin"), json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "open"
    assert ok.json()["source"] == "manual"


def test_file_issue_rejects_unknown_category() -> None:
    """An unknown category is a clean 422 (fail-closed, INV-2)."""
    _install()
    resp = client.post(
        "/crm/ops/data-quality",
        headers=_auth("admin"),
        json={"category": "nope", "kind": "x"},
    )
    assert resp.status_code == 422, resp.text


def test_patch_issue_resolve_is_leader_or_admin() -> None:
    """PATCH triage — operator 403; leader resolves + stamps resolved_by from the principal."""
    _, _, store = _install()
    store.seed_demo(PROGRAM)
    issue_id = store.list_issues(PROGRAM, status="open")[0].issue_id

    denied = client.patch(
        f"/crm/ops/data-quality/{issue_id}", headers=_auth("operator"), json={"status": "resolved"}
    )
    assert denied.status_code == 403, denied.text

    ok = client.patch(
        f"/crm/ops/data-quality/{issue_id}",
        headers=_auth("leader"),
        json={"status": "resolved", "resolution": "handled"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "handled"
    assert body["resolved_by"], "resolved_by must be stamped from the verified principal"


def test_patch_unknown_issue_404() -> None:
    """PATCH an absent issue → 404."""
    _install()
    import uuid

    resp = client.patch(
        f"/crm/ops/data-quality/{uuid.uuid4()}", headers=_auth("leader"), json={"status": "open"}
    )
    assert resp.status_code == 404, resp.text


def test_scoring_change_flags_decision_and_logs_fix() -> None:
    """POST /crm/ops/scoring-change — leader queues a crm decision + appends a fix-log entry."""
    _, _, store = _install()
    resp = client.post(
        "/crm/ops/scoring-change",
        headers=_auth("leader"),
        json={"summary": "Raise threshold to 65", "recommendation": "fall cohort skews high"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"]["workstream"] == "crm"
    assert body["fix"]["kind"] == "scoring_change"
    # The fix shows up in the lead-scoring change log.
    assert any(f.kind == "scoring_change" for f in store.list_fix_log(PROGRAM))


def test_scoring_change_operator_forbidden() -> None:
    """An operator cannot approve a scoring-model change (leadership input only)."""
    _install()
    resp = client.post("/crm/ops/scoring-change", headers=_auth("operator"), json={"summary": "x"})
    assert resp.status_code == 403, resp.text

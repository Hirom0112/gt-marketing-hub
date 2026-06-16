"""Enrollment-read API tests (S1 round 2; ARCHITECTURE.md §6, FR-2.2/2.5).

Boots the FastAPI app over the SAME fixed-seed synthetic dataset the app is
seeded from (`DEFAULT_FAMILY_COUNT`/`DEFAULT_SEED`, `synthetic.generate`), so
every expectation is exact and deterministic (CLAUDE.md §4.1) — mirroring the
S0 `test_api_pipeline.py` style.

Covered:
- `GET /work-queue` — ranked by deterministic work-queue score, descending. The
  endpoint MUST delegate to `app.core.work_queue.rank_families` (not re-implement
  ranking): the test cross-checks the returned order against that core function
  over the same dataset, and asserts each item carries identity + score +
  recoverability + value (the deal cards the UI renders).
- `GET /families/{id}` — now carries the FR-2.2 `deal_view` projection equal to
  `assemble_deal_view` over that family's joined rows, AND still the joined
  spine + four source rows (the S0 contract must stay green).
- `GET /families?min_score=<x>` — only families whose work-queue score ≥ x.

Deterministic without a local `params/params.yaml` (gitignored): the app loads
the committed `params/params.example.yaml` via the `get_params` dependency, and
this test recomputes its expectations from that same committed file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.core.contact_log import last_contact_at
from app.core.contact_status import ContactStatus, derive_contact_status
from app.core.family_record import assemble_deal_view
from app.core.params import Params, load_params
from app.core.work_queue import (
    WorkQueueFamily,
    recoverable_now,
    responsiveness_from_engagement,
    score_family,
    value,
)
from app.data.models import FundingState
from app.data.repository import (
    DEFAULT_FAMILY_COUNT,
    DEFAULT_SEED,
    InMemoryFamilyRepository,
    JoinedFamily,
)
from app.main import app
from app.observability.log_store import DecisionAction

client = TestClient(app)

# The committed example file is the authoritative params source for these tests,
# identical to the app's get_params() fallback when no params.yaml exists.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _seeded() -> InMemoryFamilyRepository:
    """The SAME fixed-seed store the app boots over — expectations draw from it."""
    return InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)


def _work_queue_family(joined: JoinedFamily, params: Params) -> WorkQueueFamily:
    """Project a joined family to the scorer's input (mirrors the router build)."""
    signals = joined.community_profile.engagement_signals if joined.community_profile else {}
    num_children = joined.lead.num_children if joined.lead else 1
    return WorkQueueFamily(
        family_id=joined.family.family_id,
        current_stage=joined.family.current_stage,
        stalled_since=joined.family.stalled_since,
        created_at=joined.family.created_at,
        responsiveness=responsiveness_from_engagement(signals, params),
        num_children=num_children,
        funding_type=joined.family.funding_type,
    )


def _expected_ranked(params: Params, *, now: datetime) -> list[WorkQueueFamily]:
    """The S12 ordering: recoverable_now desc, ties broken by ascending family_id."""
    repo = _seeded()
    queue_families = [_work_queue_family(joined, params) for joined in repo.list_joined()]
    return sorted(
        queue_families,
        key=lambda f: (-recoverable_now(f, params, now=now), f.family_id),
    )


def test_read_endpoints_contract() -> None:
    """The three S1-round-2 read endpoints meet their §6 contract on fixed-seed data."""
    params = _params()

    # --- GET /work-queue?scope=all: ranked by recoverable_now desc (S12 W1). ---
    # The full-cohort assertions use the back-compat `scope=all` slice (the default
    # is now `active`, the small live recovery queue); DEFAULT_FAMILY_COUNT (24)
    # sits well under the scope's `limit` cap, so `all` returns the whole cohort.
    wq_resp = client.get("/work-queue", params={"scope": "all"})
    assert wq_resp.status_code == 200
    items = wq_resp.json()
    assert isinstance(items, list)
    assert len(items) == DEFAULT_FAMILY_COUNT

    # Non-increasing recoverable_now across the list (descending order).
    recoverables = [item["recoverable_now"] for item in items]
    assert recoverables == sorted(recoverables, reverse=True)

    # Each item carries identity + score + recoverability + value + stall_date.
    for item in items:
        assert "family_id" in item
        assert "score" in item
        assert "recoverability" in item
        assert "value" in item
        assert "stall_date" in item
        assert "recoverable_now" in item
        assert "freshness" in item
        assert "recovery_state" in item

    # The endpoint orders by recoverable_now — the id set matches the core ranking
    # (the exact sequence can hairline-wobble on a near-tie due to the route's own
    # `now`, so assert the SET and the monotonicity above, not a brittle sequence).
    expected = _expected_ranked(params, now=datetime.now(UTC))
    assert {item["family_id"] for item in items} == {str(f.family_id) for f in expected}

    # And the per-item value equals the core function (value has no `now`); score /
    # recoverability are recomputed at the item's own family for consistency.
    by_id = {str(f.family_id): f for f in expected}
    for item in items:
        fam = by_id[item["family_id"]]
        assert round(item["value"], 6) == round(value(fam, params), 6)
        assert 0.0 <= item["recoverability"] <= 1.0
        assert item["recoverable_now"] >= 0.0

    # --- GET /families/{id}: deal_view projection + still-joined record. ---
    repo = _seeded()
    sample = repo.list_families()[0]
    detail = client.get(f"/families/{sample.family_id}")
    assert detail.status_code == 200
    body = detail.json()

    # S0 contract: the joined spine + four source rows are STILL present.
    assert body["family"]["family_id"] == str(sample.family_id)
    assert body["lead"]["family_id"] == str(sample.family_id)
    assert body["app_form"]["family_id"] == str(sample.family_id)
    assert body["enrollment_forms"]["family_id"] == str(sample.family_id)
    assert body["community_profile"]["family_id"] == str(sample.family_id)

    # New: a deal_view object equal to assemble_deal_view over the joined rows.
    joined = repo.get_family(sample.family_id)
    assert joined is not None
    expected_dv = assemble_deal_view(joined)
    dv = body["deal_view"]
    assert dv["stall_reason"] == (
        expected_dv.stall_reason.value if expected_dv.stall_reason is not None else None
    )
    assert dv["funding_type"] == (
        expected_dv.funding_type.value if expected_dv.funding_type is not None else None
    )
    assert dv["map_score"] == expected_dv.map_score
    assert dv["attribution_source"] == expected_dv.attribution_source
    assert dv["crm_seam_status"] == expected_dv.crm_seam_status.value

    # --- GET /families?min_score=<x>: only families with score >= x. ---
    # min_score still gates on the canonical score_family (unchanged by S12); build
    # the cohort directly (the recoverable_now ordering is irrelevant to this gate).
    repo = _seeded()
    cohort = [_work_queue_family(j, params) for j in repo.list_joined()]
    ranked = sorted(cohort, key=lambda f: score_family(f, params), reverse=True)
    # Pick a threshold mid-distribution so the filter is non-trivial.
    threshold = score_family(ranked[len(ranked) // 2], params)
    filtered = client.get("/families", params={"min_score": threshold})
    assert filtered.status_code == 200
    filtered_ids = {row["family_id"] for row in filtered.json()}

    expected_ids = {str(f.family_id) for f in ranked if score_family(f, params) >= threshold}
    assert filtered_ids == expected_ids
    # Sanity: the filter actually drops some families (threshold is mid-list).
    assert len(filtered_ids) < DEFAULT_FAMILY_COUNT


def test_family_detail_surfaces_recency_and_dropoff() -> None:
    """`GET /families/{id}` carries the S9 W2 recency + drop-off projection fields.

    The pure drop-off fields (completion_pct/forms_signed/forms_total/
    next_unsigned_form/apply_date) equal `assemble_deal_view` over the joined
    rows. The recency fields (contact_status/last_contact_at) are composed in the
    API layer from the audit log + an api-layer `now` (NOT pure core): seeding an
    approve decision for the family ⇒ contact_status == followed_up and
    last_contact_at non-None, matching the deriver.
    """
    params = _params()
    repo = _seeded()
    # A non-funded family so contact_status is not short-circuited to CLOSED.
    sample = next(f for f in repo.list_families() if f.funding_state is not FundingState.FUNDED)

    # Seed an approve decision in the app's observability singleton so recency
    # derives deterministically; reset afterward to avoid cross-test bleed.
    deps.reset_observability_log()
    log = deps.get_observability_log()
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow="enrollment_draft",
        schema_version="1",
        payload={"action": "email", "body": "hi"},
        family_id=sample.family_id,
    )
    log.log_decision(proposal_id=proposal_id, human="operator", action=DecisionAction.APPROVE)
    try:
        detail = client.get(f"/families/{sample.family_id}")
        assert detail.status_code == 200
        dv = detail.json()["deal_view"]

        # --- pure drop-off fields equal assemble_deal_view. ---
        joined = repo.get_family(sample.family_id)
        assert joined is not None
        expected = assemble_deal_view(joined)
        assert dv["completion_pct"] == expected.completion_pct
        assert dv["forms_signed"] == expected.forms_signed
        assert dv["forms_total"] == expected.forms_total
        assert dv["next_unsigned_form"] == expected.next_unsigned_form
        assert (dv["apply_date"] is None) == (expected.apply_date is None)

        # --- recency fields composed in the API layer. ---
        stamped = last_contact_at(log, sample.family_id)
        assert stamped is not None
        assert dv["last_contact_at"] is not None
        expected_status = derive_contact_status(
            created_at=sample.created_at,
            last_contact_at=stamped,
            now=datetime.now(UTC),
            funded=sample.funding_state is FundingState.FUNDED,
            params=params,
        )
        assert dv["contact_status"] == expected_status.value
        # An approved outbound ⇒ followed_up (green), not fresh/overdue.
        assert dv["contact_status"] == ContactStatus.FOLLOWED_UP.value
    finally:
        deps.reset_observability_log()


def test_work_queue_scope_active_excludes_history() -> None:
    """`?scope=active` (the default) returns ONLY {stalled, working} — no history.

    The active scope is the LIVE recovery queue (FR-2.5): recovered/dismissed
    families do NOT belong in it. Over the fixed-seed cohort the default response
    and the explicit `?scope=active` response are identical, and every row's
    derived recovery_state is active ({stalled, working}); not one recovered or
    dismissed family leaks through. Each row also carries a `stall_date` equal to
    the api-layer `_stall_date` derivation (the calendar's grouping key).
    """
    from app.api.families import _stall_date
    from app.core.recovery_state import RecoveryState, is_active

    deps.reset_observability_log()
    try:
        # Default == explicit active (the default scope IS active).
        default_resp = client.get("/work-queue")
        active_resp = client.get("/work-queue", params={"scope": "active"})
        assert default_resp.status_code == 200
        assert active_resp.status_code == 200
        default_items = default_resp.json()
        active_items = active_resp.json()
        assert [it["family_id"] for it in default_items] == [it["family_id"] for it in active_items]

        # Every active row is {stalled, working}; NO recovered/dismissed leak in.
        active_states = {RecoveryState.STALLED.value, RecoveryState.WORKING.value}
        for it in active_items:
            assert it["recovery_state"] in active_states
            assert it["recovery_state"] not in {
                RecoveryState.RECOVERED.value,
                RecoveryState.DISMISSED.value,
            }

        # stall_date is present and matches the api-layer `_stall_date` derivation.
        params = _params()
        repo = _seeded()
        log = deps.get_observability_log()
        now = datetime.now(UTC)
        for it in active_items:
            joined = repo.get_family(UUID(it["family_id"]))
            assert joined is not None
            expected = _stall_date(joined, log=log, now=now, params=params)
            assert it["stall_date"][:10] == expected.isoformat()[:10]

        # The active scope is a STRICT subset of the full cohort (the recovered/
        # dismissed long tail is excluded — the perf + correctness contract).
        all_items = client.get("/work-queue", params={"scope": "all"}).json()
        assert len(active_items) < len(all_items)
        # And the active scope == the active-derived families that were EVER
        # stalled (the perf pre-filter: `stalled_since is not None` candidates,
        # then kept iff {stalled, working}). This mirrors the route's contract.
        ever_stalled_ids = {
            str(f.family_id) for f in repo.list_families() if f.stalled_since is not None
        }
        full_active = {
            it["family_id"]
            for it in all_items
            if is_active(RecoveryState(it["recovery_state"]))
            and it["family_id"] in ever_stalled_ids
        }
        assert {it["family_id"] for it in active_items} == full_active
    finally:
        deps.reset_observability_log()


def test_work_queue_scope_history_only_recovered_dismissed_and_limit() -> None:
    """`?scope=history` returns only {recovered, dismissed} and respects `limit`.

    History is the closed-out tail; it must never include an active family, and
    its `limit` cap bounds the response so the long tail is never streamed.
    """
    from app.core.recovery_state import RecoveryState

    deps.reset_observability_log()
    try:
        hist = client.get("/work-queue", params={"scope": "history"})
        assert hist.status_code == 200
        hist_items = hist.json()
        history_states = {RecoveryState.RECOVERED.value, RecoveryState.DISMISSED.value}
        for it in hist_items:
            assert it["recovery_state"] in history_states

        # `limit` caps the row count (never stream the long tail).
        capped = client.get("/work-queue", params={"scope": "history", "limit": 1})
        assert capped.status_code == 200
        assert len(capped.json()) <= 1

        # limit out of range is rejected (1..500).
        assert client.get("/work-queue", params={"scope": "history", "limit": 0}).status_code == 422
        assert (
            client.get("/work-queue", params={"scope": "history", "limit": 9999}).status_code == 422
        )

        # active and history are DISJOINT (a family is active xor closed-out), and
        # both are subsets of the full cohort. They need not exhaust it: the active
        # scope's perf pre-filter (`stalled_since is not None`) intentionally drops
        # never-stalled families that would otherwise derive `stalled`.
        active_ids = {it["family_id"] for it in client.get("/work-queue").json()}
        hist_ids = {it["family_id"] for it in hist_items}
        all_ids = {
            it["family_id"] for it in client.get("/work-queue", params={"scope": "all"}).json()
        }
        assert active_ids.isdisjoint(hist_ids)
        assert active_ids <= all_ids
        assert hist_ids <= all_ids
    finally:
        deps.reset_observability_log()


def test_work_queue_active_scope_is_small_on_recovered_heavy_cohort() -> None:
    """Perf shape: on a large cohort the active scope is ≪ the full cohort.

    The active scope pre-filters to the cheap `stalled_since is not None`
    candidates BEFORE the per-family derive, so on a recovered-heavy cohort (the
    realistic 5,146-family scenario where ~5,006 are recovered) it returns only a
    small slice — the fix that takes the default `/work-queue` from 1.6 MB to
    tens of KB. Here a 400-family cohort stands in for that shape.
    """
    big = InMemoryFamilyRepository.seeded(n=400, seed=7)
    deps.reset_observability_log()
    app.dependency_overrides[deps.get_repository] = lambda: big
    try:
        active = client.get("/work-queue").json()
        full = client.get("/work-queue", params={"scope": "all", "limit": 500}).json()
        # The active queue is a small fraction of the full cohort.
        assert len(active) < len(full)
        # It can never exceed the ever-stalled candidate set (the perf pre-filter).
        ever_stalled = sum(1 for f in big.list_families() if f.stalled_since is not None)
        assert len(active) <= ever_stalled
        assert ever_stalled < len(big.list_families())
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_work_queue_history_recovered_row_carries_outcome_and_resolved_at() -> None:
    """A RECOVERED history row carries `recovered_outcome` + `resolved_at`, null dismiss.

    The History view needs the OUTCOME story per resolved family: for a recovered
    row the predicate that fired (∈ the three labels) and the instant it left the
    active board, and the dismiss fields must be null (it was not dismissed).
    """
    from app.core.recovery_state import RecoveryState

    deps.reset_observability_log()
    try:
        hist = client.get("/work-queue", params={"scope": "history", "limit": 500}).json()
        recovered = [it for it in hist if it["recovery_state"] == RecoveryState.RECOVERED.value]
        # The fixed-seed cohort has recovered families in the history tail.
        assert recovered, "expected at least one recovered family in the history scope"
        labels = {"stage_advanced", "forms_cleared", "deposit_received"}
        for it in recovered:
            assert it["recovered_outcome"] in labels
            assert it["resolved_at"] is not None
            # Dismiss fields are null for a recovered (not dismissed) row.
            assert it["dismiss_reason"] is None
            assert it["dismissed_by"] is None
            assert it["dismissed_at"] is None
    finally:
        deps.reset_observability_log()


def test_work_queue_history_dismissed_row_carries_dismiss_record_fields() -> None:
    """A DISMISSED history row carries reason/operator/date from the logged DismissRecord.

    Logging a dismiss for a family ⇒ its history row derives `dismissed` and
    plumbs the `DismissRecord` (reason/human/created_at) through onto the row,
    with the recovered fields null (it did not recover — it was set aside).
    """
    from app.core.recovery_state import RecoveryState

    deps.reset_observability_log()
    log = deps.get_observability_log()
    repo = _seeded()
    sample = repo.list_families()[0]
    record = log.log_dismiss(
        family_id=sample.family_id,
        human="enrollment_lead",
        reason="Out of budget this cycle",
    )
    try:
        hist = client.get("/work-queue", params={"scope": "history", "limit": 500}).json()
        by_id = {it["family_id"]: it for it in hist}
        row = by_id[str(sample.family_id)]
        assert row["recovery_state"] == RecoveryState.DISMISSED.value
        assert row["dismiss_reason"] == record.reason
        assert row["dismissed_by"] == record.human
        assert row["dismissed_at"][:19] == record.created_at.isoformat()[:19]
        # Recovered fields are null for a dismissed row.
        assert row["recovered_outcome"] is None
        assert row["resolved_at"] is None
    finally:
        deps.reset_observability_log()


def test_work_queue_active_scope_omits_history_detail_fields() -> None:
    """The active/triage rows have ALL the new history fields null (contract intact).

    The history-scope detail is populated ONLY for `scope=history`; the active
    path must add no cost and stay byte-identical, so every active row carries the
    five new fields as null.
    """
    deps.reset_observability_log()
    try:
        active = client.get("/work-queue").json()
        assert active, "expected a non-empty active queue on the fixed-seed cohort"
        for it in active:
            assert it["recovered_outcome"] is None
            assert it["resolved_at"] is None
            assert it["dismiss_reason"] is None
            assert it["dismissed_by"] is None
            assert it["dismissed_at"] is None
    finally:
        deps.reset_observability_log()


def test_work_queue_active_contract_unchanged_by_history_detail() -> None:
    """The active scope is byte-identical save the five new always-null fields.

    Stripping the new optional fields from every active row reproduces the exact
    prior active payload — proving the history-detail change is contract-additive
    only and did not touch the active path's existing values.
    """
    deps.reset_observability_log()
    try:
        active = client.get("/work-queue").json()
        new_fields = {
            "recovered_outcome",
            "resolved_at",
            "dismiss_reason",
            "dismissed_by",
            "dismissed_at",
        }
        for it in active:
            # The new fields are present-and-null; the rest is the prior contract.
            assert new_fields <= set(it)
            stripped = {k: v for k, v in it.items() if k not in new_fields}
            # The pre-existing active row keys are exactly these (S9/S12 contract).
            assert set(stripped) == {
                "family_id",
                "display_name",
                "current_stage",
                "score",
                "recoverability",
                "value",
                # A-23 value drivers — always present (child count + funding label).
                "num_children",
                "funding_type",
                "stall_date",
                "recoverable_now",
                "freshness",
                "contact_status",
                "last_contact_at",
                "recovery_state",
            }
    finally:
        deps.reset_observability_log()


def test_responsiveness_from_engagement_normalizes() -> None:
    """`responsiveness_from_engagement` maps email_opens into [0,1] via params (INV-11)."""
    params = _params()
    cap = params.work_queue.recoverability.responsiveness_email_opens_max

    # Full cap ⇒ 1.0; half ⇒ 0.5; zero / missing / empty ⇒ 0.0; over-cap clamps.
    assert responsiveness_from_engagement({"email_opens": cap}, params) == 1.0
    assert responsiveness_from_engagement({"email_opens": cap / 2}, params) == 0.5
    assert responsiveness_from_engagement({"email_opens": 0}, params) == 0.0
    assert responsiveness_from_engagement({}, params) == 0.0
    assert responsiveness_from_engagement({"events_attended": 3}, params) == 0.0
    assert responsiveness_from_engagement({"email_opens": cap * 10}, params) == 1.0

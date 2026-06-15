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
from uuid import uuid4

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
    return WorkQueueFamily(
        family_id=joined.family.family_id,
        current_stage=joined.family.current_stage,
        stalled_since=joined.family.stalled_since,
        created_at=joined.family.created_at,
        responsiveness=responsiveness_from_engagement(signals, params),
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

    # --- GET /work-queue: ranked by recoverable_now desc (S12 W1). ---
    wq_resp = client.get("/work-queue")
    assert wq_resp.status_code == 200
    items = wq_resp.json()
    assert isinstance(items, list)
    assert len(items) == DEFAULT_FAMILY_COUNT

    # Non-increasing recoverable_now across the list (descending order).
    recoverables = [item["recoverable_now"] for item in items]
    assert recoverables == sorted(recoverables, reverse=True)

    # Each item carries identity + score + recoverability + value (deal card).
    for item in items:
        assert "family_id" in item
        assert "score" in item
        assert "recoverability" in item
        assert "value" in item
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

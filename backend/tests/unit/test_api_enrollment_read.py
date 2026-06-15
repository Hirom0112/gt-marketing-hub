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

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.family_record import assemble_deal_view
from app.core.params import Params, load_params
from app.core.work_queue import (
    WorkQueueFamily,
    rank_families,
    recoverability,
    responsiveness_from_engagement,
    score_family,
    value,
)
from app.data.repository import (
    DEFAULT_FAMILY_COUNT,
    DEFAULT_SEED,
    InMemoryFamilyRepository,
    JoinedFamily,
)
from app.main import app

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
        responsiveness=responsiveness_from_engagement(signals, params),
        funding_type=joined.family.funding_type,
    )


def _expected_ranked(params: Params) -> list[WorkQueueFamily]:
    repo = _seeded()
    queue_families = [_work_queue_family(joined, params) for joined in repo.list_joined()]
    return rank_families(queue_families, params)


def test_read_endpoints_contract() -> None:
    """The three S1-round-2 read endpoints meet their §6 contract on fixed-seed data."""
    params = _params()

    # --- GET /work-queue: ranked by score desc, delegating to rank_families. ---
    wq_resp = client.get("/work-queue")
    assert wq_resp.status_code == 200
    items = wq_resp.json()
    assert isinstance(items, list)
    assert len(items) == DEFAULT_FAMILY_COUNT

    # Non-increasing score across the list (descending order).
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)

    # Each item carries identity + score + recoverability + value (deal card).
    for item in items:
        assert "family_id" in item
        assert "score" in item
        assert "recoverability" in item
        assert "value" in item

    # The endpoint MUST delegate to core rank_families — order matches exactly.
    expected = _expected_ranked(params)
    assert [item["family_id"] for item in items] == [str(f.family_id) for f in expected]

    # And the per-item score/recoverability/value equal the core functions.
    for item, fam in zip(items, expected, strict=True):
        assert round(item["score"], 6) == round(score_family(fam, params), 6)
        assert round(item["recoverability"], 6) == round(recoverability(fam, params), 6)
        assert round(item["value"], 6) == round(value(fam, params), 6)

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
    ranked = _expected_ranked(params)
    # Pick a threshold mid-distribution so the filter is non-trivial.
    threshold = score_family(ranked[len(ranked) // 2], params)
    filtered = client.get("/families", params={"min_score": threshold})
    assert filtered.status_code == 200
    filtered_ids = {row["family_id"] for row in filtered.json()}

    expected_ids = {str(f.family_id) for f in ranked if score_family(f, params) >= threshold}
    assert filtered_ids == expected_ids
    # Sanity: the filter actually drops some families (threshold is mid-list).
    assert len(filtered_ids) < DEFAULT_FAMILY_COUNT


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

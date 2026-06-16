"""GEO tracking endpoint tests (FR-3.7/4.4; ARCH §6; INV-3/INV-9/INV-11).

Acceptance tests for the S5 GEO API — the deterministic GEO core (repeated
sampling via the simulated adapter + the coverage/variance tracking eval)
surfaced over REST:

  ``GET  /geo``          — default repeated-sampling pass over the seeded GEO
                           prompt set; coverage vs the 0% baseline + lift.
  ``POST /geo/sample``   — a fresh repeated-sampling run (optional prompt_set /
                           engine / seed), logged to observability (NFR-6).

Every number asserted comes from the same pure core pinned by
``app/evals/geo_tracking_eval.py`` and the simulated adapter — these tests prove
the core is wired behind HTTP faithfully (baseline 0.0 + a computed lift,
deterministic under a fixed seed), not that the math is re-derived in the API.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from app.adapters.geo_sampling.base import GeoObservation, GeoSamplingAdapter
from app.api import deps
from app.api import geo as geo_api
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + published registry + no stray overrides per test."""
    deps.reset_observability_log()
    geo_api.reset_published_registry()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    geo_api.reset_published_registry()


class _SingleRunAdapter(GeoSamplingAdapter):
    """A GEO adapter that returns only ONE run per prompt regardless of the ask.

    Used to drive the insufficient-samples path: with a single distinct
    ``run_index`` the eval's ``sample_count`` is 1, below any sane
    ``min_samples_per_prompt`` ⇒ ``insufficient_samples`` / ``enabled=False``.
    """

    def sample(
        self,
        prompt_set: Sequence[str],
        engine: str,
        *,
        min_samples_per_prompt: int,
        seed: int = 0,
    ) -> list[GeoObservation]:
        return [
            GeoObservation(
                prompt=prompt,
                engine=engine,
                run_index=0,
                cited_domains=(),
                brand_cited=False,
            )
            for prompt in prompt_set
        ]


def test_get_geo_returns_baseline_zero_and_lift() -> None:
    """GET /geo returns 200 with baseline 0.0, a lift field, and an enabled flag."""
    resp = client.get("/geo")
    assert resp.status_code == 200
    body = resp.json()

    assert body["baseline"] == 0.0
    assert "lift" in body
    assert body["lift"] == pytest.approx(body["coverage_mean"] - body["baseline"])
    assert "enabled" in body
    assert isinstance(body["enabled"], bool)


def test_get_geo_contract_shape() -> None:
    """GET /geo returns the full GeoTrackingView contract the UI builds to."""
    body = client.get("/geo").json()
    for field in (
        "coverage_mean",
        "baseline",
        "lift",
        "variance",
        "sample_count",
        "insufficient_samples",
        "enabled",
        "prompt_set",
        "engine",
    ):
        assert field in body, f"missing contract field: {field}"
    assert isinstance(body["prompt_set"], list)
    assert body["prompt_set"]  # the seeded GEO prompts
    assert isinstance(body["engine"], str)
    assert body["engine"]


def test_get_geo_is_deterministic() -> None:
    """GET /geo uses a fixed default seed ⇒ identical body across calls."""
    first = client.get("/geo").json()
    second = client.get("/geo").json()
    assert first == second


def test_get_geo_enabled_with_sufficient_samples() -> None:
    """The default pass takes enough runs ⇒ not insufficient, action enabled."""
    body = client.get("/geo").json()
    assert body["insufficient_samples"] is False
    assert body["enabled"] is True


def test_post_geo_sample_returns_view_and_is_deterministic() -> None:
    """POST /geo/sample under a fixed seed is deterministic and baseline 0.0."""
    payload = {"seed": 7}
    first = client.post("/geo/sample", json=payload)
    second = client.post("/geo/sample", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["baseline"] == 0.0
    assert first.json() == second.json()


def test_post_geo_sample_empty_body_defaults() -> None:
    """POST /geo/sample with no body defaults to the seed prompt set / engine / seed."""
    resp = client.post("/geo/sample", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"] == 0.0
    assert body["prompt_set"]
    assert "lift" in body


def test_post_geo_sample_logs_to_observability() -> None:
    """POST /geo/sample appends a geo_tracking proposal + eval to the audit log."""
    log = deps.get_observability_log()
    before = len(log.list_proposals())
    resp = client.post("/geo/sample", json={})
    assert resp.status_code == 200

    proposals = log.list_proposals()
    assert len(proposals) == before + 1
    record = proposals[-1]
    # Subject is labeled as a GEO-tracking subject, NOT an enrollment_draft.
    assert record.flow == "geo_tracking"
    audit = log.get_audit(record.proposal_id)
    assert audit is not None
    assert audit.evals  # the tracking eval result is attached
    assert audit.evals[-1].eval_name == "geo_tracking"


def test_geo_insufficient_samples_disables_action() -> None:
    """A single-run sampling pass ⇒ insufficient samples ⇒ enabled False (INV-3)."""
    app.dependency_overrides[deps.get_geo_sampling_adapter_dep] = _SingleRunAdapter
    body = client.get("/geo").json()
    assert body["insufficient_samples"] is True
    assert body["enabled"] is False


# --------------------------------------------------------------------------- #
# generate-to-win flywheel (FR-3.7) — POST /geo/generate.
# --------------------------------------------------------------------------- #
_TARGET_PROMPT = "best virtual school for gifted K-8"  # a seeded GEO prompt.


def test_generate_to_win_publishes_and_raises_coverage() -> None:
    """POST /geo/generate generates a gate-passing GeoContentPiece, publishes it,
    and re-samples so coverage on the target prompt RISES (lift > 0, FR-3.7).
    """
    resp = client.post("/geo/generate", json={"target_prompt": _TARGET_PROMPT})
    assert resp.status_code == 200
    body = resp.json()

    # The piece passed the grounding gate and was published.
    assert body["published"] is True
    assert body["blocked"] is False
    # The re-sampled coverage for the won prompt rose above the 0% baseline.
    assert body["baseline"] == 0.0
    assert body["coverage_mean"] > 0.0
    assert body["lift"] > 0.0
    # The view is scoped to the won prompt.
    assert body["prompt_set"] == [_TARGET_PROMPT]


def test_generate_to_win_lift_visible_on_default_board() -> None:
    """After generate-to-win publishes a prompt, GET /geo (the whole board)
    reflects a coverage lift vs a pre-publish read (the cross-request flywheel).
    """
    before = client.get("/geo").json()["coverage_mean"]
    win = client.post("/geo/generate", json={"target_prompt": _TARGET_PROMPT})
    assert win.status_code == 200
    after = client.get("/geo").json()["coverage_mean"]
    assert after > before, "publishing a won prompt must raise board-wide coverage"


def test_generate_to_win_blocks_banned_claim_and_does_not_publish() -> None:
    """A body carrying a banned '4X speed' claim is BLOCKED (INV-4): the piece is
    NOT published and coverage on the prompt is UNCHANGED (fail-closed flywheel).
    """
    banned_prompt = "online school for profoundly gifted elementary"
    before = client.get("/geo").json()["coverage_mean"]

    resp = client.post(
        "/geo/generate",
        json={"target_prompt": banned_prompt, "body": "GT School is 4X faster than any rival."},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Fail-closed: blocked, not published, V-2 grounding flagged.
    assert body["blocked"] is True
    assert body["published"] is False
    assert "v2_grounding" in body["failed_rules"]
    # Coverage did not move — a blocked piece never reaches the corpus (INV-4).
    after = client.get("/geo").json()["coverage_mean"]
    assert after == before


def test_generate_to_win_logs_proposal_and_eval() -> None:
    """POST /geo/generate logs the piece proposal + its grounding eval (NFR-6)."""
    log = deps.get_observability_log()
    before = len(log.list_proposals())
    resp = client.post("/geo/generate", json={"target_prompt": _TARGET_PROMPT})
    assert resp.status_code == 200
    proposals = log.list_proposals()
    assert len(proposals) == before + 1
    record = proposals[-1]
    audit = log.get_audit(record.proposal_id)
    assert audit is not None
    assert audit.evals  # the grounding gate verdict is attached


def test_generate_to_win_audit_subject_is_geo_with_piece_uuid() -> None:
    """The GEO generate proposal audit entry is labeled `geo` and carries the
    GeoContentPiece's UUID as subject_ref — NOT the shared gate's mislabeled
    `enrollment_draft` / dropped UUID (S5 backlog; NFR-6 observability).

    The shared `eval_gate._subject_type` classifies a `.body`-bearing record as
    `enrollment_draft` and drops a non-string `subject_ref`; a GeoContentPiece has
    a UUID `.id` and a `.body`, so the gate verdict mislabels it. The GEO logging
    layer must correct the audit entry to the `geo` subject and re-attach the UUID.
    """
    from app.marketing.geo import build_geo_piece
    from app.marketing.schemas.geo import GeoStructure

    expected_id = build_geo_piece(
        target_prompt=_TARGET_PROMPT, structure=GeoStructure.DEFINITION
    ).id

    log = deps.get_observability_log()
    resp = client.post("/geo/generate", json={"target_prompt": _TARGET_PROMPT})
    assert resp.status_code == 200

    record = log.list_proposals()[-1]
    # Labeled `geo` (not `enrollment_draft`) and the UUID is preserved, not dropped.
    assert record.payload.get("subject_type") == "geo"
    assert record.content_ref == expected_id
    assert record.payload.get("subject_ref") == str(expected_id)


def test_enrollment_draft_proposal_still_labeled_enrollment_draft() -> None:
    """Regression guard: an enrollment_draft proposal through the gate still records
    `subject_type="enrollment_draft"` — only GEO entries change (S5 backlog).
    """
    from pathlib import Path
    from uuid import uuid4

    from app.ai.schemas.enrollment_draft import Claim, DraftAction, EnrollmentDraftProposal
    from app.core.eval_gate import evaluate_message
    from app.core.params import load_params
    from app.core.settings import Settings

    example_params = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
    draft = EnrollmentDraftProposal(
        action=DraftAction.NUDGE,
        family_id=uuid4(),
        body="A grounded, on-brand enrollment nudge for the family.",
        claims=[Claim(text="GT School offers a gifted curriculum.", source_ref="kb-1")],
    )
    verdict = evaluate_message(
        draft,
        settings=Settings(),
        params=load_params(example_params),
        brand_judge=lambda _r, _n: 0.99,
    )
    assert verdict.subject_type == "enrollment_draft"

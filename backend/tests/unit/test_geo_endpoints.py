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
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + no stray dependency overrides per test."""
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


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

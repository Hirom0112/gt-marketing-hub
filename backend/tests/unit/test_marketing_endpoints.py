"""S6 marketing-breadth endpoint tests (FR-3.6/3.8/3.10/3.11/3.12; ARCH §6).

Acceptance tests for the S6 marketing-breadth REST surface — the deterministic
marketing core (creator scoring, sentiment aggregation, KPI rollup, the
simulated dispatch gate, the staged-pipeline guard, Tom-Babb recipes) surfaced
behind HTTP. Every assertion ties back to the same pure core / params the API
orchestrates, proving the wiring is faithful (not that the math is re-derived in
the router):

  ``GET  /creators``         — surfaced/filtered by the params surface_threshold,
                               sorted fit desc then id; aggregate-only, no minor.
  ``GET  /sentiment``        — aggregate summary (source_mode placeholder) + records.
  ``GET  /kpi``              — per-channel rollup; baseline/target from params.
  ``GET  /content/schedule`` — the simulated post queue.
  ``POST /content/schedule`` — build + gate + simulate-send; fail-closed (blocked
                               vs simulated_sent), NEVER live (INV-9).
  ``GET  /content/pipeline`` — the seeded concept→image→video artifacts.
  ``POST /content/pipeline/advance`` — the §4 cheapest-first guard; fail-closed (422).
  ``GET  /recipes``          — every recipe attributes Tom Babb (INV-7).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.params import Params
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


def _params() -> Params:
    """The active params (surface_threshold / kpi levers / dispatch_mode)."""
    return deps.get_params()


# --------------------------------------------------------------------------- #
# GET /creators
# --------------------------------------------------------------------------- #


def test_get_creators_contract_shape() -> None:
    """GET /creators returns the locked CreatorOut contract the UI builds to."""
    resp = client.get("/creators")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body  # the seeded creators surface
    for creator in body:
        for field in (
            "id",
            "display_handle",
            "channel",
            "audience_segment",
            "fit_score",
            "authenticity_score",
            "rationale",
            "data_mode",
            "is_minor",
        ):
            assert field in creator, f"missing contract field: {field}"


def test_get_creators_filtered_by_surface_threshold() -> None:
    """Only creators with fit_score >= params.surface_threshold are surfaced."""
    threshold = _params().creator_scoring.surface_threshold
    body = client.get("/creators").json()
    assert body
    for creator in body:
        assert creator["fit_score"] >= threshold


def test_get_creators_sorted_fit_desc() -> None:
    """Surfaced creators are sorted by fit_score descending (then id)."""
    body = client.get("/creators").json()
    fits = [creator["fit_score"] for creator in body]
    assert fits == sorted(fits, reverse=True)


def test_get_creators_aggregate_only_no_minor() -> None:
    """Every surfaced creator is aggregate/synthetic and never a minor (INV-6)."""
    body = client.get("/creators").json()
    for creator in body:
        assert creator["is_minor"] is False
        assert creator["data_mode"] in ("synthetic", "aggregate")


# --------------------------------------------------------------------------- #
# GET /sentiment
# --------------------------------------------------------------------------- #


def test_get_sentiment_contract_shape() -> None:
    """GET /sentiment returns {summary, records} with the locked summary fields."""
    resp = client.get("/sentiment")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "records" in body
    summary = body["summary"]
    for field in ("positive", "neutral", "negative", "total", "source_mode"):
        assert field in summary, f"missing summary field: {field}"
    assert isinstance(body["records"], list)
    assert body["records"]


def test_get_sentiment_source_mode_placeholder() -> None:
    """The sentiment summary is aggregate-only and source_mode == placeholder."""
    summary = client.get("/sentiment").json()["summary"]
    assert summary["source_mode"] == "placeholder"
    assert summary["total"] == summary["positive"] + summary["neutral"] + summary["negative"]


# --------------------------------------------------------------------------- #
# GET /kpi
# --------------------------------------------------------------------------- #


def test_get_kpi_contract_shape() -> None:
    """GET /kpi returns the ChannelKpi rollup list with the locked fields."""
    resp = client.get("/kpi")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body
    for kpi in body:
        for field in (
            "channel",
            "metric",
            "baseline",
            "target",
            "lever_delta",
            "target_gap",
            "target_met",
        ):
            assert field in kpi, f"missing kpi field: {field}"


def test_get_kpi_baselines_from_params() -> None:
    """Each channel's baseline/target/lever_delta is derived from params levers."""
    levers = _params().kpi.levers
    body = client.get("/kpi").json()
    by_channel = {kpi["channel"]: kpi for kpi in body}
    assert set(by_channel) == set(levers)
    for channel, lever in levers.items():
        kpi = by_channel[channel]
        assert kpi["baseline"] == lever.baseline
        assert kpi["target"] == lever.target
        # lever_delta = metric - baseline (INV-11 — params-derived, no magic number).
        assert kpi["lever_delta"] == pytest.approx(kpi["metric"] - lever.baseline)
        assert kpi["target_gap"] == pytest.approx(lever.target - kpi["metric"])
        assert kpi["target_met"] == (kpi["metric"] >= lever.target)


# --------------------------------------------------------------------------- #
# GET /content/schedule
# --------------------------------------------------------------------------- #


def test_get_content_schedule_returns_list() -> None:
    """GET /content/schedule returns the (deterministic) simulated post queue."""
    resp = client.get("/content/schedule")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# --------------------------------------------------------------------------- #
# POST /content/schedule
# --------------------------------------------------------------------------- #


def _schedule_body(*, decision: str, passed: bool) -> dict[str, object]:
    """A POST /content/schedule body with the given approval + validation state."""
    return {
        "channel": "instagram",
        "scheduled_for": "2026-07-01T09:00:00Z",
        "approval": {"decision": decision},
        "validation": {"passed": passed},
    }


def test_post_schedule_approved_passing_simulated_sent() -> None:
    """A passing validation + approve ⇒ dispatch_status simulated_sent (simulated)."""
    resp = client.post("/content/schedule", json=_schedule_body(decision="approve", passed=True))
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatch_status"] == "simulated_sent"
    # ALWAYS simulated dispatch — never live (INV-9).
    assert body["dispatch_mode"] == "simulated"
    assert body["simulated_result"]


def test_post_schedule_failing_validation_blocked() -> None:
    """A failing validation ⇒ dispatch_status blocked, 200 (fail-closed, not 500)."""
    resp = client.post("/content/schedule", json=_schedule_body(decision="approve", passed=False))
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatch_status"] == "blocked"
    assert body["dispatch_mode"] == "simulated"
    assert body["simulated_result"] is None


def test_post_schedule_unapproved_blocked() -> None:
    """A non-approve decision ⇒ blocked even with a passing validation (INV-3)."""
    resp = client.post("/content/schedule", json=_schedule_body(decision="pending", passed=True))
    assert resp.status_code == 200
    assert resp.json()["dispatch_status"] == "blocked"


def test_post_schedule_logs_to_observability() -> None:
    """POST /content/schedule appends a schedule proposal to the audit log (NFR-6)."""
    log = deps.get_observability_log()
    before = len(log.list_proposals())
    resp = client.post("/content/schedule", json=_schedule_body(decision="approve", passed=True))
    assert resp.status_code == 200
    assert len(log.list_proposals()) == before + 1


def test_post_schedule_never_live() -> None:
    """A live dispatch_mode is never accepted/produced — the response is simulated."""
    resp = client.post("/content/schedule", json=_schedule_body(decision="approve", passed=True))
    assert resp.json()["dispatch_mode"] == "simulated"


# --------------------------------------------------------------------------- #
# GET /pipeline
# --------------------------------------------------------------------------- #


def test_get_pipeline_returns_concept_image_video() -> None:
    """GET /pipeline returns the seeded concept/image/video artifacts."""
    resp = client.get("/content/pipeline")
    assert resp.status_code == 200
    body = resp.json()
    for stage in ("concept", "image", "video"):
        assert stage in body, f"missing pipeline stage: {stage}"
    # image/video are PLACEHOLDER in v1 (OUT-1).
    assert body["image"]["status"] == "placeholder"
    assert body["video"]["status"] == "placeholder"
    assert body["image"]["placeholder_uri"]
    assert body["video"]["placeholder_uri"]


# --------------------------------------------------------------------------- #
# POST /pipeline/advance
# --------------------------------------------------------------------------- #


def test_post_pipeline_advance_selected_passing_unlocks() -> None:
    """A selected + passing concept advances to the next (image) stage."""
    resp = client.post(
        "/content/pipeline/advance",
        json={"stage": "concept", "status": "selected", "validation": {"passed": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["next_stage"] == "image"


def test_post_pipeline_advance_unselected_blocked() -> None:
    """An unselected concept cannot advance ⇒ 422 (fail-closed, INV-3)."""
    resp = client.post(
        "/content/pipeline/advance",
        json={"stage": "concept", "status": "generated", "validation": {"passed": True}},
    )
    assert resp.status_code == 422


def test_post_pipeline_advance_unvalidated_blocked() -> None:
    """A selected but failing-validation concept cannot advance ⇒ 422 (INV-3)."""
    resp = client.post(
        "/content/pipeline/advance",
        json={"stage": "concept", "status": "selected", "validation": {"passed": False}},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# GET /recipes
# --------------------------------------------------------------------------- #


def test_get_recipes_attributes_tom_babb() -> None:
    """Every recipe carries a non-empty attribution naming Tom Babb (INV-7)."""
    resp = client.get("/recipes")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body
    for recipe in body:
        assert recipe["attribution"]
        assert "Tom Babb" in recipe["attribution"]

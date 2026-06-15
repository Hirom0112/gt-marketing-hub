"""Eval-suite + leadership-scoreboard endpoint tests (FR-4.5/6.1; ARCH §6).

Acceptance tests for the S7 Wave 3 composition surface:

  * ``POST /evals/run`` runs the consolidated suite over deterministic, offline
    inputs (no live LLM — an injected on-brand judge + the simulated GEO adapter)
    and returns the 4-row green/red scoreboard; the all-green construction yields
    ``overall_green`` True with every action enabled, and the verdict persists so
    a follow-up ``GET /evals`` returns the same shape.
  * ``GET /evals`` with no suite run is fail-OPEN on "never run": empty rows,
    ``overall_green`` True, no disabled actions (the per-message gate still guards
    drafts; the suite-level kill only fires on an actual red row).
  * ``GET /scoreboard`` returns the cross-funnel leadership summary — the three
    sub-objects (enrollment / marketing / evals) over the current audit log.

These run fully offline (INV-9): the suite's grounding rows use the router's
injected deterministic judge and the GEO rows use the simulated adapter, so no
Anthropic key is needed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app

client = TestClient(app)

_EVAL_NAMES = {"nudge_trigger", "doc_extraction", "message_safety_grounding", "geo_tracking"}


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Reset the observability + eval-state singletons + overrides around each test."""
    deps.reset_observability_log()
    deps.reset_eval_state()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    deps.reset_eval_state()


def test_run_evals_returns_all_green_scoreboard() -> None:
    """POST /evals/run runs the suite green; GET /evals then returns the same shape."""
    resp = client.post("/evals/run")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["rows"]) == 4
    assert {row["eval_name"] for row in data["rows"]} == _EVAL_NAMES
    assert data["overall_green"] is True
    # Every row passed ⇒ no action disabled.
    assert all(disabled is False for disabled in data["disabled"].values())
    assert set(data["disabled"]) == _EVAL_NAMES

    # The verdict persists as the live kill state ⇒ GET /evals echoes the run.
    got = client.get("/evals")
    assert got.status_code == 200
    assert got.json() == data


def test_get_evals_no_suite_run_is_fail_open() -> None:
    """No suite has run ⇒ empty rows, overall green, no disabled actions (fail-OPEN)."""
    deps.reset_eval_state()
    resp = client.get("/evals")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
    assert data["overall_green"] is True
    assert data["disabled"] == {}


def test_get_scoreboard_returns_cross_funnel_summary() -> None:
    """GET /scoreboard returns the three cross-funnel sub-objects over the audit log."""
    resp = client.get("/scoreboard")
    assert resp.status_code == 200
    data = resp.json()

    assert "enrollment" in data
    assert "marketing" in data
    assert "evals" in data
    # Structure over the default (empty) log: the sub-objects exist and evals
    # carries a boolean overall_green (nothing failed ⇒ green).
    assert isinstance(data["evals"]["overall_green"], bool)
    assert data["enrollment"]["draft_proposals"] == 0
    assert data["marketing"]["geo_lift"] == 0.0

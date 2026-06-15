"""Eval-gated close-tips endpoint tests (S9 W5; FR-4.3; INV-2/INV-3/INV-4).

Acceptance tests for ``POST /ai/enrollment/close-tips`` end-to-end through the API:

  operator requests close tips → deterministic core assembles grounded context
  from app_form.extracted_fields → AI edge produces a schema-validated proposal →
  the EVAL GATE runs → the proposal + its eval are LOGGED before reaching a human →
  only on PASS (and a non-red suite row) does the proposal surface.

The LLM is never called live: tests OVERRIDE ``get_llm_client`` with a client whose
injected transport returns canned text, and ``get_brand_judge`` with a deterministic
judge. They prove INV-3 (no un-evaled action reaches a human), INV-4 (the gate
blocks, never softens), and the FR-4.5 suite-level kill (a RED ``close_tips`` row
disables the action in the LIVE path). All data is SYNTHETIC (INV-1).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.client import AnthropicLLMClient, LLMClient
from app.api import deps
from app.core.settings import Settings
from app.data.repository import InMemoryFamilyRepository
from app.evals.suite import EvalRow, EvalSuiteResult
from app.main import app

client = TestClient(app)


def _a_family_id() -> UUID:
    repo: InMemoryFamilyRepository = deps.get_repository()  # type: ignore[assignment]
    return repo.list_families()[0].family_id


def _settings_with_key() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def _fake_transport(text: str):
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def _tips_json(family_id: UUID, tips: list[dict[str, object]]) -> str:
    return json.dumps({"family_id": str(family_id), "tips": tips})


def _grounded_tips(family_id: UUID) -> str:
    return _tips_json(
        family_id,
        [{"text": "Offer to walk the parents through the enrollment steps.", "source_ref": None}],
    )


def _hallucinated_tips(family_id: UUID) -> str:
    # A "4X speed" performance multiplier — a hallucinated claim, banned by V-2.
    return _tips_json(
        family_id,
        [{"text": "Pitch that their child learns at 4X speed here.", "source_ref": None}],
    )


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    deps.reset_eval_state()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    deps.reset_eval_state()


# --------------------------------------------------------------------------- #
# 1. Close tips surface ONLY on pass; a blocked proposal is still logged.
# --------------------------------------------------------------------------- #
def test_close_tips_surfaces_only_passing_proposal() -> None:
    family_id = _a_family_id()

    # --- grounded tips ⇒ surfaced + logged ---
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _grounded_tips(family_id)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post("/ai/enrollment/close-tips", json={"family_id": str(family_id)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["surfaced"] is True
    assert data["degraded"] is False
    assert data["proposal"] is not None
    assert len(data["proposal"]["tips"]) >= 1
    proposal_id = data["proposal_id"]

    audit = client.get(f"/proposals/{proposal_id}").json()
    assert audit["proposal"]["proposal_id"] == proposal_id
    assert audit["evals"][0]["passed"] is True

    # --- hallucinated tips ⇒ blocked, surfaced False, STILL logged ---
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _hallucinated_tips(family_id)
    )
    blocked = client.post("/ai/enrollment/close-tips", json={"family_id": str(family_id)})
    bdata = blocked.json()
    assert bdata["surfaced"] is False
    assert "v2_grounding" in bdata["failed_rules"]
    assert bdata["proposal"] is None
    blocked_audit = client.get(f"/proposals/{bdata['proposal_id']}").json()
    assert blocked_audit["evals"][0]["passed"] is False


# --------------------------------------------------------------------------- #
# 2. No judge ⇒ even clean tips are V-4-denied (fail-closed at the API).
# --------------------------------------------------------------------------- #
def test_close_tips_blocked_without_judge() -> None:
    family_id = _a_family_id()
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _grounded_tips(family_id)
    )
    resp = client.post("/ai/enrollment/close-tips", json={"family_id": str(family_id)})
    data = resp.json()
    assert data["surfaced"] is False
    assert "v4_onbrand" in data["failed_rules"]
    assert data["proposal"] is None


# --------------------------------------------------------------------------- #
# 3. A RED close_tips suite row disables the action in the LIVE path (INV-3).
# --------------------------------------------------------------------------- #
def test_red_close_tips_suite_row_disables_action() -> None:
    """FR-4.5 suite-level kill: a RED ``close_tips`` row suppresses surfacing.

    Even with grounded tips + an on-brand judge (the per-message gate passes), a
    RED consolidated ``close_tips`` row disables the action: ``surfaced`` is False,
    ``eval_suite_red`` is in ``failed_rules``, and the proposal is still LOGGED
    (INV-4 audit side) — fail-closed in the live path, not merely the UI.
    """
    family_id = _a_family_id()
    red_state = EvalSuiteResult(
        rows=[EvalRow(eval_name="close_tips", score=0.2, threshold=0.95, passed=False)],
        overall_green=False,
    )
    app.dependency_overrides[deps.get_eval_state] = lambda: red_state
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _grounded_tips(family_id)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post("/ai/enrollment/close-tips", json={"family_id": str(family_id)})
    data = resp.json()
    assert data["surfaced"] is False
    assert "eval_suite_red" in data["failed_rules"]
    assert data["proposal"] is None
    # The proposal is STILL logged despite the kill (INV-4 audit side).
    assert client.get(f"/proposals/{data['proposal_id']}").status_code == 200


# --------------------------------------------------------------------------- #
# 4. Close tips for an unknown family ⇒ 404.
# --------------------------------------------------------------------------- #
def test_close_tips_unknown_family_404() -> None:
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _grounded_tips(uuid4())
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    resp = client.post("/ai/enrollment/close-tips", json={"family_id": str(uuid4())})
    assert resp.status_code == 404

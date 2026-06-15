"""A RED consolidated eval disables the AI action in the LIVE path (FR-4.5; INV-3).

This acceptance test proves the suite-level kill is fail-closed at the API
boundary, not merely in the UI: when the last consolidated
:class:`~app.evals.suite.EvalSuiteResult` carries the
``message_safety_grounding`` row RED, ``POST /ai/enrollment/draft`` surfaces NO
proposal (``surfaced=False`` / ``proposal is None`` / ``eval_suite_red`` in the
failing rules) even when the per-message V-1..V-4 gate would otherwise pass, and
``GET /evals`` reports that row red with ``disabled["message_safety_grounding"]``
True. An all-green suite re-enables surfacing.

The LLM is never called live: the draft path OVERRIDES ``get_llm_client`` with a
fake-transport client and ``get_brand_judge`` with a deterministic on-brand judge
(the same fixtures ``test_ai_endpoints.py`` uses). The suite-level kill state is
injected by overriding ``get_eval_state`` (the live seam), so no real suite run is
needed to drive the gate.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.schemas.enrollment_draft import DraftAction
from app.api import deps
from app.core.settings import Settings
from app.data.repository import InMemoryFamilyRepository
from app.evals.suite import EvalRow, EvalSuiteResult
from app.main import app

client = TestClient(app)

# The four consolidated eval names (suite.py constants), pinned here so the
# constructed EvalSuiteResult mirrors a real suite shape.
_NUDGE = "nudge_trigger"
_DOC = "doc_extraction"
_GROUNDING = "message_safety_grounding"
_GEO = "geo_tracking"


def _a_family_id() -> UUID:
    """A real seeded family id from the app's in-memory repository."""
    repo: InMemoryFamilyRepository = deps.get_repository()  # type: ignore[assignment]
    return repo.list_families()[0].family_id


def _settings_with_key() -> Settings:
    """A settings snapshot with a key ⇒ ``llm_available`` True (still no live call)."""
    return Settings(anthropic_api_key="sk-test")


def _fake_transport(text: str):
    """A transport returning ``text`` with token counts — never calls out."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    """An AnthropicLLMClient wired to a fake transport (key present ⇒ live path)."""
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    """A deterministic on-brand judge (V-4 pass)."""

    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def _proposal_json(family_id: UUID, *, body: str) -> str:
    """A schema-conforming, grounded EnrollmentDraftProposal the transport returns."""
    return json.dumps(
        {
            "action": DraftAction.EMAIL.value,
            "family_id": str(family_id),
            "body": body,
            "claims": [
                {"text": "Your TEFA standard award covers tuition.", "source_ref": "kb:tefa"}
            ],
        }
    )


def _suite(*, grounding_passed: bool) -> EvalSuiteResult:
    """A 4-row suite result; only the grounding row's verdict varies."""
    rows = [
        EvalRow(eval_name=_NUDGE, score=0.9, threshold=0.85, passed=True),
        EvalRow(eval_name=_DOC, score=1.0, threshold=0.9, passed=True),
        EvalRow(eval_name=_GROUNDING, score=0.5, threshold=0.95, passed=grounding_passed),
        EvalRow(eval_name=_GEO, score=0.6, threshold=5.0, passed=True),
    ]
    return EvalSuiteResult(rows=rows, overall_green=all(r.passed for r in rows))


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Reset overrides + the observability + eval-state singletons around each test."""
    deps.reset_observability_log()
    deps.reset_eval_state()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    deps.reset_eval_state()


def _draft(family_id: UUID) -> dict[str, object]:
    """Drive a clean, grounded, on-brand draft through the live path."""
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body="A quick note about your enrollment next steps.")
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    resp = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    )
    assert resp.status_code == 200
    return resp.json()  # type: ignore[no-any-return]


def test_red_eval_disables_ai_action() -> None:
    """A RED grounding row disables surfacing in the live path; green re-enables it.

    INV-3 end-to-end: with the consolidated suite's ``message_safety_grounding``
    row red, ``GET /evals`` shows it red + disabled, and a clean grounded draft is
    NOT surfaced (``eval_suite_red`` blocks it). With an all-green suite the same
    draft surfaces again.
    """
    family_id = _a_family_id()

    # --- (a) RED grounding row ⇒ action disabled, draft not surfaced ----------
    red = _suite(grounding_passed=False)
    app.dependency_overrides[deps.get_eval_state] = lambda: red

    evals = client.get("/evals")
    assert evals.status_code == 200
    eview = evals.json()
    grounding_row = next(r for r in eview["rows"] if r["eval_name"] == _GROUNDING)
    assert grounding_row["passed"] is False
    assert eview["disabled"][_GROUNDING] is True
    assert eview["overall_green"] is False

    blocked = _draft(family_id)
    assert blocked["surfaced"] is False
    assert blocked["proposal"] is None
    assert "eval_suite_red" in blocked["failed_rules"]

    # --- (b) all-green suite ⇒ the draft surfaces again -----------------------
    green = _suite(grounding_passed=True)
    app.dependency_overrides[deps.get_eval_state] = lambda: green

    evals2 = client.get("/evals")
    assert evals2.json()["disabled"][_GROUNDING] is False

    surfaced = _draft(family_id)
    assert surfaced["surfaced"] is True
    assert surfaced["proposal"] is not None
    assert "eval_suite_red" not in surfaced["failed_rules"]

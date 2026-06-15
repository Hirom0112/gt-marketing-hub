"""Close-tips orchestration tests (S9 W5; FR-4.3; ARCH §5.2; CLAUDE §4.2).

Behavior tests for the §5.2 close-tips pipeline — the same doctrine as the
enrollment-draft graph, applied to grounded "how to close this family" tips:

* clean grounded tips SURFACE on a passing eval (step 5),
* a hallucinated fact (a tip absent from extracted_fields) is BLOCKED, not
  softened (INV-4, fail-closed),
* a fabricated CITATION (source_ref to a non-existent extracted_fields key) is
  BLOCKED by the close-tips grounding layer (INV-4),
* a malformed LLM payload is REJECTED at the parse boundary, never coerced
  (INV-2),
* a degraded edge (no key) surfaces NOTHING — there is no deterministic close-tips
  template (fail-closed),
* the context pack is GROUNDED — it carries the family's real extracted fields and
  invents none when no application exists.

A fake transport + fake judge are injected throughout — NEVER a live call. Params
come from the committed `params/params.example.yaml`, so the suite is
deterministic without a local params file. All data is SYNTHETIC (INV-1).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.ai.client import AnthropicLLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.close_tips import CloseTipsOutcome, generate_close_tips
from app.ai.prompts.close_tips import build_context_pack, build_prompt
from app.ai.schemas.close_tips import CloseTipsProposal
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.data.models import AppForm, FamilyRecord, Stage, StallReason
from app.data.repository import JoinedFamily

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

FID = UUID("00000000-0000-0000-0000-0000000000fc")
APP_FID = UUID("00000000-0000-0000-0000-0000000000ad")
NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _settings(*, key: str | None) -> Settings:
    return Settings(anthropic_api_key=key)


def _budget(params: Params, settings: Settings) -> RunBudget:
    return RunBudget.from_config(settings=settings, params=params)


def _family(*, extracted_fields: dict[str, object] | None) -> JoinedFamily:
    """A grounded joined family; ``extracted_fields=None`` ⇒ no application at all."""
    family = FamilyRecord(
        family_id=FID,
        display_name="Synthetic Family C",
        primary_contact_synthetic_email="parent.c@synthetic.example",
        current_stage=Stage.ENROLL,
        stall_reason=StallReason.FUNDING_PENDING,
        stalled_since=NOW,
        attribution_source="paid_search",
        attribution_utm={},
        updated_at=NOW,
    )
    app_form = (
        AppForm(
            app_form_id=APP_FID,
            family_id=FID,
            submitted_at=NOW,
            extracted_fields=dict(extracted_fields),
        )
        if extracted_fields is not None
        else None
    )
    return JoinedFamily(
        family=family,
        lead=None,
        app_form=app_form,
        enrollment_forms=None,
        community_profile=None,
    )


def _tips_json(tips: list[dict[str, object]]) -> str:
    """A schema-conforming CloseTipsProposal payload the fake transport returns."""
    return json.dumps({"family_id": str(FID), "tips": tips})


def _fake_transport(text: str):
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _exploding_transport():
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        raise AssertionError("transport must not be invoked on the degraded path")

    return transport


def _on_brand_judge(score: float = 1.0):
    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


# --------------------------------------------------------------------------- #
# 1. Clean grounded tips surface on a passing eval (step 5).
# --------------------------------------------------------------------------- #
def test_clean_grounded_tips_surface() -> None:
    params = _params()
    settings = _settings(key="sk-test")
    tips = [
        {
            "text": "Lead with the homeschool funding path: they self-reported homeschooling.",
            "source_ref": "extracted_fields:prior_schooling",
        },
        {"text": "Offer to walk the parents through the enrollment steps.", "source_ref": None},
    ]
    client = AnthropicLLMClient(settings=settings, transport=_fake_transport(_tips_json(tips)))

    outcome = generate_close_tips(
        _family(extracted_fields={"prior_schooling": "homeschool"}),
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert isinstance(outcome, CloseTipsOutcome)
    assert outcome.degraded is False
    assert outcome.surfaced is True
    assert outcome.validation is not None and outcome.validation.passed is True
    assert isinstance(outcome.proposal, CloseTipsProposal)
    assert len(outcome.proposal.tips) == 2


# --------------------------------------------------------------------------- #
# 2. A hallucinated fact (banned/empirical) is BLOCKED, not softened (INV-4).
# --------------------------------------------------------------------------- #
def test_hallucinated_fact_blocked() -> None:
    params = _params()
    settings = _settings(key="sk-test")
    # A "4X speed" performance multiplier — a hallucinated claim, banned by V-2.
    tips = [{"text": "Pitch that their child learns at 4X speed here.", "source_ref": None}]
    client = AnthropicLLMClient(settings=settings, transport=_fake_transport(_tips_json(tips)))

    outcome = generate_close_tips(
        _family(extracted_fields={"grade_applying": "3"}),
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.surfaced is False
    assert outcome.validation is not None
    assert "v2_grounding" in outcome.validation.failed_rules
    assert outcome.proposal is not None  # parsed but NOT surfaced


# --------------------------------------------------------------------------- #
# 3. A fabricated CITATION (ref to a non-existent field) is BLOCKED (INV-4).
# --------------------------------------------------------------------------- #
def test_fabricated_citation_blocked() -> None:
    """A tip citing extracted_fields:made_up_key (absent) ⇒ close_tips_grounding FAIL.

    The canonical gate's V-2 only checks "is the empirical claim sourced?" — a
    cited ref passes it. The close-tips grounding LAYER additionally requires the
    cited ref to RESOLVE to a real extracted_fields key; a fabricated citation is
    as ungrounded as none ⇒ the proposal is BLOCKED, not softened.
    """
    params = _params()
    settings = _settings(key="sk-test")
    tips = [
        {
            "text": "They listed a sibling discount preference on the form.",
            "source_ref": "extracted_fields:sibling_discount",
        }
    ]
    client = AnthropicLLMClient(settings=settings, transport=_fake_transport(_tips_json(tips)))

    outcome = generate_close_tips(
        _family(extracted_fields={"grade_applying": "5"}),  # no sibling_discount key
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.surfaced is False
    assert outcome.validation is not None
    assert "close_tips_grounding" in outcome.validation.failed_rules


# --------------------------------------------------------------------------- #
# 4. Malformed LLM output is REJECTED at the parse boundary (INV-2).
# --------------------------------------------------------------------------- #
def test_malformed_output_rejected() -> None:
    params = _params()
    settings = _settings(key="sk-test")
    client = AnthropicLLMClient(
        settings=settings, transport=_fake_transport("not json <<garbage>>")
    )

    outcome = generate_close_tips(
        _family(extracted_fields={"grade_applying": "5"}),
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.surfaced is False
    assert outcome.proposal is None
    assert outcome.validation is None
    assert outcome.degraded is False


# --------------------------------------------------------------------------- #
# 5. Degraded edge (no key) surfaces NOTHING — no close-tips template (fail-closed).
# --------------------------------------------------------------------------- #
def test_degraded_surfaces_nothing() -> None:
    params = _params()
    settings = _settings(key=None)
    assert settings.llm_available is False
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport())

    outcome = generate_close_tips(
        _family(extracted_fields={"grade_applying": "5"}),
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=None,
    )

    assert outcome.degraded is True
    assert outcome.surfaced is False
    assert outcome.proposal is None
    assert outcome.validation is None


# --------------------------------------------------------------------------- #
# 6. The context pack is GROUNDED in extracted_fields and invents none.
# --------------------------------------------------------------------------- #
def test_context_pack_is_grounded() -> None:
    # An application with extracted fields ⇒ the pack carries them verbatim.
    joined = _family(extracted_fields={"prior_schooling": "homeschool", "household_size": 4})
    pack = build_context_pack(joined)
    assert pack.family_id == FID
    assert pack.extracted_fields == {"prior_schooling": "homeschool", "household_size": 4}
    prompt = build_prompt(pack)
    assert "extracted_fields:prior_schooling" in prompt
    assert "extracted_fields:household_size" in prompt

    # No application ⇒ NO extracted fields invented; the prompt says so.
    pack_empty = build_context_pack(_family(extracted_fields=None))
    assert pack_empty.extracted_fields == {}
    assert "no extracted application fields" in build_prompt(pack_empty)

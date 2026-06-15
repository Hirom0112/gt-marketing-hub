"""Enrollment-draft orchestration tests (FR-2.4; ARCH §5.2; CLAUDE §4.2).

Acceptance/golden-set-driven: these behavior tests ARE the red tests for the
§5.2 draft pipeline. They prove the four invariants the pipeline must hold:

* a clean grounded draft SURFACES on a passing eval (step 5),
* a banned-claim draft is BLOCKED, not softened (INV-4, fail-closed through the
  whole flow),
* a malformed LLM payload is REJECTED at the parse boundary, never coerced into
  a proposal (INV-2),
* a degraded edge (no key) uses the operator template WITHOUT invoking the
  transport (NFR-3),
* the context pack is GROUNDED — it carries the family's real facts and invents
  no MAP score when no application exists.

A fake transport and a fake judge are injected throughout — NEVER a live call.
Params come from the committed `params/params.example.yaml` (mirrors
`test_work_queue.py`), so the suite is deterministic without a local params file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.ai.client import AnthropicLLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.enrollment_draft import DraftOutcome, draft_enrollment_message
from app.ai.prompts.enrollment_draft import build_context_pack, build_prompt
from app.ai.schemas.enrollment_draft import DraftAction, EnrollmentDraftProposal
from app.core.family_record import assemble_deal_view
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.data.models import (
    AppForm,
    FamilyRecord,
    FundingType,
    Stage,
    StallReason,
)
from app.data.repository import JoinedFamily

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

FID = UUID("00000000-0000-0000-0000-0000000000fa")
APP_FID = UUID("00000000-0000-0000-0000-0000000000ab")
NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _settings(*, key: str | None) -> Settings:
    """A settings snapshot with or without an Anthropic key (drives llm_available)."""
    return Settings(anthropic_api_key=key)


def _budget(params: Params, settings: Settings) -> RunBudget:
    return RunBudget.from_config(settings=settings, params=params)


def _family(*, with_app_form: bool, map_score: float | None = None) -> JoinedFamily:
    """A grounded joined family — funding-pending, ad-sourced; optionally with a MAP."""
    family = FamilyRecord(
        family_id=FID,
        display_name="Synthetic Family A",
        primary_contact_synthetic_email="parent.a@synthetic.example",
        current_stage=Stage.ENROLL,
        stall_reason=StallReason.FUNDING_PENDING,
        stalled_since=NOW,
        funding_type=FundingType.TEFA_STANDARD,
        attribution_source="paid_search",
        attribution_utm={"utm_campaign": "spring"},
        updated_at=NOW,
    )
    app_form = (
        AppForm(
            app_form_id=APP_FID,
            family_id=FID,
            submitted_at=NOW,
            map_score=map_score,
            academic_signals={"grade": "5"} if map_score is not None else {},
        )
        if with_app_form
        else None
    )
    return JoinedFamily(
        family=family,
        lead=None,
        app_form=app_form,
        enrollment_forms=None,
        community_profile=None,
    )


def _valid_proposal_json(*, body: str, sourced: bool = True) -> str:
    """A schema-conforming proposal payload the fake transport returns as text."""
    claims = (
        [{"text": "Your TEFA standard award covers tuition.", "source_ref": "kb:tefa-standard"}]
        if sourced
        else []
    )
    return json.dumps(
        {
            "action": DraftAction.EMAIL.value,
            "family_id": str(FID),
            "body": body,
            "claims": claims,
        }
    )


def _fake_transport(text: str):
    """Build a fake transport returning `text` with token counts, never calling out."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _exploding_transport():
    """A transport that raises if invoked — proves the degraded path never calls it."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        raise AssertionError("transport must not be invoked on the degraded path")

    return transport


def _on_brand_judge(score: float = 1.0):
    """An injected judge that always returns an on-brand score (V-4 pass)."""

    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


# --------------------------------------------------------------------------- #
# 1. Clean draft surfaces on a passing eval (step 5).
# --------------------------------------------------------------------------- #
def test_clean_draft_surfaces_on_pass() -> None:
    """A grounded, on-brand, schema-valid draft SURFACES with a passing verdict.

    The fake transport returns valid grounded JSON (adult audience, no banned
    patterns, claims sourced); an on-brand judge is injected ⇒ all of V-1..V-4
    pass ⇒ `surfaced True`, `validation.passed True`, and `proposal` is the
    PARSED schema object (not a template stand-in).
    """
    params = _params()
    settings = _settings(key="sk-test")
    body = "Hello, just a note on your enrollment and your funding next steps."
    client = AnthropicLLMClient(
        settings=settings,
        transport=_fake_transport(_valid_proposal_json(body=body)),
    )

    outcome = draft_enrollment_message(
        _family(with_app_form=False),
        DraftAction.EMAIL,
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert isinstance(outcome, DraftOutcome)
    assert outcome.degraded is False
    assert outcome.surfaced is True
    assert outcome.validation is not None and outcome.validation.passed is True
    assert isinstance(outcome.proposal, EnrollmentDraftProposal)
    assert outcome.proposal.body == body


# --------------------------------------------------------------------------- #
# 2. Banned-claim draft is BLOCKED, not softened (INV-4, fail-closed).
# --------------------------------------------------------------------------- #
def test_banned_claim_draft_blocked() -> None:
    """A body with a banned "4X speed" claim FAILS V-2 ⇒ the draft is BLOCKED.

    Even with an on-brand judge, the banned performance-multiplier pattern makes
    V-2 fail; the gate BLOCKS (never softens) ⇒ `surfaced False` and
    `"v2_grounding"` is in `failed_rules` (INV-4 fail-closed through the flow).
    """
    params = _params()
    settings = _settings(key="sk-test")
    body = "Students learn at 4X speed here — enroll today."
    client = AnthropicLLMClient(
        settings=settings,
        transport=_fake_transport(_valid_proposal_json(body=body, sourced=False)),
    )

    outcome = draft_enrollment_message(
        _family(with_app_form=False),
        DraftAction.EMAIL,
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.surfaced is False
    assert outcome.validation is not None
    assert "v2_grounding" in outcome.validation.failed_rules
    # The proposal was parsed (it was well-formed JSON) but is NOT surfaced.
    assert outcome.proposal is not None


# --------------------------------------------------------------------------- #
# 3. Malformed LLM output is REJECTED at the parse boundary (INV-2).
# --------------------------------------------------------------------------- #
def test_malformed_llm_output_rejected() -> None:
    """Non-JSON garbage from the edge is REJECTED — never coerced into a proposal.

    The parse boundary (`model_validate_json`) raises on garbage; the pipeline
    returns with NO proposal applied: `surfaced False`, `proposal is None`,
    `validation is None` (the gate is never reached). This is INV-2 — a malformed
    payload fails closed.
    """
    params = _params()
    settings = _settings(key="sk-test")
    client = AnthropicLLMClient(
        settings=settings,
        transport=_fake_transport("not json at all <<garbage>>"),
    )

    outcome = draft_enrollment_message(
        _family(with_app_form=False),
        DraftAction.EMAIL,
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
# 4. Degraded edge (no key) uses the operator template WITHOUT calling out.
# --------------------------------------------------------------------------- #
def test_degraded_without_key_uses_template() -> None:
    """No key ⇒ degraded ⇒ operator template, transport NEVER invoked (NFR-3).

    `settings.llm_available` is False (no key), so the client returns the
    degraded template without touching the transport — an exploding transport is
    injected to PROVE it is never called. The proposal body is the operator
    template; with no judge the gate denies V-4 ⇒ `surfaced False`. The endpoint
    later offers this template as the NFR-3 fallback.
    """
    params = _params()
    settings = _settings(key=None)
    assert settings.llm_available is False
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport())

    outcome = draft_enrollment_message(
        _family(with_app_form=False),
        DraftAction.NUDGE,
        client=client,
        budget=_budget(params, settings),
        settings=settings,
        params=params,
        brand_judge=None,
    )

    assert outcome.degraded is True
    assert outcome.proposal is not None
    assert outcome.proposal.action is DraftAction.NUDGE
    assert outcome.proposal.family_id == FID
    assert "DEGRADED" in outcome.proposal.body
    # No judge ⇒ V-4 fail-closed ⇒ blocked.
    assert outcome.surfaced is False
    assert outcome.validation is not None
    assert "v4_onbrand" in outcome.validation.failed_rules


# --------------------------------------------------------------------------- #
# 5. The context pack is GROUNDED and invents no facts.
# --------------------------------------------------------------------------- #
def test_context_pack_is_grounded() -> None:
    """The context pack/prompt carry the family's REAL facts and invent no MAP.

    The grounded fields (stall reason, funding type, seam status, attribution)
    flow from the DealView into the pack and the prompt. When `app_form` is
    absent the pack carries `map_score=None` — the pipeline does NOT fabricate an
    academic score (the grounding guarantee, INV-2 / V-2). When an app_form with
    a MAP exists the pack surfaces it.
    """
    # No application ⇒ no MAP score may appear.
    joined_no_app = _family(with_app_form=False)
    deal_view = assemble_deal_view(joined_no_app)
    pack = build_context_pack(deal_view)

    assert pack.family_id == FID
    assert pack.stall_reason == StallReason.FUNDING_PENDING.value
    assert pack.funding_type == FundingType.TEFA_STANDARD.value
    assert pack.attribution_source == "paid_search"
    assert pack.map_score is None  # not fabricated
    assert pack.academic_signals == {}

    prompt = build_prompt(pack, DraftAction.FAQ)
    # The grounded facts appear verbatim in the prompt.
    assert StallReason.FUNDING_PENDING.value in prompt
    assert FundingType.TEFA_STANDARD.value in prompt
    assert "paid_search" in prompt
    assert pack.seam_status in prompt
    # The absent MAP score is rendered as "unknown", never invented.
    assert "map_score: unknown" in prompt

    # With an application carrying a MAP, the pack surfaces the real score.
    joined_with_app = _family(with_app_form=True, map_score=215.0)
    pack_with_map = build_context_pack(assemble_deal_view(joined_with_app))
    assert pack_with_map.map_score == 215.0
    assert "215.0" in build_prompt(pack_with_map, DraftAction.EMAIL)

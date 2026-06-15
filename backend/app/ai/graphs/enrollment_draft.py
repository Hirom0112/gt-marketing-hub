"""Enrollment-draft orchestration — context pack → LLM → parse → gate (FR-2.4).

This is step (2)–(5) of the §5.2 draft flow wired into one **linear deterministic
pipeline** (ASSUMPTIONS A-6: implemented as an orchestration function, not a
LangGraph graph — the v1 flow has no branching/state, so the dep is YAGNI):

  2. deterministic core assembles a GROUNDED context pack from the Family Record
     (:func:`~app.ai.prompts.enrollment_draft.build_context_pack`) — no free
     facts.
  3. the AI edge produces a proposal; the result text is parsed into the Pydantic
     :class:`~app.ai.schemas.enrollment_draft.EnrollmentDraftProposal`. A
     **malformed payload is REJECTED here, never coerced** (INV-2) — on parse
     failure no proposal is surfaced.
  4. the eval gate (:func:`~app.core.eval_gate.evaluate_message`) runs the
     message safety/grounding eval; a failing message is **BLOCKED, not softened**
     (INV-4 / fail-closed).
  5. only on a passing eval does the proposal surface.

When the edge is unavailable (no key / kill switch / cost cap) the client returns
``degraded=True`` and we build the proposal **deterministically** from the
operator template (NFR-3 fallback) — only the *drafting* is unavailable; the
pipeline still produces a (blocked) proposal the endpoint can offer as a template.

Purity at the edge: this module consumes the :class:`~app.ai.client.LLMClient`
protocol and imports **no** ``anthropic`` / ``langgraph``. Tests inject a fake
transport + judge, so no live call ever runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError

from app.ai.client import LLMClient, deterministic_fallback
from app.ai.prompts.enrollment_draft import build_context_pack, build_prompt
from app.ai.schemas.enrollment_draft import DraftAction, EnrollmentDraftProposal
from app.core.eval_gate import BrandJudge, ValidationResult, evaluate_message
from app.core.family_record import assemble_deal_view

if TYPE_CHECKING:
    from app.ai.cost import RunBudget
    from app.core.params import Params
    from app.core.settings import Settings
    from app.data.repository import JoinedFamily


@dataclass(frozen=True)
class DraftOutcome:
    """The result of the §5.2 draft pipeline — a proposal, its verdict, and flags.

    `surfaced` is the §5.2 step-5 convenience: only a proposal whose eval PASSED
    surfaces to the operator. When the gate blocks OR the LLM output failed to
    parse, ``surfaced is False`` and the caller offers the deterministic template
    fallback (NFR-3). `proposal` is ``None`` only when parsing failed (INV-2 —
    malformed output is never coerced into a proposal).
    """

    proposal: EnrollmentDraftProposal | None
    validation: ValidationResult | None
    degraded: bool

    @property
    def surfaced(self) -> bool:
        """True iff the eval ran AND passed (§5.2 step 5 — only-on-pass surfaces)."""
        return self.validation is not None and self.validation.passed


def _template_proposal(
    *,
    family_id: UUID,
    action: DraftAction,
    prompt: str,
) -> EnrollmentDraftProposal:
    """Build the deterministic NFR-3 fallback proposal from the operator template.

    The degraded path (no key / kill switch / cap tripped) produces no model
    draft; we surface the clearly-marked operator template as the `body` with no
    claims, so a human still gets something actionable. Marked by
    :func:`~app.ai.client.deterministic_fallback` so it is never mistaken for a
    model-authored draft (INV-2 — a proposal stand-in, not a state write).
    """
    return EnrollmentDraftProposal(
        action=action,
        family_id=family_id,
        body=deterministic_fallback(prompt),
        claims=[],
    )


def draft_enrollment_message(
    joined: JoinedFamily,
    action: DraftAction,
    *,
    client: LLMClient,
    budget: RunBudget,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
) -> DraftOutcome:
    """Run the §5.2 enrollment-draft pipeline and return a :class:`DraftOutcome`.

    Linear pipeline (A-6): build the grounded context pack from the joined Family
    Record (step 2), call the LLM edge (step 3), parse the result into the
    proposal schema rejecting malformed output (INV-2), run the eval gate
    (step 4, INV-4), and surface only on pass (step 5). A degraded edge yields the
    deterministic operator template (NFR-3) which still passes through the gate.

    Args:
        joined: the spine row joined to its four source rows (the grounded source).
        action: the channel the operator requested (email / nudge / faq).
        client: the LLM edge seam (a fake transport is injected under test).
        budget: the per-run token/USD governor (INV-8).
        settings: the env seam; `anthropic_max_tokens` bounds the call.
        params: the loaded params; the eval thresholds read from here (INV-11).
        brand_judge: an INJECTED V-4 brand judge (a proposal — INV-2); ``None``
            ⇒ the gate's judge is unavailable ⇒ V-4 fail-closed (deny).

    Returns:
        A frozen :class:`DraftOutcome`.
    """
    # Step 2 — deterministic grounded context pack from the Family Record.
    deal_view = assemble_deal_view(joined)
    context = build_context_pack(deal_view)
    prompt = build_prompt(context, action)

    # Step 3 — the AI edge. The client fails closed to a degraded template when
    # the edge is unavailable / the budget is tripped (no live call made).
    result = client.complete(prompt, max_tokens=settings.anthropic_max_tokens, budget=budget)

    if result.degraded:
        # NFR-3 deterministic fallback: the operator template is the proposal.
        proposal: EnrollmentDraftProposal | None = _template_proposal(
            family_id=joined.family.family_id,
            action=action,
            prompt=prompt,
        )
    else:
        # Parse boundary (INV-2): the live edge returns JSON conforming to the
        # schema. A malformed/unparseable payload is REJECTED — never coerced —
        # so no proposal is surfaced.
        try:
            proposal = EnrollmentDraftProposal.model_validate_json(result.text)
        except ValidationError:
            return DraftOutcome(proposal=None, validation=None, degraded=False)

    # Step 4 — the eval gate (INV-4, fail-closed). Step 5 — surface only on pass.
    validation = evaluate_message(
        proposal,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
    )
    return DraftOutcome(proposal=proposal, validation=validation, degraded=result.degraded)

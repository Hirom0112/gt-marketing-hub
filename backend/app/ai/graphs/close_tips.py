"""Close-tips orchestration — context pack → LLM → parse → gate (S9 Wave 5; FR-4.3).

This is steps (2)–(5) of the §5.2 proposal flow wired into one **linear
deterministic pipeline** (A-6) for the "how to close this family" tips — the same
shape as :mod:`app.ai.graphs.enrollment_draft`, so there is one doctrine, not two:

  2. deterministic core assembles a GROUNDED context pack from the joined Family
     Record (:func:`~app.ai.prompts.close_tips.build_context_pack`) — the tips may
     ground ONLY on ``app_form.extracted_fields``; no free facts.
  3. the AI edge produces a proposal; the result text is parsed into the Pydantic
     :class:`~app.ai.schemas.close_tips.CloseTipsProposal`. A **malformed payload
     is REJECTED here, never coerced** (INV-2) — on parse failure no proposal is
     surfaced.
  4. the eval gate (:func:`~app.core.eval_gate.evaluate_message`) runs the SAME
     message safety/grounding gate the enrollment draft uses (A-10): a tip that
     asserts a fact absent from ``extracted_fields`` is an unsourced empirical
     claim ⇒ V-2 FAIL ⇒ the proposal is **BLOCKED, not softened** (INV-4).
  5. only on a passing eval does the proposal surface.

When the edge is unavailable (no key / kill switch / cost cap) the client returns
``degraded=True`` and the proposal is ``None`` (no deterministic close-tips
template exists — there is nothing to ground tips on without the model), so the
endpoint surfaces nothing and the UI offers no approvable body (fail-closed).

Purity at the edge: this module consumes the :class:`~app.ai.client.LLMClient`
protocol and imports **no** ``anthropic`` / ``langgraph``. Tests inject a fake
transport + judge, so no live call ever runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.ai.json_parse import strip_code_fence
from app.ai.prompts.close_tips import (
    build_context_pack,
    build_prompt,
    unresolved_grounding_refs,
)
from app.ai.schemas.close_tips import CloseTipsProposal
from app.core.eval_gate import BrandJudge, RuleVerdict, ValidationResult, evaluate_message

if TYPE_CHECKING:
    from app.ai.client import LLMClient
    from app.ai.cost import RunBudget
    from app.core.params import Params
    from app.core.settings import Settings
    from app.data.repository import JoinedFamily


@dataclass(frozen=True)
class CloseTipsOutcome:
    """The result of the §5.2 close-tips pipeline — a proposal, its verdict, flags.

    `surfaced` is the §5.2 step-5 convenience: only a proposal whose eval PASSED
    surfaces to the operator. When the gate blocks, the LLM output failed to
    parse, OR the edge degraded, ``surfaced is False`` and no approvable body is
    offered (there is no deterministic close-tips template — fail-closed). `proposal`
    is ``None`` when parsing failed or the edge degraded (INV-2 — output is never
    coerced into a proposal, and no model output means no grounded tips).
    """

    proposal: CloseTipsProposal | None
    validation: ValidationResult | None
    degraded: bool

    @property
    def surfaced(self) -> bool:
        """True iff the eval ran AND passed (§5.2 step 5 — only-on-pass surfaces)."""
        return self.validation is not None and self.validation.passed


def generate_close_tips(
    joined: JoinedFamily,
    *,
    client: LLMClient,
    budget: RunBudget,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
) -> CloseTipsOutcome:
    """Run the §5.2 close-tips pipeline and return a :class:`CloseTipsOutcome`.

    Linear pipeline (A-6): build the grounded context pack from the joined Family
    Record (step 2; grounds on ``app_form.extracted_fields``), call the LLM edge
    (step 3), parse the result into the proposal schema rejecting malformed output
    (INV-2), run the eval gate (step 4, INV-4), and surface only on pass (step 5).
    A degraded edge yields no proposal (no deterministic close-tips template) so
    nothing surfaces — fail-closed.

    Args:
        joined: the spine row joined to its four source rows (the grounded source).
        client: the LLM edge seam (a fake transport is injected under test).
        budget: the per-run token/USD governor (INV-8).
        settings: the env seam; `anthropic_max_tokens` bounds the call.
        params: the loaded params; the eval thresholds read from here (INV-11).
        brand_judge: an INJECTED V-4 brand judge (a proposal — INV-2); ``None``
            ⇒ the gate's judge is unavailable ⇒ V-4 fail-closed (deny).

    Returns:
        A frozen :class:`CloseTipsOutcome`.
    """
    # The extra `close_tips_grounding` rule name added to a verdict's failed_rules
    # when a tip cites an extracted_fields key that does not resolve (a fabricated
    # citation) — distinct from the canonical V-1..V-4 names so the audit/UI can
    # tell a resolvability block apart from a V-2 banned-pattern block.
    grounding_rule = "close_tips_grounding"

    # Step 2 — deterministic grounded context pack from the Family Record.
    context = build_context_pack(joined)
    prompt = build_prompt(context)

    # Step 3 — the AI edge. The client fails closed to a degraded template when
    # the edge is unavailable / the budget is tripped (no live call made).
    result = client.complete(prompt, max_tokens=settings.anthropic_max_tokens, budget=budget)

    if result.degraded:
        # No deterministic close-tips fallback: tips can only be grounded by the
        # model over the extracted fields, so a degraded edge surfaces nothing.
        return CloseTipsOutcome(proposal=None, validation=None, degraded=True)

    # Parse boundary (INV-2): the live edge returns JSON conforming to the schema,
    # often wrapped in a Markdown ```json fence — unwrap it first (strip_code_fence
    # is a no-op on raw JSON). A malformed/unparseable payload is REJECTED — never
    # coerced — so no proposal is surfaced.
    try:
        proposal = CloseTipsProposal.model_validate_json(strip_code_fence(result.text))
    except ValidationError:
        return CloseTipsOutcome(proposal=None, validation=None, degraded=False)

    # Step 4 — the eval gate (INV-4, fail-closed). Step 5 — surface only on pass.
    # The canonical gate reads `proposal.body` / `proposal.claims` structurally
    # (A-10); a tip citing a fact absent from extracted_fields is unsourced ⇒ V-2
    # FAIL ⇒ blocked, not softened.
    validation = evaluate_message(
        proposal,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
    )

    # Close-tips grounding layer (INV-4): a cited source_ref must RESOLVE to a real
    # extracted_fields key. A fabricated citation (extracted_fields:made_up_key) is
    # as ungrounded as none — fold a `close_tips_grounding` FAIL into the verdict so
    # the proposal is BLOCKED (not surfaced), never softened. The model_copy keeps
    # ValidationResult frozen while recording the extra failing rule for the audit.
    unresolved = unresolved_grounding_refs(proposal, context)
    if unresolved and validation.passed:
        failed = [*validation.failed_rules, grounding_rule]
        validation = validation.model_copy(
            update={"passed": False, "failed_rules": failed, "v2_grounding": RuleVerdict.FAIL}
        )
    elif unresolved and grounding_rule not in validation.failed_rules:
        validation = validation.model_copy(
            update={"failed_rules": [*validation.failed_rules, grounding_rule]}
        )

    return CloseTipsOutcome(proposal=proposal, validation=validation, degraded=False)

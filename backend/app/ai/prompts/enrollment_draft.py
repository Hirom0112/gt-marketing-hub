"""Grounded prompt builder for the enrollment-draft edge (FR-2.4; ARCH §5.2).

Step (2) of the §5.2 draft flow is the **deterministic core assembling a grounded
context pack** from the Family Record — no free-floating facts. This module is
that step: two pure functions, no I/O, no LLM, no SDK import.

* :func:`build_context_pack` flattens a :class:`~app.core.family_record.DealView`
  (already a pure projection of the joined Family Record) into a frozen
  :class:`ContextPack` of *only* the grounded facts the drafter may use — stage,
  stall reason, funding state, attribution, and the academic signals (MAP score)
  that exist *only when an application has been submitted*. When `app_form` is
  absent the DealView already carries `map_score=None`, so the pack never invents
  one (the grounding guarantee — INV-2 / V-2).
* :func:`build_prompt` renders the pack + the requested
  :class:`~app.ai.schemas.enrollment_draft.DraftAction` into the instruction text
  handed to the LLM edge, instructing it to emit JSON conforming to the proposal
  schema and to ground every empirical claim in a listed fact.

Purity: this is the AI edge, but it imports **no** ``anthropic`` / ``langgraph``
and does no I/O — it consumes the already-assembled grounded view. The edge stays
a thin, testable shell over the deterministic core (ARCH §1).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.ai.schemas.enrollment_draft import DraftAction
from app.core.family_record import DealView


class ContextPack(BaseModel):
    """The grounded fact pack handed to the drafter — only Family-Record facts.

    Every field is sourced from the :class:`DealView` (itself a pure projection
    of the joined Family Record). Nullable academic fields stay ``None`` when no
    application exists, so the drafter cannot ground a claim on a fabricated MAP
    score. Frozen: a grounded pack is not mutated after assembly.
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    display_name: str
    seam_status: str
    stall_reason: str | None
    funding_type: str | None
    attribution_source: str
    map_score: float | None
    academic_signals: dict[str, object]


def build_context_pack(deal_view: DealView) -> ContextPack:
    """Assemble the grounded :class:`ContextPack` from a :class:`DealView`.

    Pure: a deterministic function of the deal view alone. Pulls only the fields
    the drafter is allowed to ground a message on (§5.2 step 2). The §4.8 enums
    on the deal view are rendered to their string token form so the prompt and
    the eval/UI layers see the same values; nullable academic fields pass through
    untouched, so an `interest`-stage family (no `app_form`) yields
    ``map_score=None`` and the pack invents no academic signal.

    Args:
        deal_view: the FR-2.2 operator projection of the joined Family Record.

    Returns:
        The grounded :class:`ContextPack`.
    """
    return ContextPack(
        family_id=deal_view.family_id,
        display_name=deal_view.display_name,
        seam_status=deal_view.crm_seam_status.value,
        stall_reason=deal_view.stall_reason.value if deal_view.stall_reason is not None else None,
        funding_type=deal_view.funding_type.value if deal_view.funding_type is not None else None,
        attribution_source=deal_view.attribution_source,
        map_score=deal_view.map_score,
        academic_signals=dict(deal_view.academic_signals),
    )


def build_prompt(context: ContextPack, action: DraftAction) -> str:
    """Render the grounded context pack + requested action into the LLM prompt.

    The prompt instructs the edge to emit JSON conforming to the
    :class:`~app.ai.schemas.enrollment_draft.EnrollmentDraftProposal` schema and
    to ground every empirical claim in a fact listed in the pack — the grounding
    contract the eval gate then enforces (V-2). Only the grounded facts appear;
    a ``None`` field is rendered as "unknown" so the drafter is told the fact is
    absent rather than being free to invent one.

    Args:
        context: the grounded fact pack.
        action: the channel the operator requested (email / nudge / faq).

    Returns:
        The prompt string.
    """
    facts = [
        f"family_id: {context.family_id}",
        f"display_name: {context.display_name}",
        f"crm_seam_status: {context.seam_status}",
        f"stall_reason: {context.stall_reason if context.stall_reason is not None else 'unknown'}",
        f"funding_type: {context.funding_type if context.funding_type is not None else 'unknown'}",
        f"attribution_source: {context.attribution_source}",
        f"map_score: {context.map_score if context.map_score is not None else 'unknown'}",
        f"academic_signals: {context.academic_signals if context.academic_signals else 'none'}",
    ]
    facts_block = "\n".join(f"- {fact}" for fact in facts)
    return (
        f"Draft an enrollment {action.value} for this family using ONLY the grounded "
        "facts below. Do not invent facts not listed here. Return a JSON object "
        "conforming to the EnrollmentDraftProposal schema with keys: action, "
        "family_id, body, claims (each claim: text, source_ref). Every empirical "
        "claim in the body must appear in claims with a source_ref.\n\n"
        f"Requested action: {action.value}\n\n"
        f"Grounded facts:\n{facts_block}\n"
    )

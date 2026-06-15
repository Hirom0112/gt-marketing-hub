"""Grounded prompt builder for the close-tips edge (S9 Wave 5; FR-4.3; ARCH §5.2).

Step (2) of the §5.2 proposal flow is the **deterministic core assembling a
grounded context pack** from the Family Record — no free-floating facts. This
module is that step for the "how to close this family" tips: two pure functions,
no I/O, no LLM, no SDK import.

The grounding source is the family's ``app_form.extracted_fields`` (the §4.3
doc-extraction map; FR-4.2). The drafter may ground a tip ONLY on a key present
there — a tip that asserts a fact absent from ``extracted_fields`` is unsourced
and the gate FAILS it (V-2; the hallucinated-fact block, INV-4). When no
application exists the pack carries an EMPTY field set, so the drafter is told
there are no grounded facts rather than being free to invent one.

* :func:`build_context_pack` flattens a :class:`~app.data.repository.JoinedFamily`
  into a frozen :class:`CloseTipsContext` of *only* grounded facts: the display
  name, the deterministic stall reason, and the ``extracted_fields`` the tips may
  rest on, each addressable by a stable ``extracted_fields:<key>`` source ref.
* :func:`build_prompt` renders the pack into the instruction text handed to the
  LLM edge, instructing it to emit JSON conforming to the proposal schema and to
  ground every tip in a listed ``extracted_fields`` key.

Purity: this is the AI edge, but it imports **no** ``anthropic`` / ``langgraph``
and does no I/O — it consumes the already-joined Family Record. The edge stays a
thin, testable shell over the deterministic core (ARCH §1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.ai.schemas.close_tips import CloseTipsProposal
    from app.data.repository import JoinedFamily

# The stable source-ref prefix a grounded tip uses to cite an extracted field.
# A tip's `source_ref` is `f"{EXTRACTED_FIELD_REF_PREFIX}{key}"` for a key that
# exists in `extracted_fields`; the prompt advertises exactly these refs so the
# drafter cannot cite a key that is not grounded (INV-11 — one canonical home).
EXTRACTED_FIELD_REF_PREFIX = "extracted_fields:"


def extracted_field_ref(key: str) -> str:
    """The canonical grounding source-ref for an ``extracted_fields`` key.

    A tip grounded on ``extracted_fields[key]`` carries this as its `source_ref`,
    so V-2 sees a sourced (grounded) empirical claim. Keeping the format in one
    function means the prompt, the schema fixtures, and any caller agree (INV-11).
    """
    return f"{EXTRACTED_FIELD_REF_PREFIX}{key}"


class CloseTipsContext(BaseModel):
    """The grounded fact pack handed to the close-tips drafter — Family-Record only.

    Every field is sourced from the joined Family Record. ``extracted_fields`` is
    the §4.3 doc-extraction map the tips may ground on; it is EMPTY when no
    application exists, so the drafter cannot ground a tip on a fabricated fact
    (the grounding guarantee — INV-2 / V-2). Frozen: a grounded pack is not
    mutated after assembly.
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    display_name: str
    stall_reason: str | None
    extracted_fields: dict[str, object]


def build_context_pack(joined: JoinedFamily) -> CloseTipsContext:
    """Assemble the grounded :class:`CloseTipsContext` from a joined family.

    Pure: a deterministic function of ``joined`` alone (no data access — the join
    is already done). Pulls only the fields the drafter may ground a tip on (§5.2
    step 2): the display name + stall reason from the spine, and the
    ``app_form.extracted_fields`` doc-extraction map (empty when no application
    exists, so an ``interest``-stage family yields no grounded facts and the pack
    invents none).

    Args:
        joined: the spine row joined to its four source rows (the grounded source).

    Returns:
        The grounded :class:`CloseTipsContext`.
    """
    family = joined.family
    app_form = joined.app_form
    extracted = dict(app_form.extracted_fields) if app_form is not None else {}
    return CloseTipsContext(
        family_id=family.family_id,
        display_name=family.display_name,
        stall_reason=family.stall_reason.value if family.stall_reason is not None else None,
        extracted_fields=extracted,
    )


def unresolved_grounding_refs(proposal: CloseTipsProposal, context: CloseTipsContext) -> list[str]:
    """Tip ``source_ref``s that DON'T resolve to a real ``extracted_fields`` key.

    The close-tips grounding contract is stricter than the canonical gate's V-2
    "is the claim sourced?" check: a cited ``source_ref`` must point at a key that
    ACTUALLY exists in the family's ``extracted_fields`` (the grounding source). A
    tip that cites ``extracted_fields:made_up_key`` (a fabricated citation) is just
    as ungrounded as one that cites nothing — so this returns every cited ref that
    does not resolve, and the close-tips pipeline BLOCKS the proposal if the list
    is non-empty (INV-4 fail-closed; the hallucinated-citation block).

    A ``source_ref`` of ``None`` (a self-evident, non-empirical tip) is NOT a
    grounding ref and is ignored here — V-2 still catches an unsourced *empirical*
    tip. Only refs using the :data:`EXTRACTED_FIELD_REF_PREFIX` are checked; any
    other ref shape is treated as unresolved (it cannot be a grounded family fact).

    Args:
        proposal: the parsed close-tips proposal whose tips carry ``source_ref``s.
        context: the grounded fact pack (its ``extracted_fields`` is the source).

    Returns:
        The cited refs that do not resolve to an ``extracted_fields`` key, in tip
        order (empty ⇒ every cited ref is grounded).
    """
    unresolved: list[str] = []
    for tip in proposal.tips:
        ref = tip.source_ref
        if ref is None:
            continue
        if not ref.startswith(EXTRACTED_FIELD_REF_PREFIX):
            unresolved.append(ref)
            continue
        key = ref[len(EXTRACTED_FIELD_REF_PREFIX) :]
        if key not in context.extracted_fields:
            unresolved.append(ref)
    return unresolved


def build_prompt(context: CloseTipsContext) -> str:
    """Render the grounded context pack into the close-tips LLM prompt.

    The prompt instructs the edge to emit JSON conforming to the
    :class:`~app.ai.schemas.close_tips.CloseTipsProposal` schema and to ground
    every tip in an ``extracted_fields`` key listed in the pack — the grounding
    contract the eval gate then enforces (V-2). Only the grounded facts appear; a
    family with no extracted fields is told so, so the drafter is steered to
    decline rather than invent a fact.

    Args:
        context: the grounded fact pack.

    Returns:
        The prompt string.
    """
    if context.extracted_fields:
        field_lines = "\n".join(
            f"- {extracted_field_ref(key)} = {value!r}"
            for key, value in context.extracted_fields.items()
        )
    else:
        field_lines = "- (none — this family has no extracted application fields)"
    return (
        "You are advising an enrollment operator on HOW TO CLOSE this family. "
        "Produce concrete, actionable tips grounded ONLY in the extracted "
        "application fields below. Do NOT invent facts not listed here. Return a "
        "JSON object conforming to the CloseTipsProposal schema with keys: "
        "family_id, tips (each tip: text, source_ref). Every tip that asserts a "
        "fact about this family MUST cite the extracted_fields key it rests on in "
        "source_ref (e.g. 'extracted_fields:household_size'); a tip that cites no "
        "fact may set source_ref to null only if it is self-evident advice.\n\n"
        f"family_id: {context.family_id}\n"
        f"display_name: {context.display_name}\n"
        f"stall_reason: {context.stall_reason if context.stall_reason is not None else 'unknown'}\n"
        f"Grounded extracted application fields:\n{field_lines}\n"
    )

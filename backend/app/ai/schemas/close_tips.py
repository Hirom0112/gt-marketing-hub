"""CloseTipsProposal — AI-drafted "how to close this family" tips as a proposal.

INV-2 (CLAUDE.md §1): an LLM result is a schema-validated **proposal**, never a
state write. This module is that safeguard's data contract for S9 Wave 5: the LLM
edge emits JSON, we parse it into :class:`CloseTipsProposal`, and a malformed
payload RAISES `pydantic.ValidationError` rather than being silently coerced. The
deterministic core owns every write; this shape only crosses the boundary as a
`proposal` awaiting human review (FR-4.3 gate, then operator approval — P-2).

Grounding contract (CONTENT_SPEC §9.2 V-2): the tips must be grounded in the
family's ``app_form.extracted_fields``. Every empirical tip carries a
:class:`CloseTip.source_ref` pointing at the ``extracted_fields`` key it rests on
(e.g. ``extracted_fields:household_size``). A tip that asserts a fact NOT present
in ``extracted_fields`` is an unsourced empirical claim, which the canonical
grounding gate (:func:`app.core.eval_gate.evaluate_message`) FAILS (V-2) — so a
hallucinated fact is **blocked, not softened** (INV-4, fail-closed).

The proposal is gated by the SAME canonical gate that gates the enrollment draft
(A-10): the gate reads ``.body`` (the rendered tips text) and ``.claims`` (the
per-tip grounded claims) structurally, so no new gate is introduced. ``body`` and
``claims`` are computed from ``tips`` so the two can never drift.

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: no `anthropic` / `langgraph` /
I/O imports — guarded by `test_core_purity`. Mirrors the §4.8 StrEnum + Pydantic
v2 conventions in `app/data/models.py` and the sibling `enrollment_draft.py`.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class CloseTip(BaseModel):
    """One "how to close" tip, with its grounding support (V-2; §9.2).

    `text` is a single operator-facing tip ("Lead with the homeschool funding
    path — they self-reported homeschooling."). `source_ref` points at the
    ``app_form.extracted_fields`` key the tip rests on (e.g.
    ``extracted_fields:prior_schooling``). ``None`` ⇒ an unsourced empirical tip,
    which the grounding gate FAILS unless self-evident — so a hallucinated fact
    cannot pass. Frozen + `extra="forbid"`: no field can be smuggled past the
    gate, and a tip is not mutated after it is proposed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    source_ref: str | None = None


class CloseTipsProposal(BaseModel):
    """AI-drafted "how to close this family" tips, awaiting human review (INV-2).

    Frozen + `extra="forbid"`: the proposal is immutable once parsed and rejects
    any unexpected top-level field, so a malformed LLM payload fails closed rather
    than being coerced into a write. `family_id` is a strict `UUID` and `tips` a
    strict, non-empty `list[CloseTip]`; wrong-typed input RAISES, never coerces.

    ``body`` and ``claims`` are DERIVED (computed) from ``tips`` so the canonical
    grounding gate (which reads ``.body`` / ``.claims`` structurally — A-10) gates
    this proposal exactly as it gates an enrollment draft, with no second gate and
    no chance for the gated text to drift from the tips the operator sees.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family_id: UUID
    tips: list[CloseTip] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body(self) -> str:
        """The rendered tips text the grounding gate's V-1/V-2/V-3 scan over.

        One tip per line so the banned-pattern (V-2) and minor-targeting (V-3)
        scans see every tip's words; the canonical gate reads this via ``.body``.
        """
        return "\n".join(tip.text for tip in self.tips)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claims(self) -> list[CloseTip]:
        """The grounding claims the gate's V-2 checks for a ``source_ref`` (A-10).

        Each tip IS a claim (it carries `text` + `source_ref`), so the gate's
        structural `claims` read finds the per-tip grounding evidence — an
        empirical tip with no `source_ref` FAILS V-2 (the hallucinated-fact block).
        """
        return list(self.tips)

"""EnrollmentDraftProposal — an AI-drafted enrollment message as a validated proposal.

INV-2 (CLAUDE.md §1): an LLM result is a schema-validated **proposal**, never a
state write. This module is that safeguard's data contract: the LLM edge emits
JSON, we parse it into :class:`EnrollmentDraftProposal`, and a malformed payload
RAISES `pydantic.ValidationError` rather than being silently coerced. The
deterministic core owns every write; this shape only crosses the boundary as a
`proposal` awaiting human approval (FR-2.4).

CONTENT_SPEC §9.2 (V-2 grounding): every empirical statement in `body` is also
listed as a :class:`Claim` carrying its `source_ref`, so the grounding/safety
gate (a SEPARATE agent — INV-4) can check each claim against its support. A
`source_ref is None` means an unsourced empirical claim, which V-2 will FAIL
unless self-evident; we only model the data here so it CAN be checked.

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: no `anthropic` / `langgraph` /
I/O imports — guarded by `test_core_purity`. Mirrors the §4.8 StrEnum + Pydantic
v2 conventions in `app/data/models.py`.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DraftAction(StrEnum):
    """The enrollment-draft channel an operator may approve (FR-2.4).

    String-valued so it serializes to the exact tokens the params/eval and UI
    layers use, matching the §4.8 enum style in `app/data/models.py`.
    """

    EMAIL = "email"
    NUDGE = "nudge"
    FAQ = "faq"


class Claim(BaseModel):
    """One empirical claim made in a draft `body`, with its support (V-2; §9.2).

    `source_ref` points at the support a claim rests on (e.g. a KB id). `None`
    ⇒ an unsourced empirical claim, which the grounding gate (INV-4, separate
    agent) FAILS unless self-evident. Frozen + `extra="forbid"`: no field can be
    smuggled past the gate, and a claim is not mutated after it is proposed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    source_ref: str | None = None


class EnrollmentDraftProposal(BaseModel):
    """An AI-drafted enrollment message, awaiting human approval (INV-2; FR-2.4).

    Frozen + `extra="forbid"`: the proposal is immutable once parsed and rejects
    any unexpected top-level field, so a malformed LLM payload fails closed
    rather than being coerced into a write. `family_id` is a strict `UUID` and
    `claims` a strict `list[Claim]`; wrong-typed input RAISES, never coerces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: DraftAction
    family_id: UUID
    body: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)

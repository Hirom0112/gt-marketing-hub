"""Notes-timeline core — the ``Note`` model + deterministic auto-note builders (FR-2.3).

FR-2.3 wants a per-family notes timeline with two interleaved kinds:

- **Manual notes** — operator-authored free text, stored directly.
- **Auto-notes (state-change summaries)** — *deterministic*, system-authored,
  factual records of a known stage / funding transition.

ASSUMPTIONS A-8 / INV-2: an auto-note is computed by the deterministic core from
a transition it ALREADY knows — it is not LLM output and not a proposal. It
makes no unverifiable claim, needs no eval gate, and is stored directly. INV-2
("LLM output is a proposal, never a state write") governs LLM-generated content;
there is none on this path. So this module routes nothing through the
proposal/eval machinery and calls no LLM. If GT later wants LLM-enriched notes,
those become eval-gated proposals — a different path.

Purity (CLAUDE §3, INV-2; guarded by ``test_core_purity`` + ``test_notes``):
this is plain data + pure functions. It imports nothing from ``app.ai`` /
``app.adapters`` and does no I/O. The repository (store seam) and the HTTP route
(composition layer) live elsewhere.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.data.models import FundingState, Stage

# ---------------------------------------------------------------------------
# Note provenance enumerations (StrEnum — §4.8 style; serialize to exact tokens).
# ---------------------------------------------------------------------------


class NoteAuthor(StrEnum):
    """Who authored the note (FR-2.3).

    ``operator`` ⇒ a human enrollment operator (manual notes). ``system`` ⇒ the
    deterministic core (auto-notes / state-change summaries) — never an LLM.
    """

    OPERATOR = "operator"
    SYSTEM = "system"


class NoteKind(StrEnum):
    """The note kind (FR-2.3).

    ``manual`` ⇒ operator free text, stored directly. ``state_change`` ⇒ a
    deterministic summary of a stage / funding transition.
    """

    MANUAL = "manual"
    STATE_CHANGE = "state_change"


# ---------------------------------------------------------------------------
# The timeline entry.
# ---------------------------------------------------------------------------


class Note(BaseModel):
    """One entry on a family's notes timeline (FR-2.3).

    Manual notes ⇒ ``author=operator``, ``kind=manual`` (free text). Auto-notes
    ⇒ ``author=system``, ``kind=state_change`` (a deterministic transition
    summary; A-8 — not a proposal). ``created_at`` is injected (never read off a
    wall clock here) so the timeline order is deterministic and testable.
    """

    note_id: UUID = Field(default_factory=uuid4)
    family_id: UUID
    author: NoteAuthor
    kind: NoteKind
    body: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Deterministic auto-note builders (A-8). Pure functions of the transition the
# core already knows — no LLM, no I/O, no randomness, no clock read.
# ---------------------------------------------------------------------------


def summarize_stage_change(
    *,
    family_id: UUID,
    from_stage: Stage,
    to_stage: Stage,
    at: datetime,
) -> Note:
    """Build a deterministic auto-note for a stage transition (FR-2.3; A-8).

    The body factually names the known ``from`` → ``to`` stage tokens (e.g.
    ``"Stage advanced: apply → enroll"``). Same inputs ⇒ identical body — no
    randomness, no clock leakage. System-authored, ``state_change`` kind.
    """
    body = f"Stage advanced: {from_stage.value} → {to_stage.value}"
    return Note(
        family_id=family_id,
        author=NoteAuthor.SYSTEM,
        kind=NoteKind.STATE_CHANGE,
        body=body,
        created_at=at,
    )


def summarize_funding_change(
    *,
    family_id: UUID,
    from_state: FundingState,
    to_state: FundingState,
    at: datetime,
) -> Note:
    """Build a deterministic auto-note for a funding-state transition (FR-2.3; A-8).

    The body factually names the known ``from`` → ``to`` funding-state tokens
    (e.g. ``"Funding: awarded_selfreport → gt_confirmed"``). Deterministic,
    system-authored, ``state_change`` kind — not a proposal.
    """
    body = f"Funding: {from_state.value} → {to_state.value}"
    return Note(
        family_id=family_id,
        author=NoteAuthor.SYSTEM,
        kind=NoteKind.STATE_CHANGE,
        body=body,
        created_at=at,
    )

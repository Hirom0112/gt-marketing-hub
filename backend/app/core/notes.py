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


# The channel → human label map for the follow-up note (email/sms are the two
# §5.2 draft channels; everything else falls back to the raw channel token).
_FOLLOWUP_CHANNEL_LABELS = {"email": "Email", "sms": "Nudge"}
# How many characters of the draft body the follow-up note excerpts (A-8 factual
# record — a short, deterministic preview, not the full message).
_FOLLOWUP_EXCERPT_CHARS = 60


def summarize_followup(channel: str, body_excerpt: str) -> str:
    """Build the deterministic auto follow-up note body for an approved send (S9 W2).

    The approve path (``api/ai_actions.py``) appends a system/state_change note
    when an outbound is approved and SIMULATED-sent; this builds its body from the
    known channel and the draft's body. ``email`` ⇒ ``"Email sent (simulated): …"``
    and ``sms`` ⇒ ``"Nudge sent (simulated): …"``; an unknown channel falls back
    to its raw token. The excerpt is the first 60 chars of ``body_excerpt`` (an
    ellipsis appended only when the body is longer) so the note stays a short,
    factual preview.

    Pure (A-8; INV-2): a deterministic function of its two arguments — no clock,
    no randomness, no LLM. Same inputs ⇒ identical body.

    Args:
        channel: The send channel (``email`` / ``sms``), mapped to a human label.
        body_excerpt: The approved draft's body, truncated for the preview.

    Returns:
        The follow-up note body string.
    """
    label = _FOLLOWUP_CHANNEL_LABELS.get(channel, channel)
    excerpt = body_excerpt[:_FOLLOWUP_EXCERPT_CHARS]
    if len(body_excerpt) > _FOLLOWUP_EXCERPT_CHARS:
        excerpt += "…"
    return f"{label} sent (simulated): {excerpt}"


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

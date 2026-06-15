"""Notes-timeline tests (FR-2.3; ASSUMPTIONS A-8).

The per-family notes timeline interleaves two note kinds chronologically:

- **Manual notes** — operator-authored free text, stored directly
  (``author=operator``, ``kind=manual``).
- **Auto-notes** — *deterministic* state-change summaries the core computes from
  a stage / funding transition it already knows (``author=system``,
  ``kind=state_change``). These are NOT LLM output and NOT proposals (A-8): the
  summary is a factual record of a known transition, makes no unverifiable
  claim, needs no eval gate, and is stored directly. INV-2 governs LLM output,
  of which there is none on this path.

These tests are the RED step (CLAUDE §4.1): they pin the deterministic summary
bodies, the chronological interleave, the HTTP round-trip, and — structurally —
that the auto-note path imports no ``app.ai`` / ``anthropic`` and returns a
``Note`` (never a proposal). Timestamps are injected so ordering is exact.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_notes_repository, get_repository
from app.core.notes import (
    Note,
    NoteAuthor,
    NoteKind,
    summarize_funding_change,
    summarize_stage_change,
)
from app.data.models import FundingState, Stage
from app.data.notes_repository import InMemoryNotesRepository, NotesRepository
from app.main import app

client = TestClient(app)

# Pinned timestamps so the interleave order is deterministic (no clock leakage).
_T0 = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
_T2 = datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc)


def test_notes_timeline_appends_manual_and_auto() -> None:
    """A manual note and a stage-change auto-note interleave chronologically."""
    repo: NotesRepository = InMemoryNotesRepository()
    family_id = uuid4()

    # A manual note at T1 and a deterministic auto-note for a stage change at T0
    # — added out of order to prove list_notes sorts chronologically.
    manual = Note(
        note_id=uuid4(),
        family_id=family_id,
        author=NoteAuthor.OPERATOR,
        kind=NoteKind.MANUAL,
        body="called family",
        created_at=_T1,
    )
    auto = summarize_stage_change(
        family_id=family_id,
        from_stage=Stage.APPLY,
        to_stage=Stage.ENROLL,
        at=_T0,
    )
    repo.add_note(manual)
    repo.add_note(auto)

    timeline = repo.list_notes(family_id)

    # BOTH notes present, chronological (auto @ T0 before manual @ T1).
    assert [n.note_id for n in timeline] == [auto.note_id, manual.note_id]

    # The auto-note is a system-authored state-change record naming the transition.
    assert auto.author is NoteAuthor.SYSTEM
    assert auto.kind is NoteKind.STATE_CHANGE
    assert "apply" in auto.body
    assert "enroll" in auto.body

    # The manual note kept its operator/manual provenance.
    assert manual.author is NoteAuthor.OPERATOR
    assert manual.kind is NoteKind.MANUAL


def test_summarize_stage_change_is_deterministic() -> None:
    """Same inputs ⇒ identical body; the body names both from and to stages."""
    family_id = uuid4()
    a = summarize_stage_change(
        family_id=family_id, from_stage=Stage.INTEREST, to_stage=Stage.APPLY, at=_T0
    )
    b = summarize_stage_change(
        family_id=family_id, from_stage=Stage.INTEREST, to_stage=Stage.APPLY, at=_T0
    )
    assert a.body == b.body
    assert "interest" in a.body
    assert "apply" in a.body


def test_summarize_funding_change_is_deterministic() -> None:
    """Funding summaries are deterministic and name both from and to states."""
    family_id = uuid4()
    a = summarize_funding_change(
        family_id=family_id,
        from_state=FundingState.AWARDED_SELFREPORT,
        to_state=FundingState.GT_CONFIRMED,
        at=_T0,
    )
    b = summarize_funding_change(
        family_id=family_id,
        from_state=FundingState.AWARDED_SELFREPORT,
        to_state=FundingState.GT_CONFIRMED,
        at=_T0,
    )
    assert a.body == b.body
    assert a.author is NoteAuthor.SYSTEM
    assert a.kind is NoteKind.STATE_CHANGE
    assert "awarded_selfreport" in a.body
    assert "gt_confirmed" in a.body


def test_post_manual_note_then_get_timeline() -> None:
    """POST a manual note then GET the timeline returns it (operator/manual)."""
    # A valid family id comes from the seeded read repo (the same store the app
    # validates family existence against).
    seeded = get_repository()
    family_id = seeded.list_families()[0].family_id

    # Fresh notes store per case (the timeline is append-only — avoid cross-test
    # bleed); override the dep for this client.
    app.dependency_overrides[get_notes_repository] = lambda: InMemoryNotesRepository()
    try:
        post = client.post(f"/families/{family_id}/notes", json={"body": "called family"})
        assert post.status_code == 201
        created = post.json()
        assert created["body"] == "called family"
        assert created["author"] == NoteAuthor.OPERATOR.value
        assert created["kind"] == NoteKind.MANUAL.value
        assert created["family_id"] == str(family_id)

        got = client.get(f"/families/{family_id}/notes")
        assert got.status_code == 200
        timeline = got.json()
        assert len(timeline) == 1
        assert timeline[0]["note_id"] == created["note_id"]
        assert timeline[0]["body"] == "called family"

        # Unknown family ⇒ 404 on both POST and GET (family existence validated).
        unknown = uuid4()
        assert client.post(f"/families/{unknown}/notes", json={"body": "x"}).status_code == 404
        assert client.get(f"/families/{unknown}/notes").status_code == 404
    finally:
        app.dependency_overrides.pop(get_notes_repository, None)


def test_auto_note_is_not_a_proposal() -> None:
    """Auto-notes are deterministic Notes, never proposals (A-8); core stays pure.

    Structural pin: ``app/core/notes.py`` imports no ``app.ai`` / ``anthropic``
    (it is on the deterministic core's purity guard), and the summary builders
    return a ``Note`` directly — there is no proposal / eval-gate machinery on
    this path.
    """
    source = (Path(__file__).resolve().parents[2] / "app" / "core" / "notes.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("app.ai", "anthropic", "langgraph", "app.adapters")
    assert not [m for m in imported if m.startswith(forbidden)], (
        f"core/notes.py must stay pure — found forbidden imports in {imported}"
    )

    # The builders return a Note directly (not a proposal-shaped object).
    note = summarize_stage_change(
        family_id=uuid4(), from_stage=Stage.ENROLL, to_stage=Stage.TUITION, at=_T2
    )
    assert isinstance(note, Note)
    assert note.author is NoteAuthor.SYSTEM

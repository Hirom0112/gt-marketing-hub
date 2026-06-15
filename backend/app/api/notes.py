"""Notes-timeline endpoints — the FR-2.3 per-family notes surface (§6).

Two routes over the notes store seam (``deps.get_notes_repository``):

  ``GET /families/{family_id}/notes``
    The chronological timeline — manual notes and deterministic state-change
    auto-notes interleaved by ``created_at``. 404 if the family is unknown.

  ``POST /families/{family_id}/notes``  (body ``{"body": "<text>"}``)
    Append an operator-authored manual note (``author=operator``,
    ``kind=manual``), stored directly. 404 if the family is unknown.

Family existence is validated against the read repository
(``deps.get_repository`` — the seeded Family Record store), so notes can't be
attached to a family that doesn't exist. Auto-notes are written by the
deterministic core elsewhere (on a real state transition); this surface only
adds *manual* notes — manual notes never route through the proposal/eval
machinery (A-8), and neither do auto-notes.

This module is the composition layer (it may import the repos); ``app/core/``
stays pure. No AI, no LLM call — the whole notes path is deterministic (A-8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_notes_repository, get_repository
from app.core.notes import Note, NoteAuthor, NoteKind
from app.data.notes_repository import NotesRepository
from app.data.repository import FamilyRepository

router = APIRouter(tags=["notes"])

# Dependency aliases (Annotated keeps the call in the type, not a default arg).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
NotesRepositoryDep = Annotated[NotesRepository, Depends(get_notes_repository)]


class ManualNoteRequest(BaseModel):
    """``POST /families/{id}/notes`` body — the operator's free-text note (FR-2.3)."""

    body: str


def _require_family(family_id: UUID, repository: FamilyRepository) -> None:
    """404 if the family is unknown — notes can't attach to a missing family."""
    if repository.get_family(family_id) is None:
        raise HTTPException(status_code=404, detail="family not found")


@router.get("/families/{family_id}/notes", response_model=list[Note])
def list_notes(
    family_id: UUID,
    repository: RepositoryDep,
    notes: NotesRepositoryDep,
) -> list[Note]:
    """Return the family's chronological notes timeline (FR-2.3). 404 if unknown."""
    _require_family(family_id, repository)
    return notes.list_notes(family_id)


@router.post("/families/{family_id}/notes", response_model=Note, status_code=201)
def add_manual_note(
    family_id: UUID,
    request: ManualNoteRequest,
    repository: RepositoryDep,
    notes: NotesRepositoryDep,
) -> Note:
    """Append an operator-authored manual note (FR-2.3). 404 if family unknown.

    Manual notes are stored directly (operator/manual). The timestamp is set
    server-side at insert (UTC) — the only path here that reads a wall clock,
    and it is not a value any test pins (tests inject timestamps on the core
    builders, and assert round-trip identity / order via ids on this route).
    """
    _require_family(family_id, repository)
    note = Note(
        note_id=uuid4(),
        family_id=family_id,
        author=NoteAuthor.OPERATOR,
        kind=NoteKind.MANUAL,
        body=request.body,
        created_at=datetime.now(UTC),
    )
    return notes.add_note(note)

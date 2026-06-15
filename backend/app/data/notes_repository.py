"""Notes store seam — the FR-2.3 timeline repository (ASSUMPTIONS A-3).

Defines :class:`NotesRepository`, the interface the notes route depends on, plus
:class:`InMemoryNotesRepository`, the v1 local impl. The timeline is
**append-only**: notes are added and listed, never mutated or deleted (A-8 — an
auto-note is a factual historical record). ``list_notes`` returns a family's
notes in **chronological** order so manual and auto-notes interleave by time.

NFR-8 seam (mirrors ``app/data/repository.py``): the route depends only on the
interface. Production swaps a Supabase-backed implementation in at the
composition root — ``add_note`` maps to an ``INSERT``, ``list_notes`` to a
``SELECT … WHERE family_id = $1 ORDER BY created_at`` — with zero changes to the
core or the route.

Purity: plain data access. It imports the pure :class:`app.core.notes.Note`
model and nothing from ``app.ai`` / ``app.adapters``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from uuid import UUID

from app.core.notes import Note


class NotesRepository(ABC):
    """Append-only store over per-family notes (the FR-2.3 store seam).

    The notes route depends on this interface, never on a concrete store.
    Production swaps a Supabase-backed impl with zero route/core changes.
    """

    @abstractmethod
    def add_note(self, note: Note) -> Note:
        """Append a note (manual or auto) to its family's timeline; return it."""
        raise NotImplementedError

    @abstractmethod
    def list_notes(self, family_id: UUID) -> list[Note]:
        """Return a family's notes in chronological order (oldest first)."""
        raise NotImplementedError


class InMemoryNotesRepository(NotesRepository):
    """In-memory append-only notes store (A-3 pattern).

    Holds notes per ``family_id``; ``list_notes`` returns them sorted by
    ``created_at`` so the two note kinds interleave chronologically. This is the
    v1 local store; production replaces it with a Supabase-backed
    :class:`NotesRepository` behind the same interface.
    """

    def __init__(self) -> None:
        self._by_family: dict[UUID, list[Note]] = defaultdict(list)

    def add_note(self, note: Note) -> Note:
        self._by_family[note.family_id].append(note)
        return note

    def list_notes(self, family_id: UUID) -> list[Note]:
        # Sort by timestamp on read so out-of-order appends still interleave
        # chronologically. A SQL-backed store maps this to ORDER BY created_at.
        return sorted(self._by_family.get(family_id, []), key=lambda n: n.created_at)

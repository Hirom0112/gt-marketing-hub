"""LostRecord spine event + is_lost query (rep close-loop core).

The presumed-lost rule only SURFACES a family for review; a human then CONFIRMS
the LOST transition (the machine never auto-drops a warm lead). That confirmation
is an append-only spine event — the proven ``DismissRecord`` pattern (INV-2: a
logged event with a recorded reason, never a silent state mutation) — and, like
dismiss, it is reversible: a later re-stall (a fresh ``stall_date``) supersedes it
so a family that re-engages returns to the active board.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.observability.log_store import InMemoryObservabilityLog, LostRecord

FAM_A = UUID("00000000-0000-4000-8000-00000000000a")
FAM_B = UUID("00000000-0000-4000-8000-00000000000b")


def _at(day: int) -> datetime:
    return datetime(2026, 6, day, 12, 0, tzinfo=UTC)


def test_log_lost_appends_and_lists() -> None:
    """A confirmed-lost event is appended and listed back as a LostRecord."""
    log = InMemoryObservabilityLog()
    rec = log.log_lost(
        family_id=FAM_A,
        human="rep",
        reason="5 no-answer attempts, family chose another school",
        created_at=_at(10),
    )
    assert isinstance(rec, LostRecord)
    assert log.list_lost() == [rec]
    assert rec.family_id == FAM_A
    assert rec.reason.startswith("5 no-answer")


def test_is_lost_true_after_confirm() -> None:
    """is_lost holds for a family once a lost event is logged."""
    log = InMemoryObservabilityLog()
    log.log_lost(family_id=FAM_A, human="rep", reason="declined", created_at=_at(10))
    assert log.is_lost(FAM_A) is True
    # A different family is unaffected (event is family-keyed).
    assert log.is_lost(FAM_B) is False


def test_is_lost_false_when_restalled_after_supersedes() -> None:
    """A re-stall strictly after the lost event reverses it (family re-engaged)."""
    log = InMemoryObservabilityLog()
    log.log_lost(family_id=FAM_A, human="rep", reason="declined", created_at=_at(10))
    # A fresh stall_date AFTER the lost event ⇒ no longer lost (back on the board).
    assert log.is_lost(FAM_A, restalled_after=_at(15)) is False
    # A re-stall BEFORE the lost event does not supersede it.
    assert log.is_lost(FAM_A, restalled_after=_at(5)) is True


def test_log_lost_requires_reason() -> None:
    """A confirmed-lost must record WHY — a blank reason is rejected."""
    log = InMemoryObservabilityLog()
    with pytest.raises(ValueError, match="reason"):
        log.log_lost(family_id=FAM_A, human="rep", reason="   ", created_at=_at(10))


def test_latest_lost_event_wins() -> None:
    """is_lost nets against the LATEST lost event, mirroring is_dismissed."""
    log = InMemoryObservabilityLog()
    log.log_lost(family_id=FAM_A, human="rep", reason="first", created_at=_at(5))
    log.log_lost(family_id=FAM_A, human="rep", reason="second", created_at=_at(12))
    # A re-stall between the two events does NOT supersede the later one.
    assert log.is_lost(FAM_A, restalled_after=_at(8)) is True

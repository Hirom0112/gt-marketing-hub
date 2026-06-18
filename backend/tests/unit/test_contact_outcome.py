"""ContactOutcome spine event + presumed-lost policy (rep close-loop core).

A rep who CALLS/TEXTS a family had no first-class way to record the result — only
free-text notes (invisible to derivers) or an approved outbound (records a *send*,
not a *call*). So the system couldn't even count "called 3x, no answer", which the
presumed-lost rule needs. This adds a `ContactOutcome` as an append-only spine event
(the proven `DismissRecord` pattern, INV-2 — a logged event, never a silent mutation)
and the deterministic, params-homed presumed-lost policy over those events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.nurture import count_no_response, is_presumed_lost
from app.core.params import load_params
from app.observability.log_store import (
    ContactChannel,
    ContactDisposition,
    ContactOutcomeRecord,
    InMemoryObservabilityLog,
)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
# committed dials: after_attempts=5, within_days=21
_PRESUMED_LOST = load_params(EXAMPLE_PARAMS).nurture.presumed_lost

FAM_A = UUID("00000000-0000-4000-8000-00000000000a")
FAM_B = UUID("00000000-0000-4000-8000-00000000000b")


def _at(day: int) -> datetime:
    return datetime(2026, 6, day, 12, 0, tzinfo=UTC)


def _no_answer(day: int) -> ContactOutcomeRecord:
    return ContactOutcomeRecord(
        family_id=FAM_A,
        channel=ContactChannel.CALL,
        disposition=ContactDisposition.NO_ANSWER,
        human="rep",
        created_at=_at(day),
    )


def test_log_and_list_contact_outcomes_filtered_by_family() -> None:
    """A contact outcome is appended and listed back, scoped to its family."""
    log = InMemoryObservabilityLog()
    log.log_contact_outcome(
        family_id=FAM_A,
        channel=ContactChannel.CALL,
        disposition=ContactDisposition.NO_ANSWER,
        human="rep",
        created_at=_at(1),
    )
    log.log_contact_outcome(
        family_id=FAM_B,
        channel=ContactChannel.SMS,
        disposition=ContactDisposition.NO_REPLY,
        human="rep",
        created_at=_at(2),
    )

    a = log.list_contact_outcomes(FAM_A)
    assert len(a) == 1
    assert a[0].channel == ContactChannel.CALL
    assert a[0].disposition == ContactDisposition.NO_ANSWER
    # FAM_B's outcome never leaks into FAM_A's list.
    assert log.list_contact_outcomes(FAM_B)[0].family_id == FAM_B


def test_contact_outcome_captures_a_promise_date() -> None:
    """A 'committed to pay' outcome can carry a promised-by date (drives follow-up)."""
    log = InMemoryObservabilityLog()
    rec = log.log_contact_outcome(
        family_id=FAM_A,
        channel=ContactChannel.CALL,
        disposition=ContactDisposition.COMMITTED_TO_PAY,
        human="rep",
        promised_by=_at(20).date(),
        created_at=_at(5),
    )
    assert rec.promised_by == _at(20).date()


# --- presumed-lost policy (deterministic, params-homed; auto-SURFACE, human-confirm) ---
# 5 no-answer/no-reply attempts within 21 days ⇒ "presumed lost" (the committed dials).
# The policy only SUGGESTS; a human confirms (the API guard). Here we test the signal.

_NOW = _at(28)  # within_days=21 ⇒ cutoff is June 7


def test_presumed_lost_when_attempts_meet_threshold_in_window() -> None:
    """5 no-response attempts inside the window ⇒ presumed lost."""
    outcomes = [_no_answer(d) for d in (10, 12, 14, 16, 18)]
    assert count_no_response(outcomes, now=_NOW, within_days=_PRESUMED_LOST.within_days) == 5
    assert is_presumed_lost(outcomes, _PRESUMED_LOST, now=_NOW) is True


def test_not_presumed_lost_below_threshold() -> None:
    """4 attempts ⇒ not yet presumed lost (the system never auto-drops early)."""
    outcomes = [_no_answer(d) for d in (10, 12, 14, 16)]
    assert is_presumed_lost(outcomes, _PRESUMED_LOST, now=_NOW) is False


def test_attempts_outside_window_do_not_count() -> None:
    """Old attempts age out of the window — only recent silence accrues."""
    # 2 before the June-7 cutoff + 3 inside ⇒ only 3 count ⇒ not presumed lost.
    outcomes = [_no_answer(d) for d in (1, 3, 14, 16, 18)]
    assert count_no_response(outcomes, now=_NOW, within_days=_PRESUMED_LOST.within_days) == 3
    assert is_presumed_lost(outcomes, _PRESUMED_LOST, now=_NOW) is False


def test_reached_outcome_does_not_count_as_silence() -> None:
    """A live 'reached' contact is not silence — it doesn't accrue toward lost."""
    reached = ContactOutcomeRecord(
        family_id=FAM_A,
        channel=ContactChannel.CALL,
        disposition=ContactDisposition.REACHED,
        human="rep",
        created_at=_at(17),
    )
    outcomes = [_no_answer(d) for d in (10, 12, 14, 16)] + [reached]
    assert count_no_response(outcomes, now=_NOW, within_days=_PRESUMED_LOST.within_days) == 4
    assert is_presumed_lost(outcomes, _PRESUMED_LOST, now=_NOW) is False

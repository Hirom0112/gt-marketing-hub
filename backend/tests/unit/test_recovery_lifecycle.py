"""Later-lifecycle recovery states (rep close-loop): COLD / PRESUMED_LOST / LOST.

Extends the derived recovery machine with the later-lifecycle vocabulary the
cockpit lacked. The new facts (cold / presumed_lost / lost / dormant) are resolved
at the API layer and passed IN as booleans — same composition-root pattern as
``dismissed`` — so the deriver stays pure and total. They DEFAULT OFF, so every
existing caller (and the funnel-only behavior) is unchanged.

Board split (user-ratified):
  active  = {STALLED, WORKING, COLD, PRESUMED_LOST}   ← still the rep's to work
  history = {RECOVERED, DISMISSED, LOST, DORMANT}     ← closed out / parked

Precedence (document order): DISMISSED > DORMANT > LOST > RECOVERED >
PRESUMED_LOST > WORKING > COLD > STALLED.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.core.params import Params, load_params
from app.core.recovery_state import RecoveryState, derive_recovery_state, is_active
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import JoinedFamily

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
NOW = datetime(2026, 6, 15, tzinfo=UTC)


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _joined(
    *,
    current_stage: Stage = Stage.APPLY,
    funding_state: FundingState = FundingState.NONE,
) -> JoinedFamily:
    family = FamilyRecord(
        family_id=uuid4(),
        display_name="Fixture Family",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=current_stage,
        stalled_since=None,
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=NOW - timedelta(days=20),
    )
    return JoinedFamily(
        family=family, lead=None, app_form=None, enrollment_forms=None, community_profile=None
    )


def test_cold_active_when_stalled_and_uncontacted() -> None:
    """A stalled, uncontacted family flagged cold ⇒ COLD, and stays on the active board."""
    state = derive_recovery_state(
        joined=_joined(),
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
        cold=True,
    )
    assert state is RecoveryState.COLD
    assert is_active(state) is True


def test_presumed_lost_active_and_beats_working() -> None:
    """Presumed-lost (accrued silence) dominates WORKING and stays on the active board."""
    state = derive_recovery_state(
        joined=_joined(),
        last_contact_at=NOW,  # would be WORKING...
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
        presumed_lost=True,  # ...but accrued silence wins, surfaced for confirm.
    )
    assert state is RecoveryState.PRESUMED_LOST
    assert is_active(state) is True


def test_lost_is_history_and_beats_recovered() -> None:
    """A human-confirmed LOST holds over a recovered-looking signal; it's history."""
    state = derive_recovery_state(
        joined=_joined(funding_state=FundingState.FIRST_INSTALLMENT_RECEIVED),  # looks recovered
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
        lost=True,
    )
    assert state is RecoveryState.LOST
    assert is_active(state) is False


def test_dormant_is_history() -> None:
    """A dormant (long-parked) family is history, off the active board."""
    state = derive_recovery_state(
        joined=_joined(),
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
        dormant=True,
    )
    assert state is RecoveryState.DORMANT
    assert is_active(state) is False


def test_dismissed_still_beats_lost() -> None:
    """DISMISSED keeps top precedence even over a lost flag."""
    state = derive_recovery_state(
        joined=_joined(),
        last_contact_at=None,
        dismissed=True,
        stall_stage=Stage.APPLY,
        params=_params(),
        lost=True,
    )
    assert state is RecoveryState.DISMISSED


def test_defaults_off_preserve_existing_behavior() -> None:
    """With no new flags, the machine behaves exactly as before (STALLED default)."""
    state = derive_recovery_state(
        joined=_joined(),
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
    )
    assert state is RecoveryState.STALLED


def test_selected_gt_funding_does_not_crash_recovery() -> None:
    """A family at SELECTED_GT must not crash the funding-recovered check (audit bug).

    The old hand-maintained funding order omitted SELECTED_GT/RECONFIRMED, so
    `.index()` raised ValueError. SELECTED_GT is below the first-installment floor,
    so it is NOT recovered — and must derive cleanly.
    """
    state = derive_recovery_state(
        joined=_joined(funding_state=FundingState.SELECTED_GT),
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=_params(),
    )
    assert state is RecoveryState.STALLED

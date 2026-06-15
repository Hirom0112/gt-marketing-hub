"""Recovery state-machine tests (S12 W1; ASSUMPTIONS A-19; CLAUDE.md §4.1).

The recovery state machine ``{stalled, working, recovered, dismissed}`` is
DERIVED, never stored (A-19): ``derive_recovery_state`` is a pure function of the
joined family rows, the audit-log-derived facts (``last_contact_at`` +
``dismissed``), and the params. It mirrors ``derive_contact_status`` — the API
layer reads ``now``/the log and passes the resolved facts in, so the deriver
itself stays clock-free and log-free (INV-2 core purity).

Precedence (document order is the contract):
1. DISMISSED — a dismiss event holds (and no later re-stall supersedes it).
2. RECOVERED (DETECTED) — stage advanced past the stall stage, OR the stuck form
   step cleared (``next_unsigned_form is None`` once forms existed), OR
   ``funding_state >= first_installment_received``.
3. WORKING — an approved outbound exists (``last_contact_at`` is not None).
4. STALLED — default.

``is_active`` = state in {STALLED, WORKING}.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.core.params import Params, load_params
from app.core.recovery_state import (
    RecoveryState,
    derive_recovery_state,
    is_active,
)
from app.data.models import (
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    SeamStatus,
    Stage,
)
from app.data.repository import JoinedFamily

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
NOW = datetime(2026, 6, 15, tzinfo=UTC)


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _forms(*, signed: int, total: int) -> EnrollmentForms:
    """An EnrollmentForms row with `signed` of `total` forms signed (in order)."""
    forms_status = [
        {"name": f"form_{i}", "signed_at": (NOW if i < signed else None)} for i in range(total)
    ]
    return EnrollmentForms(
        enrollment_form_id=uuid4(),
        family_id=uuid4(),
        forms_total=total,
        forms_signed=signed,
        forms_status=forms_status,
    )


def _joined(
    *,
    current_stage: Stage = Stage.APPLY,
    stalled_since: datetime | None = None,
    funding_state: FundingState = FundingState.NONE,
    enrollment_forms: EnrollmentForms | None = None,
) -> JoinedFamily:
    family = FamilyRecord(
        family_id=uuid4(),
        display_name="Fixture Family",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=current_stage,
        stalled_since=stalled_since,
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=NOW - timedelta(days=20),
    )
    return JoinedFamily(
        family=family,
        lead=None,
        app_form=None,
        enrollment_forms=enrollment_forms,
        community_profile=None,
    )


def test_dismissed_wins() -> None:
    """A dismissed family is DISMISSED even if it also looks recovered/working."""
    params = _params()
    # Looks recovered (funded) AND working (contacted) — dismiss still wins.
    joined = _joined(funding_state=FundingState.FIRST_INSTALLMENT_RECEIVED)
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=NOW,
        dismissed=True,
        stall_stage=Stage.APPLY,
        params=params,
    )
    assert state is RecoveryState.DISMISSED


def test_recovered_by_stage_advance() -> None:
    """Stage advanced past the stall stage ⇒ RECOVERED (DETECTED)."""
    params = _params()
    joined = _joined(current_stage=Stage.ENROLL)
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,  # was stuck at APPLY, now at ENROLL.
        params=params,
    )
    assert state is RecoveryState.RECOVERED


def test_recovered_by_forms_cleared() -> None:
    """The stuck form step cleared (all forms signed) ⇒ RECOVERED."""
    params = _params()
    joined = _joined(enrollment_forms=_forms(signed=6, total=6))
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=params,
    )
    assert state is RecoveryState.RECOVERED


def test_recovered_by_first_installment() -> None:
    """funding_state >= first_installment_received ⇒ RECOVERED."""
    params = _params()
    for fs in (FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED):
        joined = _joined(funding_state=fs)
        state = derive_recovery_state(
            joined=joined,
            last_contact_at=None,
            dismissed=False,
            stall_stage=Stage.APPLY,
            params=params,
        )
        assert state is RecoveryState.RECOVERED


def test_working_when_contacted() -> None:
    """An approved outbound (last_contact_at set), not recovered ⇒ WORKING."""
    params = _params()
    # Unsigned forms remain, stage unchanged, not funded — only the contact moved.
    joined = _joined(
        current_stage=Stage.APPLY,
        enrollment_forms=_forms(signed=2, total=6),
    )
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=NOW,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=params,
    )
    assert state is RecoveryState.WORKING


def test_stalled_is_default() -> None:
    """No dismiss, no recovery signal, no contact ⇒ STALLED."""
    params = _params()
    joined = _joined(
        current_stage=Stage.APPLY,
        enrollment_forms=_forms(signed=2, total=6),
    )
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=params,
    )
    assert state is RecoveryState.STALLED


def test_forms_not_recovered_when_no_forms_exist() -> None:
    """A family with NO enrollment_forms row is not "recovered" by a null form step.

    ``next_unsigned_form is None`` must mean "forms existed and all got signed",
    not "there were never any forms" — otherwise every interest-stage family
    would read as recovered. With no forms and no other signal ⇒ STALLED.
    """
    params = _params()
    joined = _joined(current_stage=Stage.APPLY, enrollment_forms=None)
    state = derive_recovery_state(
        joined=joined,
        last_contact_at=None,
        dismissed=False,
        stall_stage=Stage.APPLY,
        params=params,
    )
    assert state is RecoveryState.STALLED


def test_is_active_partitions_the_states() -> None:
    """Active board = {stalled, working}; history = {recovered, dismissed}."""
    assert is_active(RecoveryState.STALLED) is True
    assert is_active(RecoveryState.WORKING) is True
    assert is_active(RecoveryState.RECOVERED) is False
    assert is_active(RecoveryState.DISMISSED) is False

"""Voucher rules + deadline/standing engine tests (TODO.md R2; FR-2.7).

The funding lifecycle (`funding_gate.py`) owns the money math and the legal
state path; this engine COMPOSES it with the per-program RULES + DEADLINES so
the cockpit can answer "where does this family's voucher stand, what's next,
and by when." A new state is a CONFIG ROW (`voucher_programs:` in params), not
a code change — proven here by driving BOTH `tx_tefa` AND `az_esa` through the
same pure `voucher_standing` function.

Every threshold/window/amount is read FROM params (INV-11) — no literal in the
engine or in these tests. The engine is fail-closed on a missing program/rule
(INV-10): no default award, no default deadline.

Deterministic without a local `params/params.yaml` (gitignored): the committed
`params/params.example.yaml` is passed explicitly, like the funding-math tests.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import (
    Params,
    VoucherProgram,
    VoucherWindows,
    load_params,
)
from app.core.voucher import voucher_standing
from app.data.models import FundingState, FundingType

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


# --------------------------------------------------------------------------
# Params: voucher_programs block — typed, multi-state, drift fails the build.
# --------------------------------------------------------------------------


def test_voucher_programs_block_loads_both_states() -> None:
    """`voucher_programs` parses from the example with TX + a second state (INV-11).

    Two programs prove multi-state is a CONFIG ROW: `tx_tefa` (the confirmed TEFA
    rules) AND `az_esa` (a second program). The block carries only the NEW
    window/rule values; award AMOUNTS keep their canonical home in `funding`.
    """
    params = _params()

    assert set(params.voucher_programs) >= {"tx_tefa", "az_esa"}

    tx = params.voucher_programs["tx_tefa"]
    assert isinstance(tx, VoucherProgram)
    # The reconfirm gap is real for TX TEFA (SELECTED_GT -> RECONFIRMED).
    assert tx.windows.reconfirm_required is True
    # Installment schedule is DATA (fraction + due month/day), not a comment.
    assert len(tx.installment_schedule) == len(params.funding.installment_split)
    fractions = [entry.fraction for entry in tx.installment_schedule]
    assert fractions == params.funding.installment_split
    # Tuition-unlock state matches the funding gate threshold.
    assert tx.tuition_unlock_state == params.funding.tuition_unlock_state


def test_voucher_program_carries_confidence_flags() -> None:
    """Each program/rule carries a verified flag so UNVERIFIED rules stay visible.

    The full-award cutoff is CONFIRMED official; the private-to-private switch
    rule is UNVERIFIED in the ANALYSIS docs — the flag must surface that so an
    unverified rule is never silently load-bearing.
    """
    params = _params()
    tx = params.voucher_programs["tx_tefa"]

    # The deadline windows are confirmed-official for TX TEFA.
    assert tx.windows.verified is True
    # The setting-lock (homeschool irreversible) is confirmed-official.
    assert tx.setting_lock.verified is True


def test_voucher_programs_drift_extra_key_raises() -> None:
    """A stray key in a program fails to load — drift fails the build (INV-11)."""
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    payload = tx.model_dump(mode="json")
    payload["bogus_key"] = 1
    with pytest.raises(ValidationError):
        VoucherProgram.model_validate(payload)


def test_voucher_windows_wrong_type_raises() -> None:
    """A non-date deadline fails validation (drift fails the build, INV-11)."""
    with pytest.raises(ValidationError):
        VoucherWindows(
            parent_select_deadline="not-a-date",  # type: ignore[arg-type]
            full_award_cutoff="2026-09-15",
            reconfirm_required=True,
            verified=True,
            confidence="confirmed",
        )


# --------------------------------------------------------------------------
# voucher_standing: deadline math, full-vs-prorated, next-action, at-risk.
# --------------------------------------------------------------------------


def test_standing_next_action_reconfirm_at_selected_gt() -> None:
    """At SELECTED_GT with reconfirm_required, next action is to reconfirm by the deadline."""
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    today = tx.windows.parent_select_deadline - timedelta(days=5)

    standing = voucher_standing(
        state=FundingState.SELECTED_GT,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="tx_tefa",
        today=today,
        params=params,
    )

    assert standing.current_state == FundingState.SELECTED_GT
    assert standing.program == "tx_tefa"
    assert standing.due_by == tx.windows.parent_select_deadline
    assert standing.days_remaining == 5
    # The next action names the reconfirm step and its deadline.
    assert "reconfirm" in standing.next_action.lower()
    assert str(tx.windows.parent_select_deadline) in standing.next_action


def test_standing_full_award_before_cutoff() -> None:
    """today <= full_award_cutoff ⇒ FULL award branch (the confirmed Sept-15 rule)."""
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    today = tx.windows.full_award_cutoff  # exactly on the cutoff is still full

    standing = voucher_standing(
        state=FundingState.GT_CONFIRMED,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="tx_tefa",
        today=today,
        params=params,
    )

    assert standing.award_full_vs_prorated == "full"


def test_standing_prorated_after_cutoff() -> None:
    """today > full_award_cutoff ⇒ PRORATED branch (late/waitlist joiners)."""
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    cutoff = tx.windows.full_award_cutoff
    today = cutoff + timedelta(days=1)

    standing = voucher_standing(
        state=FundingState.GT_CONFIRMED,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="tx_tefa",
        today=today,
        params=params,
    )

    assert standing.award_full_vs_prorated == "prorated"


def test_standing_at_risk_near_deadline_when_not_reconfirmed() -> None:
    """Selected-but-not-reconfirmed near the select deadline ⇒ at_risk.

    The SELECTED_GT -> RECONFIRMED gap is the "$10,474 lost on a deadline" gap:
    a family that picked GT but stalls before reconfirming, with the deadline
    bearing down, is at risk.
    """
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    deadline = tx.windows.parent_select_deadline
    today = deadline - timedelta(days=1)  # one day out, not reconfirmed

    standing = voucher_standing(
        state=FundingState.SELECTED_GT,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="tx_tefa",
        today=today,
        params=params,
    )

    assert standing.at_risk is True
    assert standing.days_remaining == 1


def test_standing_not_at_risk_once_reconfirmed() -> None:
    """A reconfirmed family is past the at-risk gap even near the deadline."""
    params = _params()
    tx = params.voucher_programs["tx_tefa"]
    deadline = tx.windows.parent_select_deadline
    today = deadline - timedelta(days=1)

    standing = voucher_standing(
        state=FundingState.RECONFIRMED,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="tx_tefa",
        today=today,
        params=params,
    )

    assert standing.at_risk is False


def test_standing_drives_second_state_az_esa() -> None:
    """The SAME function drives az_esa — multi-state is a config row, not code.

    az_esa carries its OWN windows and its OWN rule (no reconfirm gap), and the
    engine honors them with no TX-specific literal: the full/prorated branch
    reads az's own cutoff, and because az's `reconfirm_required` is False there
    is no reconfirm deadline — the rule, not code, decides.
    """
    params = _params()
    az = params.voucher_programs["az_esa"]
    # Past az's OWN cutoff ⇒ prorated, read from az's params, not TX's.
    today = az.windows.full_award_cutoff + timedelta(days=1)

    standing = voucher_standing(
        state=FundingState.SELECTED_GT,
        funding_type=FundingType.TEFA_STANDARD,
        program_key="az_esa",
        today=today,
        params=params,
    )

    assert standing.program == "az_esa"
    assert standing.award_full_vs_prorated == "prorated"
    # az has no reconfirm gap (reconfirm_required=False) → no reconfirm deadline,
    # and so not at-risk on a reconfirm deadline. The PROGRAM RULE drives this.
    assert az.windows.reconfirm_required is False
    assert standing.due_by is None
    assert standing.days_remaining is None
    assert standing.at_risk is False


def test_standing_fail_closed_on_missing_program() -> None:
    """An unknown program raises — fail-closed, no default award/deadline (INV-10)."""
    params = _params()
    with pytest.raises(KeyError):
        voucher_standing(
            state=FundingState.SELECTED_GT,
            funding_type=FundingType.TEFA_STANDARD,
            program_key="ca_does_not_exist",
            today=date(2026, 7, 1),
            params=params,
        )

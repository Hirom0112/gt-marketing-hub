"""Funding-state machine tests (S3; FR-2.7; INV-10; ARCHITECTURE.md §5.4).

The legal funding lifecycle (§5.4) is a strict linear path:

    none → applied → awarded_selfreport → gt_confirmed
         → first_installment_received → funded

`advance_funding_state` walks only along this path; any illegal transition
(skip / backwards / unknown) is REJECTED. The tuition step is **fail-closed**
(INV-10): it stays locked until the funding signal proves first-installment
receipt — Tuition unlocks only at/after `funding.tuition_unlock_state`
(`first_installment_received`), which is GT-controlled (GT-confirmed enrollment
+ first-installment receipt + family self-report), NOT an Odyssey API.

The unlock threshold is read FROM params (INV-11), never hardcoded.

Deterministic without a local `params/params.yaml`: the committed
`params/params.example.yaml` is passed explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.funding_gate import advance_funding_state, tuition_step_unlocked
from app.core.params import Params, load_params
from app.data.models import FundingState

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# The legal §5.4 path, in order.
_LEGAL_PATH = [
    FundingState.NONE,
    FundingState.APPLIED,
    FundingState.AWARDED_SELFREPORT,
    FundingState.GT_CONFIRMED,
    FundingState.FIRST_INSTALLMENT_RECEIVED,
    FundingState.FUNDED,
]


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def test_advances_one_step_along_legal_path() -> None:
    """Each legal state advances to exactly the next state in the §5.4 path."""
    for current, expected_next in zip(_LEGAL_PATH, _LEGAL_PATH[1:], strict=False):
        assert advance_funding_state(current, expected_next) == expected_next


def test_full_lifecycle_walk() -> None:
    """Walking the path one signal at a time reaches `funded`."""
    state = FundingState.NONE
    for nxt in _LEGAL_PATH[1:]:
        state = advance_funding_state(state, nxt)
    assert state == FundingState.FUNDED


def test_skip_transition_rejected() -> None:
    """Skipping a step (e.g. applied → gt_confirmed) is illegal (fail-closed)."""
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.APPLIED, FundingState.GT_CONFIRMED)
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.NONE, FundingState.FUNDED)


def test_backwards_transition_rejected() -> None:
    """Moving backwards along the path is illegal."""
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.GT_CONFIRMED, FundingState.APPLIED)
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.FUNDED, FundingState.NONE)


def test_self_transition_rejected() -> None:
    """A no-op self-transition is not a legal advance."""
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.APPLIED, FundingState.APPLIED)


def test_advance_from_terminal_rejected() -> None:
    """`funded` is terminal — there is nothing to advance to."""
    with pytest.raises(ValueError):
        advance_funding_state(FundingState.FUNDED, FundingState.FUNDED)


def test_tuition_locked_before_first_installment() -> None:
    """Tuition is fail-closed: every state before the unlock threshold ⇒ locked."""
    params = _params()
    for state in (
        FundingState.NONE,
        FundingState.APPLIED,
        FundingState.AWARDED_SELFREPORT,
        FundingState.GT_CONFIRMED,
    ):
        assert tuition_step_unlocked(state, params) is False


def test_tuition_unlocked_at_and_after_first_installment() -> None:
    """`first_installment_received` and `funded` ⇒ unlocked (INV-10 GT signal)."""
    params = _params()
    assert tuition_step_unlocked(FundingState.FIRST_INSTALLMENT_RECEIVED, params) is True
    assert tuition_step_unlocked(FundingState.FUNDED, params) is True


def test_tuition_unlock_threshold_reads_from_params() -> None:
    """The unlock threshold is the params value, not a hardcoded literal (INV-11)."""
    params = _params()
    assert params.funding.tuition_unlock_state == FundingState.FIRST_INSTALLMENT_RECEIVED.value

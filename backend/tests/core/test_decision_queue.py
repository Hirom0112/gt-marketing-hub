"""Decision-Queue state-machine + can_decide tests (B2; CLAUDE.md §4.1, INV-2/INV-11).

The Decision Queue is a generic, cross-module HUMAN decision lane (NOT LLM-proposal
approval). A decision moves through ``OPEN → DECIDED → IN_FLIGHT`` via guarded
actions (``approve`` / ``reject`` / ``need_info``); ``need_info`` keeps it OPEN and
REQUIRES a non-empty comment. Illegal transitions raise ``ValueError`` (fail-closed,
like ``advance_funding_state``). ``can_decide`` reuses ``core/authz.permits`` against
the new ``decision_queue.decide`` permission — held by admin+leader, NOT operator;
the test reads it from params so a drift fails the build (INV-11).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.decision_queue import (
    DecisionAction,
    DecisionState,
    apply_action,
    can_decide,
)
from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_open_approve_to_decided() -> None:
    """OPEN --approve--> DECIDED."""
    assert apply_action(DecisionState.OPEN, DecisionAction.APPROVE, comment=None) is (
        DecisionState.DECIDED
    )


def test_open_reject_to_decided() -> None:
    """OPEN --reject--> DECIDED."""
    assert apply_action(DecisionState.OPEN, DecisionAction.REJECT, comment=None) is (
        DecisionState.DECIDED
    )


def test_open_need_info_with_comment_stays_open() -> None:
    """OPEN --need_info(comment=...)--> OPEN (decision stays open)."""
    assert (
        apply_action(DecisionState.OPEN, DecisionAction.NEED_INFO, comment="please attach the W-2")
        is DecisionState.OPEN
    )


def test_need_info_requires_non_empty_comment() -> None:
    """need_info with no/empty/blank comment raises (comment is mandatory)."""
    for bad in (None, "", "   "):
        with pytest.raises(ValueError):
            apply_action(DecisionState.OPEN, DecisionAction.NEED_INFO, comment=bad)


def test_decided_approve_executes_to_in_flight() -> None:
    """A DECIDED decision that gets actioned-on/executed → IN_FLIGHT."""
    assert apply_action(DecisionState.DECIDED, DecisionAction.APPROVE, comment=None) is (
        DecisionState.IN_FLIGHT
    )


def test_illegal_transition_raises() -> None:
    """Illegal transitions are rejected, fail-closed (like advance_funding_state)."""
    # rejecting an already-DECIDED decision is not a legal move
    with pytest.raises(ValueError):
        apply_action(DecisionState.DECIDED, DecisionAction.REJECT, comment=None)
    # need_info on a DECIDED decision is not legal
    with pytest.raises(ValueError):
        apply_action(DecisionState.DECIDED, DecisionAction.NEED_INFO, comment="x")
    # IN_FLIGHT is terminal — no action advances it
    with pytest.raises(ValueError):
        apply_action(DecisionState.IN_FLIGHT, DecisionAction.APPROVE, comment=None)


def test_leader_can_decide() -> None:
    """A leader may decide (holds decision_queue.decide)."""
    params = load_params(EXAMPLE_PARAMS)
    assert can_decide("leader", params=params) is True


def test_admin_can_decide() -> None:
    """An admin may decide (holds decision_queue.decide)."""
    params = load_params(EXAMPLE_PARAMS)
    assert can_decide("admin", params=params) is True


def test_operator_cannot_decide() -> None:
    """An operator may NOT decide (lacks decision_queue.decide)."""
    params = load_params(EXAMPLE_PARAMS)
    assert can_decide("operator", params=params) is False

"""Decision-Queue state machine + ``can_decide`` (B2; INV-2/INV-11).

A generic, cross-module HUMAN decision lane — NOT LLM-proposal approval. A
decision moves through ``OPEN → DECIDED → IN_FLIGHT`` (PLAN_v2 §B2
``open→decided→in_flight``) via guarded actions:

  - ``OPEN --approve--> DECIDED``   (a leader/admin decides the question yes)
  - ``OPEN --reject--> DECIDED``    (a leader/admin decides the question no)
  - ``OPEN --need_info--> OPEN``    (more info needed; stays open — REQUIRES a
                                     non-empty comment)
  - ``DECIDED --approve--> IN_FLIGHT`` (the decided outcome is actioned-on /
                                     executed downstream)

Every other (state, action) pair is illegal and raises ``ValueError`` —
fail-closed, exactly like :func:`~app.core.funding_gate.advance_funding_state`.
``IN_FLIGHT`` is terminal. ``apply_action`` is the one pure transition; the
store/persistence is a LATER unit and is deliberately NOT modeled here.

``can_decide`` reuses :func:`app.core.authz.permits` against the
``decision_queue.decide`` permission (held by admin+leader, NOT operator) — authz
is decided in one place, never re-implemented here.

This module is part of the deterministic core and stays pure: it imports only
typed params and the authz predicate (core→core is fine) and does no I/O — no
repository, adapter, or httpx import (the core-purity test guards this; INV-2).
"""

from __future__ import annotations

from enum import StrEnum

from app.core.authz import permits
from app.core.params import Params

# The permission a role must hold to act on the decision queue (admin+leader).
# Single canonical home in params.rbac.permissions (INV-11).
_DECIDE_PERMISSION = "decision_queue.decide"


class DecisionState(StrEnum):
    """A decision's lifecycle state (PLAN_v2 §B2 ``open→decided→in_flight``)."""

    OPEN = "open"
    DECIDED = "decided"
    IN_FLIGHT = "in_flight"


class DecisionAction(StrEnum):
    """A human action taken on a decision."""

    APPROVE = "approve"
    REJECT = "reject"
    NEED_INFO = "need_info"


# The minimal legal transition table: (state, action) → next state. Any pair
# absent from this map is an illegal transition and is rejected (fail-closed).
_TRANSITIONS: dict[tuple[DecisionState, DecisionAction], DecisionState] = {
    (DecisionState.OPEN, DecisionAction.APPROVE): DecisionState.DECIDED,
    (DecisionState.OPEN, DecisionAction.REJECT): DecisionState.DECIDED,
    (DecisionState.OPEN, DecisionAction.NEED_INFO): DecisionState.OPEN,
    # A decided outcome that gets actioned-on/executed downstream goes in-flight.
    (DecisionState.DECIDED, DecisionAction.APPROVE): DecisionState.IN_FLIGHT,
}


def apply_action(
    state: DecisionState, action: DecisionAction, *, comment: str | None
) -> DecisionState:
    """Apply ``action`` to a decision in ``state`` and return the next state.

    Pure transition over the minimal legal table. ``need_info`` REQUIRES a
    non-empty (non-blank) ``comment`` — the reviewer must say what is missing —
    and keeps the decision ``OPEN``. Any (state, action) pair not in the legal
    table is rejected — the machine is fail-closed (INV-2), exactly like
    :func:`~app.core.funding_gate.advance_funding_state`.

    Args:
        state: The decision's present state.
        action: The human action being taken.
        comment: Reviewer comment; mandatory and non-blank for ``need_info``,
            ignored otherwise.

    Returns:
        The next :class:`DecisionState`.

    Raises:
        ValueError: on an illegal (state, action) pair, or a ``need_info`` action
            with no/empty/blank comment.
    """
    if action is DecisionAction.NEED_INFO and not (comment and comment.strip()):
        raise ValueError("need_info requires a non-empty comment")

    next_state = _TRANSITIONS.get((state, action))
    if next_state is None:
        raise ValueError(f"illegal decision transition {state!r} --{action!r}-->")
    return next_state


def can_decide(role: str, *, params: Params) -> bool:
    """Whether ``role`` may act on the decision queue (B2; INV-2).

    Delegates to :func:`app.core.authz.permits` against the
    ``decision_queue.decide`` permission — authz is decided in one place. Held by
    admin+leader; an operator (or any unknown role) is denied — fail-closed.

    Args:
        role: The actor's role token (e.g. ``"admin"`` / ``"leader"`` /
            ``"operator"``).
        params: Loaded params (§8); supplies the ``rbac`` matrix (INV-11).

    Returns:
        ``True`` iff ``role`` holds ``decision_queue.decide``.
    """
    return permits(role, _DECIDE_PERMISSION, params=params)

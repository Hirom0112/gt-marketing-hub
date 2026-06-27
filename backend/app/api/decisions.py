"""Decision-Queue endpoints — leader-gated view/decide + open submit (B2).

The composition layer that wires the B2 decision core (``app.core.decision_queue``)
and the store seam (``app.data.decisions_store``) behind REST. Thin by design: the
state machine (:func:`apply_action`) is pure/owned core (INV-2); this router only
orchestrates, gates by role, maps HTTP errors, and LOGS every decided action to the
§10 observability spine (NFR-6).

  ``GET  /decisions``
    The open queue — every OPEN decision for the active program. **Leader-gated**
    (``leader``/``admin``); an operator is 403.

  ``POST /decisions``
    Submit a decision — open to ANY authenticated principal (any module / anyone
    may flag an item). Inserts an OPEN decision and returns it.

  ``POST /decisions/{id}/action``
    Decide — ``approve`` / ``reject`` / ``need_info`` (+ optional ``comment``).
    **Leader-gated**. Loads the decision (404 if absent), computes the next state via
    :func:`apply_action` (422 on an illegal transition / a ``need_info`` with no
    comment), records the action (append a ``decision_event`` + advance ``state``),
    LOGS the proposal + the human decision (NFR-6), and returns the updated decision.

``flag_decision`` is the thin module-level feeder other modules (B4/Nurture/Field)
call to enqueue — it wraps ``store.submit`` so callers never touch the store shape.

The leader gate is bound at MODULE level (``_DECIDE_GUARD``) so FastAPI resolves it
from the route's PEP-563 string annotation; a closure-local guard would be invisible
to ``get_type_hints`` and the route param would degrade to a 422-y query param.

This module may import ``app.core`` / ``app.observability`` (it is the composition
root); ``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    Principal,
    get_active_program,
    get_decisions_store,
    get_observability_log,
    get_principal,
    require_role,
)
from app.core.decision_queue import DecisionAction, DecisionState, apply_action
from app.core.program import Program
from app.data.decisions_store import Decision, DecisionsStore
from app.observability.log_store import DecisionAction as ObsDecisionAction
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["decisions"])

# The leader/admin guard, built ONCE at MODULE level so FastAPI can resolve it from
# the route's (string, PEP 563) annotation — a closure-local guard is invisible to
# `get_type_hints` and the route param would degrade to a query param (then 422).
_DECIDE_GUARD = require_role("leader", "admin")

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/seam.py).
StoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
# The leader-gated principal — resolves the verified principal AND enforces the gate.
LeaderDep = Annotated[Principal, Depends(_DECIDE_GUARD)]
# Any authenticated principal (the open-submit path — NOT role-gated).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# The audited §10 flow tag + schema version for a decision action (the audit head).
DECISION_FLOW = "decision_queue"
DECISION_SCHEMA_VERSION = "1"

# Map the B2 human action onto the observability spine's verdict vocabulary (which
# is APPROVE/EDIT/DISCARD): approve→APPROVE, reject→DISCARD, need_info→EDIT (more
# info requested ≈ an edit-back). The audit payload always carries the EXACT B2
# action + comment too, so nothing is lost in the mapping.
_OBS_ACTION: dict[DecisionAction, ObsDecisionAction] = {
    DecisionAction.APPROVE: ObsDecisionAction.APPROVE,
    DecisionAction.REJECT: ObsDecisionAction.DISCARD,
    DecisionAction.NEED_INFO: ObsDecisionAction.EDIT,
}


class DecisionResponse(BaseModel):
    """One Decision-Queue row over the wire (the API shape of :class:`Decision`)."""

    id: UUID
    source: str
    payload: dict[str, Any]
    state: DecisionState

    @classmethod
    def of(cls, decision: Decision) -> DecisionResponse:
        """Project a stored :class:`Decision` onto the response shape."""
        return cls(
            id=decision.id,
            source=decision.source,
            payload=decision.payload,
            state=decision.state,
        )


class SubmitRequest(BaseModel):
    """Body for ``POST /decisions`` — flag an item for a human (the open-submit path)."""

    source: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    """Body for ``POST /decisions/{id}/action`` — the human verdict (+ optional comment)."""

    action: DecisionAction
    comment: str | None = None


@router.get("/decisions", response_model=list[DecisionResponse])
def list_open_decisions(
    store: StoreDep,
    program: ProgramDep,
    principal: LeaderDep,
) -> list[DecisionResponse]:
    """List the OPEN decisions for the active program (leader-gated; operator → 403)."""
    return [DecisionResponse.of(d) for d in store.list_open(program)]


@router.post("/decisions", response_model=DecisionResponse)
def submit_decision(
    body: SubmitRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Submit an OPEN decision — open to ANY authenticated principal (anyone flags)."""
    decision = store.submit(program, source=body.source, payload=body.payload)
    return DecisionResponse.of(decision)


@router.post("/decisions/{decision_id}/action", response_model=DecisionResponse)
def act_on_decision(
    decision_id: UUID,
    body: ActionRequest,
    store: StoreDep,
    program: ProgramDep,
    log: LogDep,
    principal: LeaderDep,
) -> DecisionResponse:
    """Decide on a queued item — leader-gated; logged to the §10 spine (NFR-6).

    404 on an unknown decision. The next state is computed by the pure
    :func:`apply_action` (422 on an illegal transition or a ``need_info`` with no
    comment — fail-closed, INV-2). The action is recorded (append a
    ``decision_event`` + advance ``state``) and the proposal + human decision are
    LOGGED with the actor taken from the VERIFIED principal — never a client claim.
    """
    current = store.get(program, decision_id)
    if current is None:
        raise HTTPException(status_code=404, detail="decision not found")

    try:
        new_state = apply_action(current.state, body.action, comment=body.comment)
    except ValueError as exc:
        # An illegal transition or a need_info without a comment — fail-closed (INV-2).
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    actor = str(principal.user_id) if principal.user_id is not None else principal.role
    store.record_action(
        program,
        decision_id,
        action=body.action,
        comment=body.comment,
        actor=actor,
        new_state=new_state,
    )

    # LOG the human-gated decision (NFR-6): the proposal, then the decision. The audit
    # payload carries the EXACT B2 action + comment + the resulting state; the spine's
    # own verdict enum is the mapped APPROVE/EDIT/DISCARD (see `_OBS_ACTION`).
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=DECISION_FLOW,
        schema_version=DECISION_SCHEMA_VERSION,
        payload={
            "decision_id": str(decision_id),
            "source": current.source,
            "action": body.action.value,
            "comment": body.comment,
            "new_state": new_state.value,
        },
    )
    log.log_decision(
        proposal_id=proposal_id,
        human=actor,
        action=_OBS_ACTION[body.action],
    )

    updated = store.get(program, decision_id)
    # The row was just written; fall back to a locally-advanced projection defensively.
    if updated is None:
        updated = Decision(
            id=current.id,
            source=current.source,
            payload=current.payload,
            state=new_state,
            created_at=current.created_at,
        )
    return DecisionResponse.of(updated)


def flag_decision(
    store: DecisionsStore,
    program: Program,
    *,
    source: str,
    payload: dict[str, Any],
) -> Decision:
    """Enqueue an OPEN decision — the shared feeder B4/Nurture/Field call (B2).

    A thin wrapper over :meth:`DecisionsStore.submit` so other modules enqueue a
    human decision without touching the store/route shape. Returns the created open
    :class:`Decision`.
    """
    return store.submit(program, source=source, payload=payload)

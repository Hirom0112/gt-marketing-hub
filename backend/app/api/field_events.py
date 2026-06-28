"""Field & Events endpoints — the Field-Marketing → Decision-Queue proposal feeder (B2).

The composition layer that wires the Field & Events surface into the B2 Decision Queue
(``app.api.decisions.flag_decision``). Spec Module 11: "event proposals from the Field
Marketing module (the Field & Events Owner's priority recommendations)" land as OPEN
leadership decisions. Thin by design — it only validates the proposal, enqueues ONE open
``field_event_proposal`` decision on the ``field_events`` workstream, and stamps
``raised_by`` from the VERIFIED principal (never the body — the IDOR/spoof posture,
INV-1). No live external send is ever made here.

  ``POST /field/events/proposal``
    Propose an event/priority — open to ANY authenticated principal (the Field & Events
    Owner, or anyone on the field team). Accepts a structured proposal (``name`` /
    ``recommendation`` / optional ``budget_ask`` / ``due_date`` / ``priority``) and
    inserts an OPEN decision with ``workstream="field_events"``, returning it.

This module may import ``app.api`` (it is the composition root); ``app/core/`` stays
pure. Mirrors the budget feeder's posture (``app.api.budget``).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.decisions import (
    FIELD_EVENT_SOURCE,
    DecisionResponse,
    _actor_token,
    flag_decision,
)
from app.api.deps import (
    Principal,
    get_active_program,
    get_decisions_store,
    get_principal,
)
from app.core.program import Program
from app.data.decisions_store import PRIORITIES, PRIORITY_NORMAL, DecisionsStore

router = APIRouter(tags=["field_events"])

# The workstream every Field & Events proposal belongs to (one of the canonical
# decisions_store.WORKSTREAMS). Named, not a bare literal (INV-11).
FIELD_EVENTS_WORKSTREAM = "field_events"

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/decisions.py).
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
# Any authenticated principal (the open-propose path — NOT role-gated; the Field &
# Events Owner / field team flags an item, like the open-submit decision path).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]


class EventProposalRequest(BaseModel):
    """Body for ``POST /field/events/proposal`` — a Field & Events priority recommendation.

    There is DELIBERATELY no ``raised_by`` field: the route stamps it from the VERIFIED
    principal, never from the body (the IDOR/spoof posture, INV-1).
    """

    name: str = Field(min_length=1)
    recommendation: str = ""
    budget_ask: float | None = None
    due_date: date | None = None
    priority: str = PRIORITY_NORMAL


@router.post("/field/events/proposal", response_model=DecisionResponse)
def propose_event(
    body: EventProposalRequest,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Land a Field & Events proposal as an OPEN leadership decision (Module 11).

    Open to ANY authenticated principal (the Field & Events Owner / field team). Enqueues
    ONE open ``field_event_proposal`` decision on the ``field_events`` workstream via the
    B2 feeder, carrying the proposal (``name`` as the question, plus the recommendation /
    budget ask / due date / priority as first-class fields). ``raised_by`` is STAMPED from
    the verified principal — never the body (INV-1). ``priority`` must be one of
    :data:`PRIORITIES` — an unknown value is a clean 422 (fail-closed, INV-2).
    """
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}",
        )
    decision = flag_decision(
        decisions_store,
        program,
        source=FIELD_EVENT_SOURCE,
        payload={"name": body.name},
        question=f"Approve event proposal: {body.name}",
        raised_by=_actor_token(principal),
        workstream=FIELD_EVENTS_WORKSTREAM,
        recommendation=body.recommendation,
        budget_ask=body.budget_ask,
        due_date=body.due_date,
        priority=body.priority,
    )
    return DecisionResponse.of(decision)

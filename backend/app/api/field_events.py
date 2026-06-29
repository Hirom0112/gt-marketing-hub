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

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
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
    get_field_events_store,
    get_grassroots_store,
    get_params,
    get_principal,
)
from app.core import field_events as core
from app.core.params import Params
from app.core.program import Program
from app.data.decisions_store import PRIORITIES, PRIORITY_NORMAL, DecisionsStore
from app.data.field_events_store import FieldEvent, FieldEventsStore
from app.data.grassroots_store import GrassrootsStore

router = APIRouter(tags=["field_events"])

# The workstream every Field & Events proposal belongs to (one of the canonical
# decisions_store.WORKSTREAMS). Named, not a bare literal (INV-11).
FIELD_EVENTS_WORKSTREAM = "field_events"

# The owner-routing token an OPERATOR must own to WRITE a field event (distinct from the
# decision-queue workstream above). A LEADER/ADMIN may write any; a foreign operator is
# 403. Named wire tokens, not tunables (INV-11 carve-out, mirroring grassroots/camp).
FIELD_EVENTS_OWNER_WORKSTREAM = "events"
DEMO_OPERATOR_WORKSTREAM = "events"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# The source tags the two calendar blend distinguishes (fixed wire tokens, INV-11
# carve-out, like decisions.FIELD_EVENT_SOURCE).
SOURCE_FIELD = "field"
SOURCE_AMBASSADOR = "ambassador"

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/decisions.py).
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
StoreDep = Annotated[FieldEventsStore, Depends(get_field_events_store)]
GrassrootsStoreDep = Annotated[GrassrootsStore, Depends(get_grassroots_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
# Any authenticated principal (the open-propose path — NOT role-gated; the Field &
# Events Owner / field team flags an item, like the open-submit decision path).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]


# ===========================================================================
# Owner gate — an OPERATOR may write only when they OWN the 'events' workstream; a
# LEADER/ADMIN may write anything; everyone else is 403. The verified ROLE decides —
# never a client claim (the IDOR/spoof posture). Mirrors app.api.grassroots / summer.
# ===========================================================================
def _operator_workstream(principal: Principal) -> str:
    """The single workstream an OPERATOR owns (keyed by the verified agent_id only).

    An operator with no mapped/resolvable agent_id falls back to the demo-owned
    workstream (:data:`DEMO_OPERATOR_WORKSTREAM`). Derived from the verified principal
    only (INV-1) — never a client claim.
    """
    if principal.agent_id is not None:
        return OPERATOR_WORKSTREAMS.get(str(principal.agent_id), DEMO_OPERATOR_WORKSTREAM)
    return DEMO_OPERATOR_WORKSTREAM


def _require_field_events_owner(principal: Principal) -> None:
    """OWNER gate for every Field & Events write — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the
    workstream they own is ``events``; a foreign operator is 403. The verified ROLE
    decides — never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    # role == "operator" (the only remaining verified role).
    if _operator_workstream(principal) != FIELD_EVENTS_OWNER_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {FIELD_EVENTS_OWNER_WORKSTREAM!r} workstream",
        )


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


# ===========================================================================
# Wire models (8a overview / tracker rows / calendar blend / write bodies).
# ===========================================================================
class FieldEventRow(BaseModel):
    """One GT-organized field event over the wire (the tracker + create/update result)."""

    event_id: UUID
    event_name: str
    event_type: str
    venue: str
    event_date: date
    rsvp_count: int
    attendance_count: int
    consults_booked: int
    status: str
    owner: str
    notes: str
    materials: str
    budget_usd: int


class OverviewResponse(BaseModel):
    """The 8a Field & Events overview rollup (every figure computed, never faked)."""

    upcoming_count: int
    completed_this_month: int
    total_rsvps: int
    total_attendance: int
    rsvp_to_attendance_pct: int
    consults_booked_total: int
    event_to_consult_pct: int
    # HONESTY: the event→consult conversion is computed from a MANUALLY-entered field
    # (consults_booked), NOT auto-instrumented — surfaced so the UI never implies tracking.
    event_to_consult_manual: bool
    top_event_type_by_attendance: dict[str, object] | None


class CalendarItem(BaseModel):
    """One blended calendar item — a GT field event OR a read-only ambassador event.

    ``source`` tags the origin (``field`` ⇒ this module's ``field_event``; ``ambassador``
    ⇒ the grassroots ``ambassador_event``, surfaced READ-ONLY). ``read_only`` is True for
    ambassador items (Module 8 never writes them) and False for field items. ``status`` is
    the field event's lifecycle status; ``None`` for ambassador items (they carry none).
    """

    source: str
    event_id: UUID
    event_name: str
    event_type: str
    event_date: date
    venue: str
    status: str | None
    read_only: bool


class FieldEventCreateRequest(BaseModel):
    """Body for ``POST /field/events`` — create a GT-organized field event.

    There is DELIBERATELY no ``owner`` field: writes are owner-gated by the verified
    principal and the row is stamped with the field-events owner token (INV-1).
    """

    event_name: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    venue: str = ""
    event_date: date
    rsvp_count: int = Field(default=0, ge=0)
    attendance_count: int = Field(default=0, ge=0)
    consults_booked: int = Field(default=0, ge=0)
    status: str = core.STATUS_PLANNING
    notes: str = ""
    materials: str = ""
    budget_usd: int = Field(default=0, ge=0)


class FieldEventUpdateRequest(BaseModel):
    """Body for ``PATCH /field/events/{event_id}`` — partial update (log attendance, etc.).

    Every field is OPTIONAL; only the provided (non-None) fields change. ``owner`` and
    ``event_id`` are never client-updatable here (identity/routing stay server-owned).
    """

    event_name: str | None = Field(default=None, min_length=1)
    event_type: str | None = None
    venue: str | None = None
    event_date: date | None = None
    rsvp_count: int | None = Field(default=None, ge=0)
    attendance_count: int | None = Field(default=None, ge=0)
    consults_booked: int | None = Field(default=None, ge=0)
    status: str | None = None
    notes: str | None = None
    materials: str | None = None
    budget_usd: int | None = Field(default=None, ge=0)


def _event_row(e: FieldEvent) -> FieldEventRow:
    """Project a store :class:`FieldEvent` onto the wire :class:`FieldEventRow`."""
    return FieldEventRow(
        event_id=e.event_id,
        event_name=e.event_name,
        event_type=e.event_type,
        venue=e.venue,
        event_date=e.event_date,
        rsvp_count=e.rsvp_count,
        attendance_count=e.attendance_count,
        consults_booked=e.consults_booked,
        status=e.status,
        owner=e.owner,
        notes=e.notes,
        materials=e.materials,
        budget_usd=e.budget_usd,
    )


# ===========================================================================
# READ endpoints (any authenticated seat).
# ===========================================================================
@router.get("/field/events/overview", response_model=OverviewResponse)
def get_overview(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> OverviewResponse:
    """The 8a rollup — computed from the field events (``now`` injected at the edge)."""
    events = store.list_events(program)
    rollup = core.overview(
        events,
        now=datetime.now(UTC).date(),
        upcoming_window_days=params.field_events.upcoming_window_days,
    )
    return OverviewResponse(**rollup)  # type: ignore[arg-type]


@router.get("/field/events", response_model=list[FieldEventRow])
def list_events(
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
    type: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    owner: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[FieldEventRow]:
    """The GT-organized field events, with optional type/status/owner/date-range filters."""
    filtered = core.tracker_filter(
        store.list_events(program),
        type=type,
        status=status,
        owner=owner,
        date_from=date_from,
        date_to=date_to,
    )
    return [_event_row(e) for e in filtered]


@router.get("/field/events/calendar", response_model=list[CalendarItem])
def get_calendar(
    store: StoreDep,
    grassroots_store: GrassrootsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> list[CalendarItem]:
    """A BLENDED month-grid feed: GT field events + READ-ONLY ambassador events.

    The GT field events come from this module's store (writable, ``source=field``); the
    ambassador events come from the SAME grassroots read behind ``GET /grassroots/events``
    (``source=ambassador``, ``read_only=True``) — never duplicated/owned here. Each item is
    tagged with its source + type so the UI can render one grid; ambassador items are
    flagged read-only so the UI never offers to edit them.
    """
    items: list[CalendarItem] = []
    for e in store.list_events(program):
        items.append(
            CalendarItem(
                source=SOURCE_FIELD,
                event_id=e.event_id,
                event_name=e.event_name,
                event_type=e.event_type,
                event_date=e.event_date,
                venue=e.venue,
                status=e.status,
                read_only=False,
            )
        )
    for a in grassroots_store.list_events(program):
        items.append(
            CalendarItem(
                source=SOURCE_AMBASSADOR,
                event_id=a.event_id,
                event_name=a.event_name,
                event_type=a.event_type,
                event_date=a.date,
                venue=a.location_label,
                status=None,
                read_only=True,
            )
        )
    return items


# ===========================================================================
# WRITE endpoints (owner-gated; identity from the verified principal).
# ===========================================================================
@router.post("/field/events", response_model=FieldEventRow)
def create_event(
    body: FieldEventCreateRequest,
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> FieldEventRow:
    """Create a GT-organized field event — owner-gated. ``event_type`` must be a known
    params label and ``status`` a known lifecycle status (clean 422 otherwise; INV-2)."""
    _require_field_events_owner(principal)
    if body.event_type not in params.field_events.event_types:
        raise HTTPException(
            status_code=422,
            detail=(
                f"event_type must be one of {params.field_events.event_types}, "
                f"got {body.event_type!r}"
            ),
        )
    if body.status not in core.STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {list(core.STATUSES)}, got {body.status!r}",
        )
    e = store.create_event(
        program,
        event_name=body.event_name,
        event_type=body.event_type,
        venue=body.venue,
        event_date=body.event_date,
        rsvp_count=body.rsvp_count,
        attendance_count=body.attendance_count,
        consults_booked=body.consults_booked,
        status=body.status,
        owner=FIELD_EVENTS_OWNER_WORKSTREAM,
        notes=body.notes,
        materials=body.materials,
        budget_usd=body.budget_usd,
    )
    return _event_row(e)


@router.patch("/field/events/{event_id}", response_model=FieldEventRow)
def update_event(
    event_id: UUID,
    body: FieldEventUpdateRequest,
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> FieldEventRow:
    """Update a field event (log attendance/consults, change status) — owner-gated.

    Partial: only provided fields change. A provided ``event_type``/``status`` must be a
    known value (clean 422; INV-2). 404 on an unknown ``event_id``.
    """
    _require_field_events_owner(principal)
    if body.event_type is not None and body.event_type not in params.field_events.event_types:
        raise HTTPException(
            status_code=422,
            detail=(
                f"event_type must be one of {params.field_events.event_types}, "
                f"got {body.event_type!r}"
            ),
        )
    if body.status is not None and body.status not in core.STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {list(core.STATUSES)}, got {body.status!r}",
        )
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        e = store.update_event(program, event_id, **changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="field event not found") from exc
    return _event_row(e)

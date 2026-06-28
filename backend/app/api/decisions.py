"""Decision-Queue endpoints — leader+admin VIEW, leader-only DECIDE, open submit (B2).

The composition layer that wires the B2 decision core (``app.core.decision_queue``)
and the store seam (``app.data.decisions_store``) behind REST. Thin by design: the
state machine (:func:`apply_action`) is pure/owned core (INV-2); this router only
orchestrates, gates by role, maps HTTP errors, and LOGS every decided action to the
§10 observability spine (NFR-6).

  ``GET  /decisions``  (``?view=active|history|all``)
    The queue — **view-gated** (``leader``/``admin`` — the admin has full module
    access); an operator is 403. ``view`` defaults to ``active`` (the OPEN queue, the
    back-compat behavior); ``history`` returns the decided/in-flight pile; ``all``
    returns everything.

  ``GET  /decisions/mine``
    The caller's OWN submissions + their outcome (state, latest action comment,
    resolution_date) — open to ANY authenticated principal, scoped by the verified
    ``raised_by``. The operator-visible path: a submitter who is 403 on the full
    queue still sees what became of the decisions they raised.

  ``POST /decisions``
    Submit a decision — open to ANY authenticated principal (any module / anyone
    may flag an item). Accepts the auto-flag ``{source, payload}`` shape AND a
    structured manual raise (``question`` / ``recommendation`` / ``workstream`` /
    ``budget_ask`` / ``due_date`` / ``priority``). ``raised_by`` is STAMPED from the
    verified principal — never the body. Inserts an OPEN decision and returns it.

  ``POST /decisions/{id}/action``
    Decide — ``approve`` / ``reject`` / ``need_info`` (+ optional ``comment``).
    **Leader-only** (spec Module 11 reserves decision-making to leadership; an admin
    may view but is 403 here, an operator is 403). Loads the decision (404 if absent),
    computes the next state via :func:`apply_action` (422 on an illegal transition / a
    ``need_info`` with no comment), records the action (append a ``decision_event`` +
    advance ``state``, stamping ``resolution_date`` on the first transition out of
    OPEN), LOGS the proposal + the human decision (NFR-6), and returns the decision.

``flag_decision`` is the thin module-level feeder other modules (B4/Nurture/Field)
call to enqueue — it wraps ``store.submit`` so callers never touch the store shape.

The leader gate is bound at MODULE level (``_DECIDE_GUARD``) so FastAPI resolves it
from the route's PEP-563 string annotation; a closure-local guard would be invisible
to ``get_type_hints`` and the route param would degrade to a 422-y query param.

This module may import ``app.core`` / ``app.observability`` (it is the composition
root); ``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.data.decisions_store import (
    PRIORITIES,
    PRIORITY_NORMAL,
    WORKSTREAMS,
    Decision,
    DecisionsStore,
)
from app.observability.log_store import DecisionAction as ObsDecisionAction
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["decisions"])

# Spec Module 11 access control splits VIEW from DECIDE:
#   • VIEW (GET the full queue): leadership AND admin. The role table (spec §2) gives
#     the admin/Marketing-Lead "full access to all modules EXCEPT Decision Queue
#     decision-making" — so an admin may SEE the queue; an operator is 403.
#   • DECIDE (act on an item): the LEADER exclusively. The same role table reserves
#     "Decision Queue decision-making" to leadership; admin can submit but never
#     decide. So the act path is require_role("leader") — admin is 403 here.
# Both guards are built ONCE at MODULE level so FastAPI can resolve them from the
# route's (string, PEP 563) annotation — a closure-local guard is invisible to
# `get_type_hints` and the route param would degrade to a query param (then 422).
_VIEW_GUARD = require_role("leader", "admin")
_DECIDE_GUARD = require_role("leader")

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/seam.py).
StoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
# The view-gated principal (leader OR admin) — resolves the verified principal AND
# enforces the VIEW gate.
LeaderDep = Annotated[Principal, Depends(_VIEW_GUARD)]
# The decide-gated principal (LEADER only) — the act path's exclusive gate.
DecideDep = Annotated[Principal, Depends(_DECIDE_GUARD)]
# Any authenticated principal (the open-submit path — NOT role-gated).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# The audited §10 flow tag + schema version for a decision action (the audit head).
DECISION_FLOW = "decision_queue"
DECISION_SCHEMA_VERSION = "1"

# The source tag every Field & Events event-proposal Decision-Queue item carries — the
# Field-Marketing owner's priority recommendation landing as a leadership decision
# (Module 11). A named wire token, not a tunable (INV-11 carve-out, like DECISION_FLOW).
FIELD_EVENT_SOURCE = "field_event_proposal"

# Map the B2 human action onto the observability spine's verdict vocabulary (which
# is APPROVE/EDIT/DISCARD): approve→APPROVE, reject→DISCARD, need_info→EDIT (more
# info requested ≈ an edit-back). The audit payload always carries the EXACT B2
# action + comment too, so nothing is lost in the mapping.
_OBS_ACTION: dict[DecisionAction, ObsDecisionAction] = {
    DecisionAction.APPROVE: ObsDecisionAction.APPROVE,
    DecisionAction.REJECT: ObsDecisionAction.DISCARD,
    DecisionAction.NEED_INFO: ObsDecisionAction.EDIT,
}


def _actor_token(principal: Principal) -> str:
    """The verified actor reference for an audit/raise stamp — uid, else role.

    The ONE place the principal → token mapping lives (DRY): prefer the stable
    ``user_id`` (the JWT ``sub``), falling back to the ``role`` when no uid is
    present. Always derived from the VERIFIED principal — never a client claim
    (INV-1; the IDOR/spoof posture).
    """
    return str(principal.user_id) if principal.user_id is not None else principal.role


def _outcome_of(action: DecisionAction | None) -> str | None:
    """The wire ``outcome`` value for a latest action — its enum value, or ``None``.

    Maps the latest ``DecisionAction`` (``approve``/``reject``/``need_info``) to the
    string the UI shows (approved/rejected/need-info) and filters history by. ``None``
    when no action has been recorded (an OPEN row with no verdict yet).
    """
    return action.value if action is not None else None


class DecisionResponse(BaseModel):
    """One Decision-Queue row over the wire (the API shape of :class:`Decision`).

    Carries the 0028 columns + the 0034 first-class spec-fields. ``question`` and
    ``workstream`` are the DISPLAY values: a manual raise shows its own fields; an
    auto-flag (payload-only) row derives them from ``payload`` so nothing renders
    blank (the :meth:`Decision.display_question` / ``display_workstream`` helpers).
    """

    id: UUID
    source: str
    payload: dict[str, Any]
    state: DecisionState
    question: str
    raised_by: str
    workstream: str
    recommendation: str
    budget_ask: float | None
    due_date: date | None
    priority: str
    resolution_date: datetime | None
    # The latest action VERDICT — "approve" | "reject" | "need_info" | None (no action
    # yet). Lets a decided row read approved/rejected (not a flat "resolved") and powers
    # the history outcome filter. None for an OPEN row with no recorded action.
    outcome: str | None = None

    @classmethod
    def of(cls, decision: Decision, *, outcome: str | None = None) -> DecisionResponse:
        """Project a stored :class:`Decision` onto the response shape (display-derived)."""
        return cls(
            id=decision.id,
            source=decision.source,
            payload=decision.payload,
            state=decision.state,
            question=decision.display_question(),
            raised_by=decision.raised_by,
            workstream=decision.display_workstream(),
            recommendation=decision.recommendation,
            budget_ask=decision.budget_ask,
            due_date=decision.due_date,
            priority=decision.priority,
            resolution_date=decision.resolution_date,
            outcome=outcome,
        )


class MyDecisionResponse(DecisionResponse):
    """A submitter's own decision + its outcome (``GET /decisions/mine``).

    Extends :class:`DecisionResponse` with the latest action ``comment`` so a
    submitter sees what the leader said (``resolution_date`` already carries WHEN it
    was decided). ``latest_comment`` is ``None`` when no action has been recorded yet
    (or the recorded action carried no comment).
    """

    latest_comment: str | None

    @classmethod
    def of_mine(
        cls, decision: Decision, *, latest_comment: str | None, outcome: str | None = None
    ) -> MyDecisionResponse:
        """Project a stored :class:`Decision` + its latest comment onto the mine shape."""
        return cls(
            id=decision.id,
            source=decision.source,
            payload=decision.payload,
            state=decision.state,
            question=decision.display_question(),
            raised_by=decision.raised_by,
            workstream=decision.display_workstream(),
            recommendation=decision.recommendation,
            budget_ask=decision.budget_ask,
            due_date=decision.due_date,
            priority=decision.priority,
            resolution_date=decision.resolution_date,
            outcome=outcome,
            latest_comment=latest_comment,
        )


class SubmitRequest(BaseModel):
    """Body for ``POST /decisions`` — flag an item for a human.

    Back-compatible UNION of two shapes, both accepted on the one open-submit route:

    - The AUTO-FLAG / legacy shape: ``{source, payload}`` (a module enqueues context
      as jsonb; the structured fields stay at their defaults).
    - The MANUAL RAISE shape: ``{question, recommendation, workstream, budget_ask?,
      due_date?, priority}`` — an operator proposes a decision with first-class
      fields. ``source`` defaults to ``"manual_raise"`` so a raise body needs no
      source.

    There is DELIBERATELY no ``raised_by`` field: the route stamps it from the
    VERIFIED principal, never from the body (the IDOR/spoof posture, INV-1).
    """

    source: str = Field(default="manual_raise", min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    question: str = ""
    recommendation: str = ""
    workstream: str = ""
    budget_ask: float | None = None
    due_date: date | None = None
    priority: str = PRIORITY_NORMAL


class ActionRequest(BaseModel):
    """Body for ``POST /decisions/{id}/action`` — the human verdict (+ optional comment)."""

    action: DecisionAction
    comment: str | None = None


# The decided/in-flight states a `view=history` listing returns (everything past OPEN).
_HISTORY_STATES: frozenset[DecisionState] = frozenset(
    {DecisionState.DECIDED, DecisionState.IN_FLIGHT}
)

# The query-param vocabulary for `GET /decisions?view=…` (named — INV-11). `active`
# is the back-compat default (the OPEN queue); `history` is the decided/in-flight
# pile; `all` is the full set.
DecisionView = Literal["active", "history", "all"]


@router.get("/decisions", response_model=list[DecisionResponse])
def list_decisions(
    store: StoreDep,
    program: ProgramDep,
    principal: LeaderDep,
    view: Annotated[DecisionView, Query()] = "active",
) -> list[DecisionResponse]:
    """List decisions for the active program (leader/admin-gated; operator → 403).

    The ``view`` query param selects the slice (default ``active`` ⇒ the unchanged
    back-compat OPEN queue):

    - ``active``  — the OPEN queue (the live work; the original behavior).
    - ``history`` — the DECIDED + IN_FLIGHT pile (what's already been acted on).
    - ``all``     — every decision (open + decided + in_flight).
    """
    if view == "active":
        # OPEN queue: no verdict yet, so skip the per-row latest_action lookup.
        return [DecisionResponse.of(d) for d in store.list_open(program)]
    if view == "history":
        decisions = [d for d in store.list_all(program) if d.state in _HISTORY_STATES]
    else:  # all
        decisions = store.list_all(program)
    # History/all rows may carry a verdict — attach the latest action so the UI can
    # show approved/rejected/need-info and filter by outcome.
    return [
        DecisionResponse.of(d, outcome=_outcome_of(store.latest_action(program, d.id)))
        for d in decisions
    ]


@router.get("/decisions/mine", response_model=list[MyDecisionResponse])
def list_my_decisions(
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> list[MyDecisionResponse]:
    """List the CALLER's OWN submissions + their outcome (ANY authenticated seat).

    The operator-visible path: a submitter (who is 403 on the full leader queue) can
    still see the decisions THEY raised, each with its current ``state``, the latest
    action ``comment``, and the ``resolution_date`` — so they learn the outcome of
    their proposal. Scoped by ``raised_by == the verified principal`` (never a client
    filter), so one seat never reads another's submissions.
    """
    me = _actor_token(principal)
    mine = [d for d in store.list_all(program) if d.raised_by == me]
    return [
        MyDecisionResponse.of_mine(
            d,
            latest_comment=store.latest_comment(program, d.id),
            outcome=_outcome_of(store.latest_action(program, d.id)),
        )
        for d in mine
    ]


@router.post("/decisions", response_model=DecisionResponse)
def submit_decision(
    body: SubmitRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Submit an OPEN decision — open to ANY authenticated principal (anyone flags).

    Accepts both the auto-flag ``{source, payload}`` shape and a structured manual
    raise (``question`` / ``recommendation`` / ``workstream`` / ``budget_ask`` /
    ``due_date`` / ``priority``). ``raised_by`` is STAMPED from the verified principal
    — never the body (the IDOR/spoof posture). ``priority`` must be one of
    :data:`PRIORITIES`; a non-empty ``workstream`` must be one of :data:`WORKSTREAMS`
    — an unknown value is a clean 422 (fail-closed, INV-2).
    """
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}",
        )
    if body.workstream and body.workstream not in WORKSTREAMS:
        raise HTTPException(
            status_code=422,
            detail=f"workstream must be one of {WORKSTREAMS}, got {body.workstream!r}",
        )
    decision = store.submit(
        program,
        source=body.source,
        payload=body.payload,
        question=body.question,
        raised_by=_actor_token(principal),
        workstream=body.workstream,
        recommendation=body.recommendation,
        budget_ask=body.budget_ask,
        due_date=body.due_date,
        priority=body.priority,
    )
    return DecisionResponse.of(decision)


@router.post("/decisions/{decision_id}/action", response_model=DecisionResponse)
def act_on_decision(
    decision_id: UUID,
    body: ActionRequest,
    store: StoreDep,
    program: ProgramDep,
    log: LogDep,
    principal: DecideDep,
) -> DecisionResponse:
    """Decide on a queued item — LEADER-only (spec Module 11; admin is 403); logged
    to the §10 spine (NFR-6).

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

    actor = _actor_token(principal)
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
    # The row was just written; fall back to a locally-advanced projection defensively
    # (carry the spec-fields forward; stamp the resolution instant if it just left open).
    if updated is None:
        resolution_date = current.resolution_date
        if new_state is not DecisionState.OPEN and resolution_date is None:
            resolution_date = datetime.now(UTC)
        updated = Decision(
            id=current.id,
            source=current.source,
            payload=current.payload,
            state=new_state,
            created_at=current.created_at,
            question=current.question,
            raised_by=current.raised_by,
            workstream=current.workstream,
            recommendation=current.recommendation,
            budget_ask=current.budget_ask,
            due_date=current.due_date,
            priority=current.priority,
            resolution_date=resolution_date,
        )
    # The verdict just applied is the row's current outcome (approve/reject/need-info).
    return DecisionResponse.of(updated, outcome=body.action.value)


def flag_decision(
    store: DecisionsStore,
    program: Program,
    *,
    source: str,
    payload: dict[str, Any],
    question: str = "",
    raised_by: str = "",
    workstream: str = "",
    recommendation: str = "",
    budget_ask: float | None = None,
    due_date: date | None = None,
    priority: str = PRIORITY_NORMAL,
) -> Decision:
    """Enqueue an OPEN decision — the shared feeder B4/Nurture/Field call (B2).

    A thin wrapper over :meth:`DecisionsStore.submit` so other modules enqueue a
    human decision without touching the store/route shape. The structured spec-fields
    (``question`` / ``workstream`` / ``recommendation`` / ``budget_ask`` / ``due_date``
    / ``priority``) are KEYWORD-OPTIONAL so the legacy budget-variance feeder still
    calls with only ``source`` + ``payload`` (defaults unchanged), while the Module-11
    hot-family + field-events feeders stamp a workstream/question/recommendation. For a
    purely AUTOMATED flag with no human submitter, the caller passes a system actor
    token as ``raised_by``; a user-driven proposal stamps the verified principal's
    token. Returns the created open :class:`Decision`.
    """
    return store.submit(
        program,
        source=source,
        payload=payload,
        question=question,
        raised_by=raised_by,
        workstream=workstream,
        recommendation=recommendation,
        budget_ask=budget_ask,
        due_date=due_date,
        priority=priority,
    )

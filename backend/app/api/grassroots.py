"""Grassroots-Engine endpoints (Module 2) — roster/sprints/market-map/events + cross-links.

The composition layer that wires the Module-2 deterministic core
(:mod:`app.core.grassroots`), the store seam (:mod:`app.data.grassroots_store`), the
dual-source ambassador reconciler (:mod:`app.core.ambassador_reconcile`), and three
cross-module links behind REST. Thin by design: every derivation is pure/owned core
(INV-2); this router only adapts the store rows in, gates the writes by owner, and
shapes the JSON the Grassroots UI consumes.

READS (any authenticated seat):
  ``GET /grassroots/overview``     — the four goal bars + pipeline counts + headline.
  ``GET /grassroots/ambassadors``  — the roster (+ reconcile provenance where available).
  ``GET /grassroots/market-map``   — the nodes + per-category coverage summary.
  ``GET /grassroots/sprints``      — the sprints + per-sprint health (as_of injected here).
  ``GET /grassroots/events``       — the parent-led events (the Field & Events READ source).

WRITES (OWNER-gated — an operator must OWN the ``grassroots`` workstream; leaders/admins
may write any; everyone else is 403. ``raised_by``/identity is ALWAYS from the verified
principal, NEVER the client body — the IDOR/spoof posture, INV-1):
  ``POST /grassroots/ambassador/{id}/log-p2p`` — increment an ambassador's p2p_calls.
  ``POST /grassroots/market-map/node``         — create/update a market-map node.
  ``POST /grassroots/sprint``                  — launch a referral sprint.
  ``POST /grassroots/event``                   — log a parent-led event.

CROSS-MODULE LINKS (all OWNER-gated; ``raised_by`` from the verified principal):
  ``POST /grassroots/hot-family``  — escalate a hot family into the leadership Decision
    Queue via the SAME ``app.api.decisions.flag_decision`` feeder budget/field use.
  ``POST /grassroots/testimonial`` — stub a DRAFT content asset in the Content library
    (tagged ``source=grassroots_testimonial``) for the content team to pick up.

This module may import ``app.core`` / ``app.api`` (it is the composition root);
``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import (
    Channel,
    ContentFormat,
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.api.decisions import DecisionResponse, _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_content_library_dep,
    get_decisions_store,
    get_grassroots_store,
    get_params,
    get_principal,
)
from app.core.ambassador_reconcile import reconcile_ambassadors
from app.core.grassroots import (
    AmbassadorView,
    NodeView,
    SprintView,
    attribute_enrollments,
    goal_progress,
    market_map_summary,
    pipeline_counts,
    sprint_health,
)
from app.core.params import Params
from app.core.program import Program
from app.data.decisions_store import (
    PRIORITIES,
    PRIORITY_NORMAL,
    DecisionsStore,
)
from app.data.grassroots_store import (
    Ambassador,
    AmbassadorEvent,
    GrassrootsStore,
    MarketNode,
    ReferralSprint,
)
from app.data.synthetic_ambassadors import generate_ambassador_sources
from app.marketing.library import ContentLibrary

router = APIRouter(tags=["grassroots"])

# The workstream the Grassroots Engine owns (one of decisions_store.WORKSTREAMS). The
# OPERATOR who owns this workstream may write; a foreign operator is 403. Named wire
# tokens, not tunables (INV-11 carve-out, mirroring budget.DEMO_OPERATOR_WORKSTREAM).
GRASSROOTS_WORKSTREAM = "grassroots"
DEMO_OPERATOR_WORKSTREAM = "grassroots"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# The source tags the two cross-module feeders carry (fixed wire tokens, INV-11
# carve-out, like decisions.FIELD_EVENT_SOURCE).
HOT_FAMILY_SOURCE = "grassroots_hot_family"
TESTIMONIAL_SOURCE = "grassroots_testimonial"

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/budget.py).
StoreDep = Annotated[GrassrootsStore, Depends(get_grassroots_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ContentLibraryDep = Annotated[ContentLibrary, Depends(get_content_library_dep)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
# Any authenticated principal (the READ path — NOT role-gated; anyone may VIEW).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]


# ===========================================================================
# Owner gate — an OPERATOR may write only when they OWN the grassroots workstream;
# a LEADER/ADMIN may write anything; everyone else is 403. The verified ROLE decides
# — never a client claim (the IDOR/spoof posture). Mirrors app.api.budget.
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


def _require_grassroots_owner(principal: Principal) -> None:
    """OWNER gate for every Grassroots write — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the
    workstream they own is ``grassroots``; a foreign operator is 403. The verified ROLE
    decides — never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    # role == "operator" (the only remaining verified role).
    if _operator_workstream(principal) != GRASSROOTS_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {GRASSROOTS_WORKSTREAM!r} workstream",
        )


# ===========================================================================
# Wire models.
# ===========================================================================
class GoalBarOut(BaseModel):
    """One goal-progress bar over the wire (value/target/pct — no fake delta)."""

    key: str
    value: int
    target: int
    pct: int


class OverviewResponse(BaseModel):
    """The Grassroots overview — goal bars + pipeline counts + headline counts."""

    goals: list[GoalBarOut]
    pipeline: dict[str, int]
    headline: dict[str, int]


class AmbassadorRow(BaseModel):
    """One ambassador roster row over the wire (+ reconcile provenance where available)."""

    ambassador_id: UUID
    synthetic_name: str
    synthetic_email: str
    segment: str
    region: str
    status: str
    intros: int
    p2p_calls: int
    last_touch: date | None
    owner: str
    # The dual-source reconcile provenance (both / hubspot-only / community-only) when
    # the email matches a reconciled row; ``None`` when this roster row is not present
    # in either tracked source (the honest "where available" contract).
    provenance: str | None = None


class CategorySummaryOut(BaseModel):
    """One market-map category coverage row over the wire."""

    category: str
    total: int
    contacted: int
    leads: int
    coverage_pct: int


class MarketNodeRow(BaseModel):
    """One market-map node over the wire."""

    node_id: UUID
    category: str
    contact_label: str
    status: str
    leads_generated: int
    last_activity: date | None
    owner: str


class MarketMapResponse(BaseModel):
    """The market map — nodes + per-category coverage summary."""

    nodes: list[MarketNodeRow]
    summary: list[CategorySummaryOut]


class SprintRow(BaseModel):
    """One referral sprint over the wire (+ derived health)."""

    sprint_id: UUID
    name: str
    window_start: date
    window_end: date
    ambassadors_enlisted: int
    families_identified: int
    conversions: int
    status: str
    health: str


class EventRow(BaseModel):
    """One parent-led event over the wire (the Field & Events READ-ONLY contract)."""

    event_id: UUID
    event_name: str
    host_ambassador_id: UUID | None
    event_type: str
    date: date
    location_label: str
    rsvp_count: int
    attendance_count: int
    conversions_influenced: int


# ----------------------------------------- write request bodies (NO identity field)
class MarketNodeRequest(BaseModel):
    """Body for ``POST /grassroots/market-map/node`` — create/update a node."""

    node_id: UUID | None = None
    category: str = Field(min_length=1)
    contact_label: str = ""
    status: str = "cold"
    leads_generated: int = Field(default=0, ge=0)
    last_activity: date | None = None


class SprintRequest(BaseModel):
    """Body for ``POST /grassroots/sprint`` — launch a referral sprint."""

    name: str = Field(min_length=1)
    window_start: date
    window_end: date
    ambassadors_enlisted: int = Field(default=0, ge=0)
    families_identified: int = Field(default=0, ge=0)
    conversions: int = Field(default=0, ge=0)
    status: str = "active"


class EventRequest(BaseModel):
    """Body for ``POST /grassroots/event`` — log a parent-led event."""

    event_name: str = Field(min_length=1)
    host_ambassador_id: UUID | None = None
    event_type: str = "coffee_chat"
    date: date
    location_label: str = ""
    rsvp_count: int = Field(default=0, ge=0)
    attendance_count: int = Field(default=0, ge=0)
    conversions_influenced: int = Field(default=0, ge=0)


class HotFamilyRequest(BaseModel):
    """Body for ``POST /grassroots/hot-family`` — escalate a hot family to the queue.

    There is DELIBERATELY no ``raised_by`` field: the route stamps it from the VERIFIED
    principal, never the body (the IDOR/spoof posture, INV-1). ``family_label`` is a
    SYNTHETIC/aggregate label only — never real PII (INV-1).
    """

    family_label: str = Field(min_length=1)
    reason: str = ""
    recommendation: str = ""
    budget_ask: float | None = None
    due_date: date | None = None
    priority: str = PRIORITY_NORMAL


class TestimonialRequest(BaseModel):
    """Body for ``POST /grassroots/testimonial`` — stub a DRAFT content asset.

    ``quote`` is SYNTHETIC/aggregate adult content only — never real PII (INV-1). The
    content team picks the stub up and runs it through the §9 gate before it can be kept.
    """

    title: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    attribution_label: str = ""


# ===========================================================================
# Projection helpers (store rows → core views / wire rows).
# ===========================================================================
def _ambassador_views(ambassadors: list[Ambassador]) -> list[AmbassadorView]:
    """Project store ambassadors onto the pure-core :class:`AmbassadorView`."""
    return [
        AmbassadorView(status=a.status, intros=a.intros, p2p_calls=a.p2p_calls) for a in ambassadors
    ]


def _node_views(nodes: list[MarketNode]) -> list[NodeView]:
    """Project store market nodes onto the pure-core :class:`NodeView`."""
    return [
        NodeView(category=n.category, status=n.status, leads_generated=n.leads_generated)
        for n in nodes
    ]


def _sprint_views(sprints: list[ReferralSprint]) -> list[SprintView]:
    """Project store sprints onto the pure-core :class:`SprintView`."""
    return [
        SprintView(
            window_start=s.window_start,
            window_end=s.window_end,
            families_identified=s.families_identified,
            conversions=s.conversions,
            status=s.status,
        )
        for s in sprints
    ]


def _provenance_by_email() -> dict[str, str]:
    """Map normalized email → reconcile provenance from the dual-source reconciler.

    Runs the SAME pure :func:`reconcile_ambassadors` the ``/ambassadors/reconcile``
    endpoint uses over the stood-in HubSpot ⊕ community sources, so a roster row whose
    email appears in either tracked source carries its provenance badge. A row absent
    from both sources gets no entry (provenance ``None`` — the honest contract).
    """
    sources = generate_ambassador_sources()
    result = reconcile_ambassadors(list(sources.hubspot.rows), list(sources.community.rows))
    return {row.synthetic_email.strip().casefold(): str(row.provenance) for row in result.union}


# ===========================================================================
# READ endpoints (any authenticated seat).
# ===========================================================================
@router.get("/grassroots/overview", response_model=OverviewResponse)
def get_overview(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> OverviewResponse:
    """The four goal bars + pipeline counts + headline counts (any authenticated VIEW)."""
    ambassadors = store.list_ambassadors(program)
    sprints = store.list_sprints(program)
    events = store.list_events(program)
    nodes = store.list_market_nodes(program)

    views = _ambassador_views(ambassadors)
    influenced = attribute_enrollments(_sprint_views(sprints))
    targets = params.grassroots.targets
    bars = goal_progress(
        views,
        influenced,
        target_active_ambassadors=targets.active_ambassadors,
        target_warm_intros=targets.warm_intros,
        target_p2p_calls=targets.p2p_calls,
        target_influenced_enrollments=targets.influenced_enrollments,
    )
    pipeline = pipeline_counts(views)
    today = datetime.now(UTC).date()
    headline = {
        "ambassadors_total": len(ambassadors),
        "sprints_total": len(sprints),
        "sprints_active": sum(1 for s in sprints if s.status == "active"),
        "market_nodes_total": len(nodes),
        "events_total": len(events),
        "events_upcoming": sum(1 for e in events if e.date >= today),
    }
    goals = [
        GoalBarOut(key=key, value=b.value, target=b.target, pct=b.pct) for key, b in bars.items()
    ]
    return OverviewResponse(
        goals=goals,
        pipeline=pipeline,
        headline=headline,
    )


@router.get("/grassroots/ambassadors", response_model=list[AmbassadorRow])
def list_ambassadors(
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> list[AmbassadorRow]:
    """The ambassador roster, each with reconcile provenance where available."""
    provenance = _provenance_by_email()
    rows: list[AmbassadorRow] = []
    for a in store.list_ambassadors(program):
        rows.append(
            AmbassadorRow(
                ambassador_id=a.ambassador_id,
                synthetic_name=a.synthetic_name,
                synthetic_email=a.synthetic_email,
                segment=a.segment,
                region=a.region,
                status=a.status,
                intros=a.intros,
                p2p_calls=a.p2p_calls,
                last_touch=a.last_touch,
                owner=a.owner,
                provenance=provenance.get(a.synthetic_email.strip().casefold()),
            )
        )
    return rows


@router.get("/grassroots/market-map", response_model=MarketMapResponse)
def get_market_map(
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> MarketMapResponse:
    """The market-map nodes + per-category coverage summary."""
    nodes = store.list_market_nodes(program)
    summary = market_map_summary(_node_views(nodes))
    return MarketMapResponse(
        nodes=[
            MarketNodeRow(
                node_id=n.node_id,
                category=n.category,
                contact_label=n.contact_label,
                status=n.status,
                leads_generated=n.leads_generated,
                last_activity=n.last_activity,
                owner=n.owner,
            )
            for n in nodes
        ],
        summary=[
            CategorySummaryOut(
                category=c.category,
                total=c.total,
                contacted=c.contacted,
                leads=c.leads,
                coverage_pct=c.coverage_pct,
            )
            for c in summary
        ],
    )


@router.get("/grassroots/sprints", response_model=list[SprintRow])
def list_sprints(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> list[SprintRow]:
    """The referral sprints, each with derived health (as_of injected at the edge)."""
    as_of = datetime.now(UTC).date()
    behind_pace_frac = params.grassroots.sprint_health.behind_pace_frac
    rows: list[SprintRow] = []
    for s in store.list_sprints(program):
        health = sprint_health(
            SprintView(
                window_start=s.window_start,
                window_end=s.window_end,
                families_identified=s.families_identified,
                conversions=s.conversions,
                status=s.status,
            ),
            as_of=as_of,
            behind_pace_frac=behind_pace_frac,
        )
        rows.append(
            SprintRow(
                sprint_id=s.sprint_id,
                name=s.name,
                window_start=s.window_start,
                window_end=s.window_end,
                ambassadors_enlisted=s.ambassadors_enlisted,
                families_identified=s.families_identified,
                conversions=s.conversions,
                status=s.status,
                health=health,
            )
        )
    return rows


@router.get("/grassroots/events", response_model=list[EventRow])
def list_events(
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> list[EventRow]:
    """The parent-led events — the SOURCE OF TRUTH the Field & Events module reads.

    READ-ONLY cross-module contract: ``ambassador_event`` is owned/written ONLY by the
    Grassroots Engine (the owner-gated ``POST /grassroots/event``); Field & Events
    consumes this read path and never writes the table.
    """
    return [_event_row(e) for e in store.list_events(program)]


def _event_row(e: AmbassadorEvent) -> EventRow:
    """Project a store :class:`AmbassadorEvent` onto the wire :class:`EventRow`."""
    return EventRow(
        event_id=e.event_id,
        event_name=e.event_name,
        host_ambassador_id=e.host_ambassador_id,
        event_type=e.event_type,
        date=e.date,
        location_label=e.location_label,
        rsvp_count=e.rsvp_count,
        attendance_count=e.attendance_count,
        conversions_influenced=e.conversions_influenced,
    )


# ===========================================================================
# WRITE endpoints (owner-gated).
# ===========================================================================
@router.post("/grassroots/ambassador/{ambassador_id}/log-p2p", response_model=AmbassadorRow)
def log_p2p(
    ambassador_id: UUID,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> AmbassadorRow:
    """Increment one ambassador's p2p_calls — owner-gated; 404 on an unknown ambassador."""
    _require_grassroots_owner(principal)
    try:
        a = store.log_p2p_call(program, ambassador_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ambassador not found") from exc
    return AmbassadorRow(
        ambassador_id=a.ambassador_id,
        synthetic_name=a.synthetic_name,
        synthetic_email=a.synthetic_email,
        segment=a.segment,
        region=a.region,
        status=a.status,
        intros=a.intros,
        p2p_calls=a.p2p_calls,
        last_touch=a.last_touch,
        owner=a.owner,
    )


@router.post("/grassroots/market-map/node", response_model=MarketNodeRow)
def upsert_market_node(
    body: MarketNodeRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> MarketNodeRow:
    """Create or update a market-map node — owner-gated."""
    _require_grassroots_owner(principal)
    n = store.upsert_market_node(
        program,
        node_id=body.node_id,
        category=body.category,
        contact_label=body.contact_label,
        status=body.status,
        leads_generated=body.leads_generated,
        last_activity=body.last_activity,
    )
    return MarketNodeRow(
        node_id=n.node_id,
        category=n.category,
        contact_label=n.contact_label,
        status=n.status,
        leads_generated=n.leads_generated,
        last_activity=n.last_activity,
        owner=n.owner,
    )


@router.post("/grassroots/sprint", response_model=SprintRow)
def create_sprint(
    body: SprintRequest,
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> SprintRow:
    """Launch a referral sprint — owner-gated. Returns the sprint with derived health."""
    _require_grassroots_owner(principal)
    if body.window_end < body.window_start:
        raise HTTPException(status_code=422, detail="window_end must be >= window_start")
    s = store.create_sprint(
        program,
        name=body.name,
        window_start=body.window_start,
        window_end=body.window_end,
        ambassadors_enlisted=body.ambassadors_enlisted,
        families_identified=body.families_identified,
        conversions=body.conversions,
        status=body.status,
    )
    health = sprint_health(
        SprintView(
            window_start=s.window_start,
            window_end=s.window_end,
            families_identified=s.families_identified,
            conversions=s.conversions,
            status=s.status,
        ),
        as_of=datetime.now(UTC).date(),
        behind_pace_frac=params.grassroots.sprint_health.behind_pace_frac,
    )
    return SprintRow(
        sprint_id=s.sprint_id,
        name=s.name,
        window_start=s.window_start,
        window_end=s.window_end,
        ambassadors_enlisted=s.ambassadors_enlisted,
        families_identified=s.families_identified,
        conversions=s.conversions,
        status=s.status,
        health=health,
    )


@router.post("/grassroots/event", response_model=EventRow)
def create_event(
    body: EventRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> EventRow:
    """Log a parent-led event — owner-gated. ``ambassador_event`` is the Field & Events
    READ source (this is the only writer)."""
    _require_grassroots_owner(principal)
    e = store.create_event(
        program,
        event_name=body.event_name,
        host_ambassador_id=body.host_ambassador_id,
        event_type=body.event_type,
        date=body.date,
        location_label=body.location_label,
        rsvp_count=body.rsvp_count,
        attendance_count=body.attendance_count,
        conversions_influenced=body.conversions_influenced,
    )
    return _event_row(e)


# ===========================================================================
# CROSS-MODULE LINKS (owner-gated; identity from the verified principal).
# ===========================================================================
@router.post("/grassroots/hot-family", response_model=DecisionResponse)
def escalate_hot_family(
    body: HotFamilyRequest,
    store: DecisionsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Escalate a hot family into the leadership Decision Queue (Module 11 plumbing).

    Reuses the SAME :func:`app.api.decisions.flag_decision` feeder budget-variance and
    field-events use: enqueues ONE open ``grassroots_hot_family`` decision on the
    ``grassroots`` workstream, ``raised_by`` STAMPED from the verified principal (never
    the body — the IDOR/spoof posture, INV-1). Owner-gated. ``priority`` must be one of
    :data:`PRIORITIES` (a clean 422 otherwise; fail-closed, INV-2).
    """
    _require_grassroots_owner(principal)
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}",
        )
    decision = flag_decision(
        store,
        program,
        source=HOT_FAMILY_SOURCE,
        payload={"family_label": body.family_label, "reason": body.reason},
        question=f"Escalate hot family: {body.family_label}",
        raised_by=_actor_token(principal),
        workstream=GRASSROOTS_WORKSTREAM,
        recommendation=body.recommendation,
        budget_ask=body.budget_ask,
        due_date=body.due_date,
        priority=body.priority,
    )
    return DecisionResponse.of(decision)


class TestimonialResponse(BaseModel):
    """The stubbed content asset over the wire (``POST /grassroots/testimonial``)."""

    asset_id: str
    title: str
    lifecycle: str
    source: str
    tags: list[str]


@router.post("/grassroots/testimonial", response_model=TestimonialResponse)
def stub_testimonial(
    body: TestimonialRequest,
    library: ContentLibraryDep,
    principal: AnyPrincipalDep,
) -> TestimonialResponse:
    """Stub a DRAFT content asset in the Content library from a grassroots testimonial.

    The minimal correct cross-module integration: a parent testimonial captured in the
    Grassroots Engine becomes a DRAFT :class:`LibraryAsset` in the Content module's
    store, tagged ``source=grassroots_testimonial`` (the ``source_ref``). It is a DRAFT
    (``lifecycle=draft``), NOT kept — so it does NOT yet surface in the library SEARCH
    (which returns only kept+validated assets); the content team must run it through the
    §9 gate and explicitly keep it first. ``created_by_user`` is the verified principal
    (never a client claim, INV-1). Owner-gated. Returns the stub's id/lifecycle/tags.
    """
    _require_grassroots_owner(principal)
    asset_id = f"grassroots-testimonial-{uuid4().hex}"
    asset = LibraryAsset(
        id=asset_id,
        title=body.title,
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        body=body.quote,
        source_ref=TESTIMONIAL_SOURCE,
        tags=[TESTIMONIAL_SOURCE, "testimonial"],
        search_text=f"{body.title} {body.quote} {body.attribution_label}".strip(),
        # A DRAFT stub has no passing ValidationResult yet; this is a documented
        # stand-in marker (the field is min_length=1) — the content team's keep path
        # replaces it with a real validation id once the §9 gate passes.
        validation="pending-grassroots-testimonial-stub",
        lifecycle=LifecycleStage.DRAFT,
        provenance=Provenance(
            generated_by=GeneratedBy.HUMAN,
            created_at=datetime.now(UTC).isoformat(),
            created_by_user=_actor_token(principal),
        ),
    )
    stored = library.add(asset)
    return TestimonialResponse(
        asset_id=stored.id,
        title=stored.title,
        lifecycle=stored.lifecycle.value,
        source=TESTIMONIAL_SOURCE,
        tags=list(stored.tags),
    )

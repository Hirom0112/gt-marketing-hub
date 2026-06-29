"""Admissions & Voice-of-Customer endpoints (Module 9) — the listening post + two cross-links.

The composition layer that wires the Admissions surface into the pure core
(``app.core.admissions``), the store seam (``app.data.admissions_store``), the §7.5
sentiment adapter (aggregate-only, INV-6), and the two cross-links the spec mandates:

  * GET  /admissions/overview   — the 9a hero rollup (admission numbers by week, top-3
    objections + trend, feedback open count, notable quotes, objection→resolution time,
    content-bridge hit rate).
  * GET  /admissions/objections — the 9b objection log (theme/source filters + sort).
  * GET  /admissions/voice      — the 9d voice feed + quote-of-the-week + sentiment
    score/trend from the §7.5 adapter (``source_mode`` labelled honestly, INV-6).
  * GET  /admissions/feedback   — the 9e feedback items + the closure rate.
  * GET  /admissions/bridge     — the 9c content-bridge hit rate + per-brief produced
    status + did-frequency-decrease.
  * POST /admissions/objections/{id}/brief — CROSS-LINK 1: turn an objection into a
    Content calendar DRAFT brief (``content_metrics_store.upsert_calendar_entry``,
    ``owner="admissions"``) + record a ``content_bridge`` row. Owner-gated (admissions).
  * POST /admissions/feedback   — create a feedback item; when ``actionable`` it ALSO
    enqueues an OPEN ``admissions`` Decision-Queue item (CROSS-LINK 2) and stores the
    returned ``decision_id`` on the item. Owner-gated (admissions).
  * PATCH /admissions/feedback/{id} — action/close a feedback item (set status +
    actioned_at). Leader/admin only (leadership input).

``owner`` / ``raised_by`` are STAMPED from the VERIFIED principal — never the body (the
IDOR/spoof posture, INV-1). This module may import ``app.api`` / ``app.adapters`` (it is
the composition root); ``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.adapters.sentiment.base import SentimentAdapter, SentimentWindow
from app.api.decisions import _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_admissions_store,
    get_content_metrics_store,
    get_decisions_store,
    get_params,
    get_principal,
    get_sentiment_adapter_dep,
    require_role,
)
from app.core import admissions as core
from app.core.content_analytics import STATUS_DRAFT
from app.core.params import Params
from app.core.program import Program
from app.data.admissions_store import (
    AdmissionsStore,
    ContentBridge,
    FeedbackItem,
    Objection,
    VoiceQuote,
)
from app.data.content_metrics_store import ContentMetricsStore
from app.data.decisions_store import WORKSTREAMS, DecisionsStore

router = APIRouter(tags=["admissions"])

# The Decision-Queue workstream every admissions escalation belongs to (one of
# decisions_store.WORKSTREAMS). Named, not a bare literal (INV-11 carve-out).
ADMISSIONS_WORKSTREAM = "admissions"
# The source tag an actionable-feedback Decision-Queue item carries.
ADMISSIONS_FEEDBACK_SOURCE = "admissions_feedback"
# The title prefix the objection→content brief carries in the Content calendar.
BRIEF_TITLE_PREFIX = "Brief from admissions"

# The owner-routing token an OPERATOR must own to WRITE admissions state. A LEADER/ADMIN
# may write any; the demo operator owns ``admissions``, so the demo operator may write.
# Named wire tokens, not tunables (INV-11 carve-out, mirroring field_events).
ADMISSIONS_OWNER_WORKSTREAM = "admissions"
DEMO_OPERATOR_WORKSTREAM = "admissions"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# Feedback PATCH actions → the resulting status (named wire tokens, INV-11 carve-out).
_FEEDBACK_ACTIONS: dict[str, str] = {"action": "actioned", "close": "closed"}

# Dependency aliases (Annotated keeps the call in the type — ruff B008).
StoreDep = Annotated[AdmissionsStore, Depends(get_admissions_store)]
ContentStoreDep = Annotated[ContentMetricsStore, Depends(get_content_metrics_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
SentimentDep = Annotated[SentimentAdapter, Depends(get_sentiment_adapter_dep)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# The leadership gate (leader/admin) for the feedback action/close path — leadership
# input on what the listening post surfaces. Built ONCE at module level so FastAPI
# resolves it from the route's PEP-563 annotation (the decisions.py pattern).
_LEADERSHIP_GUARD = require_role("leader", "admin")
LeadershipDep = Annotated[Principal, Depends(_LEADERSHIP_GUARD)]


# ===========================================================================
# Owner gate — copied from app.api.field_events (the IDOR/spoof posture, INV-1).
# ===========================================================================
def _operator_workstream(principal: Principal) -> str:
    """The single workstream an OPERATOR owns (keyed by the verified agent_id only)."""
    if principal.agent_id is not None:
        return OPERATOR_WORKSTREAMS.get(str(principal.agent_id), DEMO_OPERATOR_WORKSTREAM)
    return DEMO_OPERATOR_WORKSTREAM


def _require_admissions_owner(principal: Principal) -> None:
    """OWNER gate for every admissions WRITE — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the workstream
    they own is ``admissions``; a foreign operator is 403. The verified ROLE decides —
    never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    if _operator_workstream(principal) != ADMISSIONS_OWNER_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {ADMISSIONS_OWNER_WORKSTREAM!r} workstream",
        )


# ===========================================================================
# Wire models.
# ===========================================================================
class ObjectionModel(BaseModel):
    """One objection-log row over the wire (synthetic verbatim; INV-1)."""

    objection_id: UUID
    theme: str
    week_count: int
    cumulative_count: int
    trend: str
    source: str
    example_quote: str
    persona: str
    urgency: str

    @classmethod
    def of(cls, o: Objection) -> ObjectionModel:
        return cls(
            objection_id=o.objection_id,
            theme=o.theme,
            week_count=o.week_count,
            cumulative_count=o.cumulative_count,
            trend=o.trend,
            source=o.source,
            example_quote=o.example_quote,
            persona=o.persona,
            urgency=o.urgency,
        )


class VoiceQuoteModel(BaseModel):
    """One voice-quote row over the wire (synthetic verbatim; INV-1)."""

    quote_id: UUID
    quote: str
    sentiment: str
    theme: str
    source: str
    is_quote_of_week: bool

    @classmethod
    def of(cls, q: VoiceQuote) -> VoiceQuoteModel:
        return cls(
            quote_id=q.quote_id,
            quote=q.quote,
            sentiment=q.sentiment,
            theme=q.theme,
            source=q.source,
            is_quote_of_week=q.is_quote_of_week,
        )


class FeedbackModel(BaseModel):
    """One feedback-loop item over the wire."""

    item_id: UUID
    summary: str
    category: str
    status: str
    actionable: bool
    owner: str
    decision_id: UUID | None
    created_at: datetime
    actioned_at: datetime | None

    @classmethod
    def of(cls, f: FeedbackItem) -> FeedbackModel:
        return cls(
            item_id=f.item_id,
            summary=f.summary,
            category=f.category,
            status=f.status,
            actionable=f.actionable,
            owner=f.owner,
            decision_id=f.decision_id,
            created_at=f.created_at,
            actioned_at=f.actioned_at,
        )


class AdmissionStatModel(BaseModel):
    """One week's admission funnel counters over the wire."""

    week_of: str
    applicants: int
    shadow_days: int
    offers: int
    deposits: int


class BridgeModel(BaseModel):
    """One content-bridge row over the wire (the per-brief produced status + freq drop)."""

    bridge_id: UUID
    objection_theme: str
    brief_entry_id: UUID | None
    produced: bool
    surfaced_at: datetime
    published_at: datetime | None
    freq_before: int
    freq_after: int | None
    frequency_decreased: bool


class OverviewResponse(BaseModel):
    """The 9a overview rollup."""

    weekly_stats: list[AdmissionStatModel]
    top_objections: list[ObjectionModel]
    objection_trend: dict[str, str]
    feedback_open_count: int
    notable_quotes: list[VoiceQuoteModel]
    objection_to_resolution_days: float
    bridge_hit_rate: dict[str, Any]


class VoiceResponse(BaseModel):
    """The 9d voice feed + quote-of-the-week + sentiment score/trend (honest source)."""

    quotes: list[VoiceQuoteModel]
    quote_of_week: VoiceQuoteModel | None
    quote_sentiment: dict[str, Any]
    feed_sentiment: dict[str, Any]
    sentiment_source_mode: str


class FeedbackResponse(BaseModel):
    """The 9e feedback items + the closure rate."""

    items: list[FeedbackModel]
    closure_rate: dict[str, Any]


class BridgeResponse(BaseModel):
    """The 9c content-bridge hit rate + per-brief rows."""

    bridges: list[BridgeModel]
    hit_rate: dict[str, Any]


class BriefRequest(BaseModel):
    """Body for ``POST /admissions/objections/{id}/brief`` (optional title override)."""

    title: str | None = None


class BriefResponse(BaseModel):
    """The created Content DRAFT brief + the recorded bridge id (CROSS-LINK 1)."""

    entry_id: UUID
    title: str
    channel: str
    status: str
    bridge_id: UUID
    theme: str


class FeedbackCreateRequest(BaseModel):
    """Body for ``POST /admissions/feedback`` — file a "marketing needs to know X" item.

    There is DELIBERATELY no ``owner``/``raised_by`` field: the route stamps them from the
    VERIFIED principal, never from the body (the IDOR/spoof posture, INV-1).
    """

    summary: str = Field(min_length=1)
    category: str
    actionable: bool = False
    recommendation: str = ""


class FeedbackPatchRequest(BaseModel):
    """Body for ``PATCH /admissions/feedback/{id}`` — action or close a feedback item."""

    action: str


# ===========================================================================
# READ paths — open to any authenticated seat.
# ===========================================================================
def _stat_model(stat: Any) -> AdmissionStatModel:
    return AdmissionStatModel(
        week_of=stat.week_of.isoformat(),
        applicants=stat.applicants,
        shadow_days=stat.shadow_days,
        offers=stat.offers,
        deposits=stat.deposits,
    )


def _bridge_model(b: ContentBridge) -> BridgeModel:
    decreased = (
        b.freq_after is not None and b.published_at is not None and b.freq_after < b.freq_before
    )
    return BridgeModel(
        bridge_id=b.bridge_id,
        objection_theme=b.objection_theme,
        brief_entry_id=b.brief_entry_id,
        produced=b.produced,
        surfaced_at=b.surfaced_at,
        published_at=b.published_at,
        freq_before=b.freq_before,
        freq_after=b.freq_after,
        frequency_decreased=decreased,
    )


@router.get("/admissions/overview", response_model=OverviewResponse)
def overview(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
) -> OverviewResponse:
    """The 9a overview rollup — computed, never faked (any authenticated seat)."""
    objections = store.list_objections(program)
    bridges = store.list_content_bridges(program)
    feedback = store.list_feedback(program)
    quotes = store.list_voice_quotes(program)

    top = core.top_objections(objections, n=params.admissions.top_objections_n)
    notable = [q for q in quotes if q.is_quote_of_week] + [
        q for q in quotes if not q.is_quote_of_week
    ]
    return OverviewResponse(
        weekly_stats=[_stat_model(s) for s in store.list_admission_stats(program)],
        top_objections=[ObjectionModel.of(o) for o in top],
        objection_trend=core.objection_trend(objections),
        feedback_open_count=sum(1 for f in feedback if f.status == "open"),
        notable_quotes=[VoiceQuoteModel.of(q) for q in notable[:3]],
        objection_to_resolution_days=core.objection_to_resolution_time(bridges),
        bridge_hit_rate=core.bridge_hit_rate(bridges),
    )


@router.get("/admissions/objections", response_model=list[ObjectionModel])
def list_objections(
    store: StoreDep,
    program: ProgramDep,
    theme: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "frequency",
) -> list[ObjectionModel]:
    """The 9b objection log — theme/source filters + sort by frequency (any seat)."""
    rows = store.list_objections(program)
    if theme is not None:
        rows = [o for o in rows if o.theme == theme]
    if source is not None:
        rows = [o for o in rows if o.source == source]
    if sort == "frequency":
        rows = sorted(rows, key=lambda o: o.week_count, reverse=True)
    return [ObjectionModel.of(o) for o in rows]


@router.get("/admissions/voice", response_model=VoiceResponse)
def voice(
    store: StoreDep,
    sentiment: SentimentDep,
    program: ProgramDep,
    params: ParamsDep,
) -> VoiceResponse:
    """The 9d voice feed + quote-of-the-week + sentiment score/trend (honest source).

    ``quote_sentiment`` is computed over the SEEDED voice quotes; ``feed_sentiment`` is the
    §7.5 adapter's aggregate summary over the recent ``trend_weeks`` window — its
    ``source_mode`` is surfaced honestly (``placeholder`` in v1, never ``live_feed``;
    INV-6/INV-9). No live feed is polled.
    """
    quotes = store.list_voice_quotes(program)
    now = datetime.now(UTC)
    window = SentimentWindow(
        start=(now - timedelta(weeks=params.admissions.trend_weeks)).date().isoformat(),
        end=now.date().isoformat(),
    )
    summary = sentiment.fetch(window)
    return VoiceResponse(
        quotes=[VoiceQuoteModel.of(q) for q in quotes],
        quote_of_week=(
            VoiceQuoteModel.of(qow) if (qow := store.get_quote_of_week(program)) else None
        ),
        quote_sentiment=core.sentiment_ratio(quotes),
        feed_sentiment=core.sentiment_ratio(summary),
        sentiment_source_mode=summary.source_mode,
    )


@router.get("/admissions/feedback", response_model=FeedbackResponse)
def list_feedback(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
) -> FeedbackResponse:
    """The 9e feedback items + the closure rate (any authenticated seat)."""
    items = store.list_feedback(program)
    return FeedbackResponse(
        items=[FeedbackModel.of(f) for f in items],
        closure_rate=core.feedback_closure_rate(
            items, now=datetime.now(UTC), sla_days=params.admissions.sla_closure_days
        ),
    )


@router.get("/admissions/bridge", response_model=BridgeResponse)
def bridge(
    store: StoreDep,
    program: ProgramDep,
) -> BridgeResponse:
    """The 9c content-bridge hit rate + per-brief produced status (any seat)."""
    bridges = store.list_content_bridges(program)
    return BridgeResponse(
        bridges=[_bridge_model(b) for b in bridges],
        hit_rate=core.bridge_hit_rate(bridges),
    )


# ===========================================================================
# WRITE paths — owner-gated (admissions) / leadership-gated.
# ===========================================================================
@router.post("/admissions/objections/{objection_id}/brief", response_model=BriefResponse)
def objection_to_brief(
    objection_id: UUID,
    body: BriefRequest,
    store: StoreDep,
    content_store: ContentStoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> BriefResponse:
    """Turn an objection into a Content DRAFT brief + record a bridge [CROSS-LINK 1].

    Owner-gated (admissions). Creates one ``content_calendar_entry`` in DRAFT status via
    the Content store (``owner="admissions"`` so Content shows "brief from admissions") and
    records a ``content_bridge`` row tracking the brief + the objection's current weekly
    frequency (``freq_before``). 404 on an unknown objection.
    """
    _require_admissions_owner(principal)
    objection = next(
        (o for o in store.list_objections(program) if o.objection_id == objection_id), None
    )
    if objection is None:
        raise HTTPException(status_code=404, detail="objection not found")

    now = datetime.now(UTC)
    channel = params.content.channels[0] if params.content.channels else "email"
    title = body.title or f"{BRIEF_TITLE_PREFIX}: {objection.theme}"
    entry = content_store.upsert_calendar_entry(
        program,
        title=title,
        channel=channel,
        scheduled_date=now.date(),
        status=STATUS_DRAFT,
        piece_ref=objection.theme,
        owner=ADMISSIONS_OWNER_WORKSTREAM,
    )
    bridge_row = store.upsert_bridge(
        program,
        objection_theme=objection.theme,
        brief_entry_id=entry.entry_id,
        produced=False,
        surfaced_at=now,
        freq_before=objection.week_count,
    )
    return BriefResponse(
        entry_id=entry.entry_id,
        title=entry.title,
        channel=entry.channel,
        status=entry.status,
        bridge_id=bridge_row.bridge_id,
        theme=objection.theme,
    )


@router.post("/admissions/feedback", response_model=FeedbackModel)
def create_feedback(
    body: FeedbackCreateRequest,
    store: StoreDep,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> FeedbackModel:
    """File a feedback item; when actionable, enqueue an admissions decision [CROSS-LINK 2].

    Owner-gated (admissions). ``category`` must be one of
    ``params.admissions.feedback_categories`` (a clean 422 otherwise; fail-closed, INV-2).
    When ``actionable`` the route ALSO enqueues ONE open ``admissions_feedback`` decision on
    the ``admissions`` workstream (``raised_by`` STAMPED from the verified principal) and
    stores the returned ``decision_id`` on the item. ``owner`` is server-stamped.
    """
    _require_admissions_owner(principal)
    if body.category not in params.admissions.feedback_categories:
        raise HTTPException(
            status_code=422,
            detail=(
                f"category must be one of {params.admissions.feedback_categories}, "
                f"got {body.category!r}"
            ),
        )

    decision_id: UUID | None = None
    if body.actionable:
        if ADMISSIONS_WORKSTREAM not in WORKSTREAMS:  # defensive (the token is canonical)
            raise HTTPException(status_code=500, detail="admissions workstream not configured")
        decision = flag_decision(
            decisions_store,
            program,
            source=ADMISSIONS_FEEDBACK_SOURCE,
            payload={"summary": body.summary, "category": body.category},
            question=f"Marketing needs to know: {body.summary}",
            raised_by=_actor_token(principal),
            workstream=ADMISSIONS_WORKSTREAM,
            recommendation=body.recommendation,
        )
        decision_id = decision.id

    item = store.create_feedback(
        program,
        summary=body.summary,
        category=body.category,
        status="open",
        actionable=body.actionable,
        owner=ADMISSIONS_OWNER_WORKSTREAM,
        decision_id=decision_id,
        created_at=datetime.now(UTC),
    )
    return FeedbackModel.of(item)


@router.patch("/admissions/feedback/{item_id}", response_model=FeedbackModel)
def patch_feedback(
    item_id: UUID,
    body: FeedbackPatchRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: LeadershipDep,
) -> FeedbackModel:
    """Action or close a feedback item — LEADER/ADMIN only (leadership input).

    ``action`` is ``action`` (→ status ``actioned``) or ``close`` (→ status ``closed``); an
    unknown action is a clean 422 (fail-closed, INV-2). Sets ``actioned_at`` to now on the
    first transition out of ``open``. 404 on an unknown item.
    """
    new_status = _FEEDBACK_ACTIONS.get(body.action)
    if new_status is None:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {tuple(_FEEDBACK_ACTIONS)}, got {body.action!r}",
        )
    try:
        updated = store.update_feedback(
            program,
            item_id,
            status=new_status,
            actioned_at=datetime.now(UTC),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="feedback item not found") from exc
    return FeedbackModel.of(updated)

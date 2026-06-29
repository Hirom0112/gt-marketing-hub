"""Website & Digital-Analytics endpoints (Module 13) — the GA4 surface + three cross-links.

The composition layer that wires the website-analytics surface into the pure core
(``app.core.website``), the GA4 boundary (``app.adapters.analytics`` — a STOOD-IN
simulated adapter in v1, ``source_mode="simulated"``; INV-6/INV-9), the Hub-owned
leadership-input store (``app.data.website_store``), and the THREE cross-links the spec
mandates:

  * GET  /website/overview   — the 13a hero rollup (sessions/pageviews, new-vs-returning,
    bounce + duration, PDF downloads, top landing pages) + open leadership-input counts.
  * GET  /website/subpages   — the 13b subpage table (site / page_type filters + sort) with
    per-page weekly trend + refresh-candidate flag.
  * GET  /website/traffic    — the 13c channel breakdown + social platform split +
    source×page matrix + the UTM source VALIDATION (CROSS-LINK → Module 7 CRM Ops).
  * GET  /website/downloads  — the 13d PDF/asset tracking (ranked + weekly/cumulative + WoW).
  * GET  /website/paths      — the 13e conversion paths (key pages, homepage flows,
    cross-site flow, landing→application funnel drop-off).
  * GET  /website/inputs     — the leadership-input panel (page flags + analysis requests).
  * POST /website/pages/flag — LEADERSHIP: flag an underperforming page for a content
    refresh → a Content calendar DRAFT brief (CROSS-LINK → Module 3) + a ``website``
    Decision-Queue card (CROSS-LINK → Module 11); persists the linked ids.
  * POST /website/analysis   — LEADERSHIP: request analysis on a page/campaign → a
    ``website`` Decision-Queue card (CROSS-LINK → Module 11); persists the decision id.
  * PATCH /website/pages/flag/{id} | /website/analysis/{id} — resolve an input.

The website METRICS are read off the GA4 adapter, never persisted; only the leadership
inputs (flags/requests) are Hub-owned and persisted (migration 0043). Writes are
LEADERSHIP-gated (leader/admin — the spec's "Leadership input"); ``raised_by`` is STAMPED
from the VERIFIED principal, never the body (the IDOR/spoof posture, INV-1). This module
may import ``app.api`` / ``app.adapters`` (it is the composition root); ``app/core/`` stays
pure. No live external read/send is ever made here (INV-9).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.adapters.analytics.base import (
    AnalyticsAdapter,
    AnalyticsSnapshot,
    AnalyticsWindow,
    DownloadMetric,
    PageMetric,
    SiteMetric,
)
from app.api.decisions import _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_analytics_adapter_dep,
    get_content_metrics_store,
    get_decisions_store,
    get_params,
    get_principal,
    get_website_store,
    require_role,
)
from app.core import website as core
from app.core.content_analytics import STATUS_DRAFT
from app.core.params import Params
from app.core.program import Program
from app.data.content_metrics_store import ContentMetricsStore
from app.data.decisions_store import WORKSTREAMS, DecisionsStore
from app.data.website_store import AnalysisRequest, PageFlag, WebsiteStore

router = APIRouter(tags=["website"])

# The Decision-Queue workstream every website escalation belongs to (one of
# decisions_store.WORKSTREAMS). Named, not a bare literal (INV-11 carve-out).
WEBSITE_WORKSTREAM = "website"
# The source tags the two website Decision-Queue cards carry.
PAGE_FLAG_SOURCE = "website_page_flag"
ANALYSIS_SOURCE = "website_analysis_request"
# The owner routing token the persisted inputs + the Content refresh brief carry.
WEBSITE_OWNER = "website"
# The title prefix the page-refresh brief carries in the Content calendar (CROSS-LINK 1).
REFRESH_TITLE_PREFIX = "Refresh"

# Resolve action → the resulting status (named wire tokens, INV-11 carve-out).
_RESOLVE_ACTIONS: dict[str, str] = {"resolve": "resolved"}

# Dependency aliases (Annotated keeps the call in the type — ruff B008).
AdapterDep = Annotated[AnalyticsAdapter, Depends(get_analytics_adapter_dep)]
StoreDep = Annotated[WebsiteStore, Depends(get_website_store)]
ContentStoreDep = Annotated[ContentMetricsStore, Depends(get_content_metrics_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# The leadership gate (leader/admin) for every website WRITE — the spec's "Leadership
# input". Built ONCE at module level so FastAPI resolves it from the route annotation.
_LEADERSHIP_GUARD = require_role("leader", "admin")
LeadershipDep = Annotated[Principal, Depends(_LEADERSHIP_GUARD)]


def _window(params: Params) -> AnalyticsWindow:
    """The reporting window (``params.website.analytics_window_weeks`` back from now)."""
    now = datetime.now(UTC)
    start = now - timedelta(weeks=params.website.analytics_window_weeks)
    return AnalyticsWindow(start=start.date().isoformat(), end=now.date().isoformat())


def _snapshot(adapter: AnalyticsAdapter, params: Params) -> AnalyticsSnapshot:
    """Read one aggregate GA4 snapshot for the active window (the single boundary call)."""
    return adapter.snapshot(_window(params))


# ===========================================================================
# Wire models — the leadership-input rows + the per-page subpage row (metrics rows
# reuse the adapter's aggregate models directly; the API is the composition root).
# ===========================================================================
class SubpageModel(BaseModel):
    """One subpage row over the wire — the adapter metrics + derived trend/flag (13b)."""

    page_path: str
    site: str
    page_type: str
    pageviews: int
    prev_pageviews: int
    unique_visitors: int
    avg_time_on_page_s: float
    bounce_rate: float
    exit_rate: float
    conversions: int
    trend_pct: int
    refresh_candidate: bool

    @classmethod
    def of(cls, p: PageMetric, *, bounce_warn_pct: float) -> SubpageModel:
        return cls(
            page_path=p.page_path,
            site=p.site,
            page_type=p.page_type,
            pageviews=p.pageviews,
            prev_pageviews=p.prev_pageviews,
            unique_visitors=p.unique_visitors,
            avg_time_on_page_s=p.avg_time_on_page_s,
            bounce_rate=p.bounce_rate,
            exit_rate=p.exit_rate,
            conversions=p.conversions,
            trend_pct=core.page_trend_pct(p),
            refresh_candidate=p.bounce_rate >= bounce_warn_pct,
        )


class TopPageModel(BaseModel):
    """One top-landing-page row for the overview (path/site/type + traffic + trend)."""

    page_path: str
    site: str
    page_type: str
    pageviews: int
    trend_pct: int


class DownloadModel(BaseModel):
    """One download row over the wire (13d)."""

    file_name: str
    weekly_count: int
    cumulative_count: int
    prev_weekly_count: int
    referring_page: str
    source: str

    @classmethod
    def of(cls, d: DownloadMetric) -> DownloadModel:
        return cls(
            file_name=d.file_name,
            weekly_count=d.weekly_count,
            cumulative_count=d.cumulative_count,
            prev_weekly_count=d.prev_weekly_count,
            referring_page=d.referring_page,
            source=d.source,
        )


class PageFlagModel(BaseModel):
    """One persisted page-flag row over the wire."""

    flag_id: UUID
    page_path: str
    site: str
    reason: str
    status: str
    brief_entry_id: UUID | None
    decision_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None

    @classmethod
    def of(cls, f: PageFlag) -> PageFlagModel:
        return cls(
            flag_id=f.flag_id,
            page_path=f.page_path,
            site=f.site,
            reason=f.reason,
            status=f.status,
            brief_entry_id=f.brief_entry_id,
            decision_id=f.decision_id,
            created_at=f.created_at,
            resolved_at=f.resolved_at,
        )


class AnalysisRequestModel(BaseModel):
    """One persisted analysis-request row over the wire."""

    request_id: UUID
    target: str
    target_kind: str
    question: str
    status: str
    decision_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None

    @classmethod
    def of(cls, r: AnalysisRequest) -> AnalysisRequestModel:
        return cls(
            request_id=r.request_id,
            target=r.target,
            target_kind=r.target_kind,
            question=r.question,
            status=r.status,
            decision_id=r.decision_id,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
        )


class OverviewResponse(BaseModel):
    """The 13a overview rollup."""

    source_mode: str
    site_rollup: dict[str, Any]
    sites: list[SiteMetric]
    download_summary: dict[str, Any]
    top_downloads: list[DownloadModel]
    top_landing_pages: list[TopPageModel]
    refresh_candidate_count: int
    open_flag_count: int
    open_request_count: int


class SubpagesResponse(BaseModel):
    """The 13b subpage table (filtered + sorted) + the available filter values."""

    source_mode: str
    pages: list[SubpageModel]
    sites: list[str]
    page_types: list[str]
    bounce_warn_pct: float


class TrafficResponse(BaseModel):
    """The 13c traffic breakdown + source×page matrix + UTM validation (→ CRM Ops)."""

    source_mode: str
    breakdown: dict[str, Any]
    source_pages: list[dict[str, Any]]
    utm_validation: dict[str, Any]


class DownloadsResponse(BaseModel):
    """The 13d PDF/asset tracking — ranked + summarised."""

    source_mode: str
    downloads: list[DownloadModel]
    summary: dict[str, Any]


class PathsResponse(BaseModel):
    """The 13e conversion paths — funnel + key pages + flows + cross-site."""

    source_mode: str
    funnel: list[dict[str, Any]]
    key_conversion_pages: list[dict[str, Any]]
    path_flows: list[dict[str, Any]]
    cross_site_flows: list[dict[str, Any]]


class InputsResponse(BaseModel):
    """The leadership-input panel (page flags + analysis requests)."""

    page_flags: list[PageFlagModel]
    analysis_requests: list[AnalysisRequestModel]
    open_flag_count: int
    open_request_count: int


class FlagPageRequest(BaseModel):
    """Body for ``POST /website/pages/flag`` — flag an underperforming page.

    No ``owner``/``raised_by`` field: the route stamps them from the VERIFIED principal,
    never the body (the IDOR/spoof posture, INV-1).
    """

    page_path: str = Field(min_length=1)
    site: str
    reason: str = Field(min_length=1)
    create_brief: bool = True
    raise_decision: bool = True
    recommendation: str = ""


class FlagPageResponse(BaseModel):
    """The created page flag + the brief/decision it produced (CROSS-LINK 1 + 2)."""

    flag: PageFlagModel
    brief_entry_id: UUID | None
    brief_title: str | None
    decision_id: UUID | None


class AnalysisRequestCreate(BaseModel):
    """Body for ``POST /website/analysis`` — request analysis on a page/campaign."""

    target: str = Field(min_length=1)
    target_kind: str = "page"
    question: str = Field(min_length=1)
    recommendation: str = ""


class ResolveRequest(BaseModel):
    """Body for the resolve PATCH endpoints."""

    action: str = "resolve"


# ===========================================================================
# READ paths — open to any authenticated seat.
# ===========================================================================
@router.get("/website/overview", response_model=OverviewResponse)
def overview(
    adapter: AdapterDep,
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
) -> OverviewResponse:
    """The 13a overview rollup — computed off the GA4 snapshot, never faked (any seat)."""
    snap = _snapshot(adapter, params)
    flags = store.list_page_flags(program)
    requests = store.list_analysis_requests(program)
    top_downloads = sorted(snap.downloads, key=lambda d: d.weekly_count, reverse=True)[:5]
    top_pages = core.top_landing_pages(snap.pages, n=params.website.top_landing_n)
    candidates = core.refresh_candidates(snap.pages, bounce_warn_pct=params.website.bounce_warn_pct)
    return OverviewResponse(
        source_mode=snap.source_mode,
        site_rollup=core.site_rollup(snap.sites),
        sites=list(snap.sites),
        download_summary=core.download_summary(snap.downloads),
        top_downloads=[DownloadModel.of(d) for d in top_downloads],
        top_landing_pages=[
            TopPageModel(
                page_path=p.page_path,
                site=p.site,
                page_type=p.page_type,
                pageviews=p.pageviews,
                trend_pct=core.page_trend_pct(p),
            )
            for p in top_pages
        ],
        refresh_candidate_count=len(candidates),
        open_flag_count=sum(1 for f in flags if f.status == "open"),
        open_request_count=sum(1 for r in requests if r.status == "open"),
    )


# The page-metric sort keys the subpage table supports (named wire tokens, INV-11 carve).
_SORT_KEYS = {
    "pageviews": lambda p: p.pageviews,
    "unique_visitors": lambda p: p.unique_visitors,
    "avg_time_on_page": lambda p: p.avg_time_on_page_s,
    "bounce_rate": lambda p: p.bounce_rate,
    "exit_rate": lambda p: p.exit_rate,
    "conversions": lambda p: p.conversions,
}


@router.get("/website/subpages", response_model=SubpagesResponse)
def subpages(
    adapter: AdapterDep,
    program: ProgramDep,
    params: ParamsDep,
    site: Annotated[str | None, Query()] = None,
    page_type: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "pageviews",
) -> SubpagesResponse:
    """The 13b subpage table — site/page_type filters + sort (any authenticated seat).

    An unknown ``sort`` falls back to ``pageviews`` (lenient read path); filters narrow the
    rows. Each row carries its weekly trend + whether it clears the refresh threshold.
    """
    snap = _snapshot(adapter, params)
    rows = list(snap.pages)
    if site is not None:
        rows = [p for p in rows if p.site == site]
    if page_type is not None:
        rows = [p for p in rows if p.page_type == page_type]
    key = _SORT_KEYS.get(sort, _SORT_KEYS["pageviews"])
    rows = sorted(rows, key=key, reverse=True)
    warn = params.website.bounce_warn_pct
    return SubpagesResponse(
        source_mode=snap.source_mode,
        pages=[SubpageModel.of(p, bounce_warn_pct=warn) for p in rows],
        sites=list(params.website.sites),
        page_types=list(params.website.page_types),
        bounce_warn_pct=warn,
    )


@router.get("/website/traffic", response_model=TrafficResponse)
def traffic(
    adapter: AdapterDep,
    program: ProgramDep,
    params: ParamsDep,
) -> TrafficResponse:
    """The 13c traffic breakdown + source×page matrix + UTM validation (→ CRM Ops).

    ``utm_validation`` runs the SAME ``core.utm_health.check_utm`` rule set CRM Ops uses
    over the tagged campaigns at the ORIGIN of the tags — the broken count feeds the CRM-Ops
    attribution-chain (detect-only; the honesty mandate).
    """
    snap = _snapshot(adapter, params)
    source_pages = sorted(snap.source_pages, key=lambda c: c.sessions, reverse=True)
    return TrafficResponse(
        source_mode=snap.source_mode,
        breakdown=core.traffic_breakdown(snap.sources),
        source_pages=[
            {"channel": c.channel, "page_path": c.page_path, "sessions": c.sessions}
            for c in source_pages
        ],
        utm_validation=core.validate_campaign_utms(snap.campaigns, params=params),
    )


@router.get("/website/downloads", response_model=DownloadsResponse)
def downloads(
    adapter: AdapterDep,
    program: ProgramDep,
    params: ParamsDep,
) -> DownloadsResponse:
    """The 13d PDF/asset tracking — ranked by weekly downloads + the WoW summary (any seat)."""
    snap = _snapshot(adapter, params)
    ranked = sorted(snap.downloads, key=lambda d: d.weekly_count, reverse=True)
    return DownloadsResponse(
        source_mode=snap.source_mode,
        downloads=[DownloadModel.of(d) for d in ranked],
        summary=core.download_summary(snap.downloads),
    )


@router.get("/website/paths", response_model=PathsResponse)
def paths(
    adapter: AdapterDep,
    program: ProgramDep,
    params: ParamsDep,
) -> PathsResponse:
    """The 13e conversion paths — funnel drop-off + key pages + flows + cross-site (any seat)."""
    snap = _snapshot(adapter, params)
    key_pages = core.key_conversion_pages(snap.conversion_pages, n=params.website.top_landing_n)
    flows = sorted(snap.path_flows, key=lambda f: f.sessions, reverse=True)
    return PathsResponse(
        source_mode=snap.source_mode,
        funnel=core.funnel_dropoff(snap.funnel),
        key_conversion_pages=[
            {
                "page_path": p.page_path,
                "site": p.site,
                "sessions": p.sessions,
                "form_submissions": p.form_submissions,
                "submission_rate": core.conversion_page_rate(p),
            }
            for p in key_pages
        ],
        path_flows=[
            {"from_page": f.from_page, "to_page": f.to_page, "sessions": f.sessions} for f in flows
        ],
        cross_site_flows=[
            {"from_site": c.from_site, "to_site": c.to_site, "sessions": c.sessions}
            for c in snap.cross_site_flows
        ],
    )


@router.get("/website/inputs", response_model=InputsResponse)
def inputs(store: StoreDep, program: ProgramDep) -> InputsResponse:
    """The leadership-input panel — page flags + analysis requests (any seat)."""
    flags = store.list_page_flags(program)
    requests = store.list_analysis_requests(program)
    return InputsResponse(
        page_flags=[PageFlagModel.of(f) for f in flags],
        analysis_requests=[AnalysisRequestModel.of(r) for r in requests],
        open_flag_count=sum(1 for f in flags if f.status == "open"),
        open_request_count=sum(1 for r in requests if r.status == "open"),
    )


# ===========================================================================
# WRITE paths — LEADERSHIP-gated (leader/admin; the spec's "Leadership input").
# ===========================================================================
@router.post("/website/pages/flag", response_model=FlagPageResponse)
def flag_page(
    body: FlagPageRequest,
    store: StoreDep,
    content_store: ContentStoreDep,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: LeadershipDep,
) -> FlagPageResponse:
    """Flag an underperforming page → a content-refresh brief + a Decision card.

    LEADERSHIP-gated. ``site`` must be one of ``params.website.sites`` (clean 422
    otherwise; fail-closed, INV-2). When ``create_brief`` (default) it creates ONE
    ``content_calendar_entry`` DRAFT (``owner="website"``, title "Refresh: <path>") so
    Content shows a brief from the website surface [CROSS-LINK → Module 3]. When
    ``raise_decision`` (default) it enqueues ONE open ``website`` Decision-Queue card
    [CROSS-LINK → Module 11], ``raised_by`` STAMPED from the verified principal. The
    persisted ``page_flag`` records both linked ids.
    """
    if body.site not in params.website.sites:
        raise HTTPException(
            status_code=422,
            detail=f"site must be one of {params.website.sites}, got {body.site!r}",
        )

    brief_entry_id: UUID | None = None
    brief_title: str | None = None
    if body.create_brief:
        channel = params.content.channels[0] if params.content.channels else "email"
        brief_title = f"{REFRESH_TITLE_PREFIX}: {body.page_path}"
        entry = content_store.upsert_calendar_entry(
            program,
            title=brief_title,
            channel=channel,
            scheduled_date=date.today(),
            status=STATUS_DRAFT,
            piece_ref=body.page_path,
            owner=WEBSITE_OWNER,
        )
        brief_entry_id = entry.entry_id

    decision_id: UUID | None = None
    if body.raise_decision:
        if WEBSITE_WORKSTREAM not in WORKSTREAMS:  # defensive (the token is canonical)
            raise HTTPException(status_code=500, detail="website workstream not configured")
        decision = flag_decision(
            decisions_store,
            program,
            source=PAGE_FLAG_SOURCE,
            payload={"page_path": body.page_path, "site": body.site, "reason": body.reason},
            question=f"Refresh underperforming page {body.page_path}?",
            raised_by=_actor_token(principal),
            workstream=WEBSITE_WORKSTREAM,
            recommendation=body.recommendation,
        )
        decision_id = decision.id

    flag = store.create_page_flag(
        program,
        page_path=body.page_path,
        site=body.site,
        reason=body.reason,
        status="open",
        brief_entry_id=brief_entry_id,
        decision_id=decision_id,
        owner=WEBSITE_OWNER,
        created_at=datetime.now(UTC),
    )
    return FlagPageResponse(
        flag=PageFlagModel.of(flag),
        brief_entry_id=brief_entry_id,
        brief_title=brief_title,
        decision_id=decision_id,
    )


@router.post("/website/analysis", response_model=AnalysisRequestModel)
def request_analysis(
    body: AnalysisRequestCreate,
    store: StoreDep,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    principal: LeadershipDep,
) -> AnalysisRequestModel:
    """Request analysis on a page/campaign → a Decision card [CROSS-LINK → Module 11].

    LEADERSHIP-gated. ``target_kind`` must be ``page`` or ``campaign`` (clean 422
    otherwise; fail-closed, INV-2). Enqueues ONE open ``website`` Decision-Queue card
    (``raised_by`` STAMPED from the verified principal) and stores the returned
    ``decision_id`` on the persisted request.
    """
    if body.target_kind not in ("page", "campaign"):
        raise HTTPException(
            status_code=422,
            detail=f"target_kind must be 'page' or 'campaign', got {body.target_kind!r}",
        )
    decision = flag_decision(
        decisions_store,
        program,
        source=ANALYSIS_SOURCE,
        payload={"target": body.target, "target_kind": body.target_kind},
        question=f"Analysis requested on {body.target_kind} {body.target!r}: {body.question}",
        raised_by=_actor_token(principal),
        workstream=WEBSITE_WORKSTREAM,
        recommendation=body.recommendation,
    )
    request = store.create_analysis_request(
        program,
        target=body.target,
        target_kind=body.target_kind,
        question=body.question,
        status="open",
        decision_id=decision.id,
        owner=WEBSITE_OWNER,
        created_at=datetime.now(UTC),
    )
    return AnalysisRequestModel.of(request)


@router.patch("/website/pages/flag/{flag_id}", response_model=PageFlagModel)
def resolve_flag(
    flag_id: UUID,
    body: ResolveRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: LeadershipDep,
) -> PageFlagModel:
    """Resolve a page flag — LEADER/ADMIN only. 404 on unknown; 422 on a bad action."""
    if _RESOLVE_ACTIONS.get(body.action) is None:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {tuple(_RESOLVE_ACTIONS)}, got {body.action!r}",
        )
    try:
        updated = store.update_page_flag(
            program, flag_id, status="resolved", resolved_at=datetime.now(UTC)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="page flag not found") from exc
    return PageFlagModel.of(updated)


@router.patch("/website/analysis/{request_id}", response_model=AnalysisRequestModel)
def resolve_analysis(
    request_id: UUID,
    body: ResolveRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: LeadershipDep,
) -> AnalysisRequestModel:
    """Resolve an analysis request — LEADER/ADMIN only. 404 on unknown; 422 on bad action."""
    if _RESOLVE_ACTIONS.get(body.action) is None:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {tuple(_RESOLVE_ACTIONS)}, got {body.action!r}",
        )
    try:
        updated = store.update_analysis_request(
            program, request_id, status="resolved", resolved_at=datetime.now(UTC)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="analysis request not found") from exc
    return AnalysisRequestModel.of(updated)

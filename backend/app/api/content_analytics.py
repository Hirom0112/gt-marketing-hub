"""Content & Thought-Leadership analytics endpoints (Module 3) — overview / calendar /
performance / testimonial-stubs + owner-gated calendar writes + advisory brand-voice.

The composition layer wiring the Module-3 deterministic core
(:mod:`app.core.content_analytics`), the metrics store seam
(:mod:`app.data.content_metrics_store`), the content library (the cross-module
testimonial drafts), and the V-4 brand judge behind REST. Thin by design: every
derivation is pure/owned core (INV-2); this router only adapts the store rows in, gates
the writes by owner, and shapes the JSON the Content UI consumes.

READS (any authenticated seat):
  ``GET /content/overview``          — the 3a hero rollup (productions in flight +
    on-track, this-week publish, X conversion %, top performer, channel stand-ins,
    library count, recent testimonial-stub count).
  ``GET /content/calendar``          — the month-grid entries + detected conflict dates.
  ``GET /content/performance``       — channel breakdown + piece rankings +
    content-to-conversion (with honest source_kind / utm labels for provenance).
  ``GET /content/testimonial-stubs`` — the DRAFT library assets sourced from grassroots
    testimonials (which ``library.search()`` hides; this is the narrow draft read).

WRITES (OWNER-gated — an operator must OWN the ``content`` workstream; leaders/admins
may write any; everyone else is 403. Identity is ALWAYS from the verified principal):
  ``POST /content/calendar/reschedule`` — drag-to-reschedule persistence.
  ``POST /content/calendar/entry``      — create/update a calendar entry.

BRAND VOICE (non-blocking suggest-edits — INV-2 PROPOSAL; does NOT write state):
  ``POST /content/brand-voice/suggest`` — inline rewrite suggestions + an overall brand
    score, reusing the V-4 brand judge (LLM-backed when available, deterministic
    heuristic otherwise). Advisory only — suggested, never applied.

This module may import ``app.core`` / ``app.ai`` / ``app.marketing`` (it is the
composition root); ``app/core/`` stays pure. No live LLM call is ever made here — the
brand judge degrades without a key and tests inject the heuristic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.brand_judge import _OFFBRAND_TERMS, heuristic_brand_score
from app.ai.schemas.brand import BrandRule, RuleType
from app.api.deps import (
    Principal,
    get_active_brand_rules,
    get_active_program,
    get_brand_judge,
    get_content_library_dep,
    get_content_metrics_store,
    get_params,
    get_principal,
    get_settings_dep,
)
from app.core.content_analytics import (
    CalendarEntryView,
    ChannelMetricView,
    PiecePerfView,
    channel_breakdown,
    detect_calendar_conflicts,
    overview_rollup,
    piece_rankings,
)
from app.core.eval_gate import BrandJudge
from app.core.params import Params
from app.core.program import Program
from app.core.settings import Settings
from app.data.content_metrics_store import (
    CalendarEntry,
    ChannelMetric,
    ContentMetricsStore,
    PiecePerf,
)
from app.marketing.library import ContentLibrary

router = APIRouter(tags=["content-analytics"])

# The workstream the Content module owns (one of decisions_store.WORKSTREAMS). The
# OPERATOR who owns this workstream may write; a foreign operator is 403. Named wire
# tokens, not tunables (INV-11 carve-out, mirroring grassroots.GRASSROOTS_WORKSTREAM).
CONTENT_WORKSTREAM = "content"
DEMO_OPERATOR_WORKSTREAM = "content"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# The X/Twitter channel token (a fixed wire identifier, the INV-11 carve-out for named
# tokens — the label's tunable home is params.content.channels). The overview surfaces
# this channel's COMPUTED conversion rate (the "42% conversion engine").
X_CHANNEL = "x"

# The cross-module source tag the grassroots testimonial feeder stamps on its DRAFT
# library assets (mirrors grassroots.TESTIMONIAL_SOURCE — a fixed wire token, INV-11
# carve-out). The testimonial-stubs read lists the drafts carrying this source_ref.
TESTIMONIAL_SOURCE = "grassroots_testimonial"

# Deterministic hype → on-brand rewrite suggestions for the advisory brand-voice path
# (a PROPOSAL, INV-2). Keyed by the off-brand term; an off-brand term with no mapped
# rewrite suggests a removal (``after=""``). These are DATA (the GT "concrete over hype"
# voice as rewrites), not scoring tunables — the score bar lives in params (V-4).
_HYPE_REWRITES: dict[str, str] = {
    "amazing": "effective",
    "incredible": "proven",
    "revolutionary": "different",
    "world-class": "rigorous",
    "best in class": "rigorous",
    "the best": "a strong fit",
    "guaranteed": "designed to",
    "guarantee": "aim to",
    "unbeatable": "strong",
    "act now": "enroll when you're ready",
    "limited time": "this enrollment window",
    "hurry": "take your time",
    "buy now": "learn more",
    "sign up today": "explore enrollment",
}

# --- dependency aliases (Annotated keeps the call in the type — ruff B008) ---
StoreDep = Annotated[ContentMetricsStore, Depends(get_content_metrics_store)]
LibraryDep = Annotated[ContentLibrary, Depends(get_content_library_dep)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
BrandJudgeDep = Annotated["BrandJudge | None", Depends(get_brand_judge)]
BrandRulesDep = Annotated[list[BrandRule], Depends(get_active_brand_rules)]
# Any authenticated principal (the READ path — NOT role-gated; anyone may VIEW).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]


# ===========================================================================
# Owner gate — an OPERATOR may write only when they OWN the content workstream; a
# LEADER/ADMIN may write anything; everyone else is 403. The verified ROLE decides —
# never a client claim (the IDOR/spoof posture). Mirrors app.api.grassroots.
# ===========================================================================
def _operator_workstream(principal: Principal) -> str:
    """The single workstream an OPERATOR owns (keyed by the verified agent_id only)."""
    if principal.agent_id is not None:
        return OPERATOR_WORKSTREAMS.get(str(principal.agent_id), DEMO_OPERATOR_WORKSTREAM)
    return DEMO_OPERATOR_WORKSTREAM


def _require_content_owner(principal: Principal) -> None:
    """OWNER gate for every Content write — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the
    workstream they own is ``content``; a foreign operator is 403. The verified ROLE
    decides — never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    if _operator_workstream(principal) != CONTENT_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {CONTENT_WORKSTREAM!r} workstream",
        )


# ===========================================================================
# Wire models.
# ===========================================================================
class ChannelStandinOut(BaseModel):
    """One channel's reach stand-in for the overview (with provenance)."""

    channel: str
    reach: int
    source_kind: str


class OverviewResponse(BaseModel):
    """The 3a hero rollup over the wire (real measurements, no fake deltas)."""

    productions_in_flight: int
    on_track: int
    on_track_pct: int
    this_week_publish_count: int
    top_piece_title: str | None
    top_piece_conversions: int
    x_conversion_rate_pct: int
    channel_standins: list[ChannelStandinOut]
    library_count: int
    testimonial_stub_count: int


class CalendarEntryOut(BaseModel):
    """One editorial-calendar entry over the wire."""

    entry_id: UUID
    title: str
    channel: str
    scheduled_date: date
    status: str
    piece_ref: str | None
    owner: str


class CalendarResponse(BaseModel):
    """The editorial calendar — entries + detected same-day conflict dates."""

    entries: list[CalendarEntryOut]
    conflict_dates: list[date]
    conflict_threshold: int


class ChannelBreakdownOut(BaseModel):
    """One channel's breakdown row (conversion rate COMPUTED; honest source_kind)."""

    channel: str
    reach: int
    clicks: int
    conversions: int
    conversion_rate_pct: int
    source_kind: str
    is_top: bool
    is_bottom: bool


class PieceRankingOut(BaseModel):
    """One ranked piece over the wire (utm_attributed kept visible)."""

    piece_title: str
    channel: str
    reach: int
    clicks: int
    conversions: int
    conversion_rate_pct: int
    utm_attributed: bool


class PerformanceResponse(BaseModel):
    """The performance surface — channel breakdown + piece rankings + attribution."""

    channels: list[ChannelBreakdownOut]
    top_pieces: list[PieceRankingOut]
    bottom_pieces: list[PieceRankingOut]
    # Only the UTM-attributable conversions are listed; the rest are counted honestly.
    content_to_conversion: list[PieceRankingOut]
    unattributable_count: int


class TestimonialStubOut(BaseModel):
    """One grassroots testimonial DRAFT over the wire (recently-captured stub)."""

    asset_id: str
    title: str
    body: str | None
    tags: list[str]
    source_ref: str | None
    created_at: str | None


# ----------------------------------------- write request bodies (NO identity field)
class RescheduleRequest(BaseModel):
    """Body for ``POST /content/calendar/reschedule`` — drag-to-reschedule a slot."""

    entry_id: UUID
    new_date: date


class CalendarEntryRequest(BaseModel):
    """Body for ``POST /content/calendar/entry`` — create/update a calendar entry.

    There is DELIBERATELY no ``owner`` field: the entry is stamped to the ``content``
    workstream server-side (a routing token, not identity — never a client claim).
    """

    entry_id: UUID | None = None
    title: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    scheduled_date: date
    status: str = "planned"
    piece_ref: str | None = None


class BrandVoiceRequest(BaseModel):
    """Body for ``POST /content/brand-voice/suggest`` — copy to advise on."""

    text: str = Field(min_length=1)


class SuggestionOut(BaseModel):
    """One inline brand-voice suggestion (advisory — suggested, never applied)."""

    before: str
    after: str
    rule: str
    kind: str


class BrandVoiceResponse(BaseModel):
    """The advisory brand-voice result — a PROPOSAL (INV-2), never a state write."""

    brand_score: float
    suggestions: list[SuggestionOut]
    advisory: bool
    mode: str
    note: str


# ===========================================================================
# Projection helpers (store rows → core views).
# ===========================================================================
def _calendar_views(entries: Sequence[CalendarEntry]) -> list[CalendarEntryView]:
    """Project store calendar entries onto the pure-core :class:`CalendarEntryView`."""
    return [
        CalendarEntryView(scheduled_date=e.scheduled_date, channel=e.channel, status=e.status)
        for e in entries
    ]


def _channel_views(metrics: Sequence[ChannelMetric]) -> list[ChannelMetricView]:
    """Project store channel metrics onto the pure-core :class:`ChannelMetricView`."""
    return [
        ChannelMetricView(
            channel=m.channel,
            reach=m.reach,
            clicks=m.clicks,
            conversions=m.conversions,
            source_kind=m.source_kind,
        )
        for m in metrics
    ]


def _piece_views(pieces: Sequence[PiecePerf]) -> list[PiecePerfView]:
    """Project store piece-perf rows onto the pure-core :class:`PiecePerfView`."""
    return [
        PiecePerfView(
            piece_title=p.piece_title,
            channel=p.channel,
            reach=p.reach,
            clicks=p.clicks,
            conversions=p.conversions,
            utm_attributed=p.utm_attributed,
        )
        for p in pieces
    ]


def _calendar_row(e: CalendarEntry) -> CalendarEntryOut:
    """Project a store :class:`CalendarEntry` onto the wire :class:`CalendarEntryOut`."""
    return CalendarEntryOut(
        entry_id=e.entry_id,
        title=e.title,
        channel=e.channel,
        scheduled_date=e.scheduled_date,
        status=e.status,
        piece_ref=e.piece_ref,
        owner=e.owner,
    )


def _this_week_window(today: date) -> tuple[date, date]:
    """The Mon..Sun window containing ``today`` (the injected this-week publish window)."""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


# ===========================================================================
# READ endpoints (any authenticated seat).
# ===========================================================================
@router.get("/content/overview", response_model=OverviewResponse)
def get_overview(
    store: StoreDep,
    library: LibraryDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> OverviewResponse:
    """The 3a hero rollup (any authenticated VIEW)."""
    today = datetime.now(UTC).date()
    week_start, week_end = _this_week_window(today)
    rollup = overview_rollup(
        _calendar_views(store.list_calendar(program)),
        _channel_views(store.list_channel_metrics(program)),
        _piece_views(store.list_piece_perf(program)),
        library_count=len(library.search()),
        testimonial_stub_count=len(library.list_drafts(TESTIMONIAL_SOURCE)),
        conflict_threshold=params.content.calendar.conflict_threshold,
        this_week_start=week_start,
        this_week_end=week_end,
        x_channel=X_CHANNEL,
        x_conversion_rate_fallback=params.content.x_conversion_rate,
    )
    return OverviewResponse(
        productions_in_flight=rollup.productions_in_flight,
        on_track=rollup.on_track,
        on_track_pct=rollup.on_track_pct,
        this_week_publish_count=rollup.this_week_publish_count,
        top_piece_title=rollup.top_piece_title,
        top_piece_conversions=rollup.top_piece_conversions,
        x_conversion_rate_pct=rollup.x_conversion_rate_pct,
        channel_standins=[
            ChannelStandinOut(channel=s.channel, reach=s.reach, source_kind=s.source_kind)
            for s in rollup.channel_standins
        ],
        library_count=rollup.library_count,
        testimonial_stub_count=rollup.testimonial_stub_count,
    )


@router.get("/content/calendar", response_model=CalendarResponse)
def get_calendar(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> CalendarResponse:
    """The editorial-calendar entries + the detected same-day conflict dates."""
    entries = store.list_calendar(program)
    threshold = params.content.calendar.conflict_threshold
    conflicts = detect_calendar_conflicts(_calendar_views(entries), threshold=threshold)
    return CalendarResponse(
        entries=[_calendar_row(e) for e in entries],
        conflict_dates=conflicts,
        conflict_threshold=threshold,
    )


@router.get("/content/performance", response_model=PerformanceResponse)
def get_performance(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> PerformanceResponse:
    """The channel breakdown + per-piece rankings + content-to-conversion (honest labels)."""
    channels = channel_breakdown(_channel_views(store.list_channel_metrics(program)))
    rankings = piece_rankings(
        _piece_views(store.list_piece_perf(program)),
        top_n=params.content.rankings.top_n,
        bottom_n=params.content.rankings.bottom_n,
    )
    return PerformanceResponse(
        channels=[
            ChannelBreakdownOut(
                channel=c.channel,
                reach=c.reach,
                clicks=c.clicks,
                conversions=c.conversions,
                conversion_rate_pct=c.conversion_rate_pct,
                source_kind=c.source_kind,
                is_top=c.is_top,
                is_bottom=c.is_bottom,
            )
            for c in channels
        ],
        top_pieces=[_ranking_out(r) for r in rankings.top],
        bottom_pieces=[_ranking_out(r) for r in rankings.bottom],
        content_to_conversion=[_ranking_out(r) for r in rankings.content_to_conversion],
        unattributable_count=rankings.unattributable_count,
    )


def _ranking_out(r: object) -> PieceRankingOut:
    """Project a core :class:`PieceRanking` onto the wire :class:`PieceRankingOut`."""
    # r is a content_analytics.PieceRanking (a frozen dataclass with these fields).
    return PieceRankingOut(
        piece_title=r.piece_title,  # type: ignore[attr-defined]
        channel=r.channel,  # type: ignore[attr-defined]
        reach=r.reach,  # type: ignore[attr-defined]
        clicks=r.clicks,  # type: ignore[attr-defined]
        conversions=r.conversions,  # type: ignore[attr-defined]
        conversion_rate_pct=r.conversion_rate_pct,  # type: ignore[attr-defined]
        utm_attributed=r.utm_attributed,  # type: ignore[attr-defined]
    )


@router.get("/content/testimonial-stubs", response_model=list[TestimonialStubOut])
def list_testimonial_stubs(
    library: LibraryDep,
    principal: AnyPrincipalDep,
) -> list[TestimonialStubOut]:
    """The recently-captured grassroots testimonial DRAFTs (the 3a/3e cross-module read).

    ``library.search()`` hides drafts (it returns only kept+validated assets), so this
    uses the narrow :meth:`ContentLibrary.list_drafts` read to surface the
    ``grassroots_testimonial`` stubs the content team must pick up and run through the §9
    gate before they can be kept.
    """
    return [
        TestimonialStubOut(
            asset_id=a.id,
            title=a.title,
            body=a.body,
            tags=list(a.tags),
            source_ref=a.source_ref,
            created_at=a.provenance.created_at,
        )
        for a in library.list_drafts(TESTIMONIAL_SOURCE)
    ]


# ===========================================================================
# WRITE endpoints (owner-gated).
# ===========================================================================
@router.post("/content/calendar/reschedule", response_model=CalendarEntryOut)
def reschedule_entry(
    body: RescheduleRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> CalendarEntryOut:
    """Drag-to-reschedule a calendar entry — owner-gated; 404 on an unknown entry."""
    _require_content_owner(principal)
    try:
        entry = store.reschedule_entry(program, body.entry_id, body.new_date)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="calendar entry not found") from exc
    return _calendar_row(entry)


@router.post("/content/calendar/entry", response_model=CalendarEntryOut)
def upsert_calendar_entry(
    body: CalendarEntryRequest,
    store: StoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> CalendarEntryOut:
    """Create or update a calendar entry — owner-gated."""
    _require_content_owner(principal)
    entry = store.upsert_calendar_entry(
        program,
        entry_id=body.entry_id,
        title=body.title,
        channel=body.channel,
        scheduled_date=body.scheduled_date,
        status=body.status,
        piece_ref=body.piece_ref,
        owner=CONTENT_WORKSTREAM,
    )
    return _calendar_row(entry)


# ===========================================================================
# BRAND VOICE — advisory suggest-edits (INV-2 PROPOSAL; does NOT write state).
# ===========================================================================
@dataclass(frozen=True)
class _BrandVoiceRecord:
    """A minimal :class:`app.core.eval_gate.GatedRecord` for the brand judge.

    The judge only reads the record text (via ``_record_text`` → ``copy_text``); the
    ``claims`` property satisfies the structural :class:`GatedRecord` Protocol.
    """

    copy_text: str

    @property
    def claims(self) -> Sequence[object]:
        return ()


def _never_rule_statements(brand_rules: Sequence[BrandRule]) -> list[str]:
    """The active NEVER-rule statements (the absolute V-4 phrases the judge penalizes)."""
    return [r.statement for r in brand_rules if r.active and r.rule_type is RuleType.NEVER]


def _inline_suggestions(text: str, never_rules: Sequence[str]) -> list[SuggestionOut]:
    """Deterministic inline rewrite suggestions over the GT off-brand vocabulary (INV-2).

    Scans the copy for off-brand/hype tokens (the brand judge's own ``_OFFBRAND_TERMS``,
    reused — never a parallel list) and active never-rule phrases, proposing a concrete
    rewrite (or a removal) for each. Pure + deterministic so the advisory path needs no
    live LLM call. The suggestions are SUGGESTED, never applied (INV-2).
    """
    lowered = text.lower()
    suggestions: list[SuggestionOut] = []
    for term in sorted(_OFFBRAND_TERMS):
        if term in lowered:
            suggestions.append(
                SuggestionOut(
                    before=term,
                    after=_HYPE_REWRITES.get(term, ""),
                    rule="GT voice: concrete over hype",
                    kind="hype",
                )
            )
    for statement in never_rules:
        if statement.lower() in lowered:
            suggestions.append(
                SuggestionOut(
                    before=statement,
                    after="",
                    rule=statement,
                    kind="never_rule",
                )
            )
    return suggestions


@router.post("/content/brand-voice/suggest", response_model=BrandVoiceResponse)
def suggest_brand_voice(
    body: BrandVoiceRequest,
    settings: SettingsDep,
    brand_judge: BrandJudgeDep,
    brand_rules: BrandRulesDep,
    principal: AnyPrincipalDep,
) -> BrandVoiceResponse:
    """Advisory brand-voice suggest-edits — a PROPOSAL (INV-2), never a state write.

    Reuses the V-4 brand judge for the overall score: LLM-backed when
    ``settings.llm_available`` (the injected judge scores via the gated edge — no live
    call under test), DEGRADING to the deterministic heuristic otherwise (and the judge
    itself falls back internally on a degraded reply). The inline rewrite suggestions are
    a deterministic scan over the GT off-brand vocabulary + active never-rules. Nothing
    is applied — the response is clearly marked advisory.
    """
    record = _BrandVoiceRecord(copy_text=body.text)
    never_rules = _never_rule_statements(brand_rules)
    score: float | None = None
    llm_scored = False
    if brand_judge is not None:
        try:
            score = brand_judge(record, never_rules)
            llm_scored = score is not None and settings.llm_available
        except Exception:  # noqa: BLE001 — advisory path must NEVER fail (INV-2 non-blocking)
            # A live-LLM error (e.g. the optional ``anthropic`` dep absent though a key
            # is configured, or a transport failure) degrades to the deterministic
            # heuristic rather than 500-ing — the suggest-edits path is non-blocking.
            score = None
            llm_scored = False
    if score is None:
        # No judge wired / it declined / the LLM degraded ⇒ the deterministic heuristic
        # (never a silent pass; the same offline conformance score the judge falls to).
        score = heuristic_brand_score(record, never_rules)
    mode = "llm" if llm_scored else "heuristic"
    return BrandVoiceResponse(
        brand_score=score,
        suggestions=_inline_suggestions(body.text, never_rules),
        advisory=True,
        mode=mode,
        note="Suggestions are advisory — proposed edits, not applied (INV-2).",
    )

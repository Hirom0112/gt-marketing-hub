"""Summer-camp surface (Module 4) — reconcile + content + the leadership cross-link.

The thin HTTP composition over the pure dual-source reconciler
(:mod:`app.core.summer_reconcile`) fed from the camp STORE seam
(:mod:`app.data.camp_store`). Summer camp ingests registrations from TWO overlapping
sources — ``summer.gt.school`` and a standalone registration form — so a raw union
would double-count anyone in both. The deterministic core merges them on a stable
identity key and counts each registrant ONCE (INV-2); an ambiguous match is held for
human review, never silently merged (INV-4).

  ``GET /summer/reconcile``  (any authenticated seat; optional ``?campus`` /
    ``?grade_band`` / ``?source`` slicing)
      the per-campus rollup, the dedup summary, the synthetic revenue-vs-target, PLUS
      the Phase-1 dimensions: the signup-channel breakdown, the
      Lead→Registered→Paid→Attended funnel, "registrations this week", the
      days-to-camp-start countdown, the session calendar, and per-campus waitlist.

  ``GET /summer/content``  (any authenticated seat)
      the camp-tagged slice of the live content kanban (rows whose ``utm`` starts with
      ``camp_``), grouped by stage. Idempotently seeds a handful of camp content rows
      so the slice is non-empty.

  ``POST /summer/session-change``  (OWNER-gated)
      the leadership cross-link: a pricing/session change becomes a Decision-Queue card
      via the SAME ``flag_decision`` feeder grassroots/budget use.

This module is a composition root (it may import ``app.core`` / ``app.data`` /
``app.api``); the core stays pure (INV-2). Revenue reads the Stripe camp-payment ledger
(0038) when it has succeeded charges (``basis == "stripe_collected"``), falling back to
the honest synthetic ``paid × price`` estimate otherwise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.adapters import registry
from app.adapters.sheets.base import STAGES, ContentRow, SheetsAdapter
from app.api.decisions import DecisionResponse, _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_camp_store,
    get_decisions_store,
    get_params,
    get_principal,
    get_settings_dep,
    get_sheets_adapter_dep,
)
from app.core.params import Params
from app.core.program import Program
from app.core.settings import Settings
from app.core.summer_reconcile import (
    CampRegistration,
    SummerReconciliation,
    channel_breakdown,
    days_until,
    reconcile,
    registration_funnel,
    registrations_in_window,
    waitlist_by_campus,
)
from app.data.camp_store import CampRegistrationRow, CampSession, CampStore
from app.data.decisions_store import (
    PRIORITIES,
    PRIORITY_NORMAL,
    DecisionsStore,
)

router = APIRouter(tags=["summer"])

# Summer camp is its OWN program tenant (0032) — the reconcile surface always scopes to
# ``summer_camp`` regardless of the env active program. The camp STORE reads use this.
CAMP_PROGRAM = Program.SUMMER_CAMP

# The workstream Summer Camp owns (one of decisions_store.WORKSTREAMS). An OPERATOR who
# owns this workstream may post a session change; a foreign operator is 403; a
# leader/admin may always. Named wire tokens, not tunables (INV-11 carve-out, mirroring
# grassroots.GRASSROOTS_WORKSTREAM).
CAMP_WORKSTREAM = "camp"
DEMO_OPERATOR_WORKSTREAM = "camp"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# The source tag the leadership cross-link carries (a fixed wire token, INV-11 carve-out
# like decisions.FIELD_EVENT_SOURCE).
SESSION_CHANGE_SOURCE = "summer_session_change"

# The camp content slice key: a kanban row belongs to camp when its UTM starts here.
CAMP_UTM_PREFIX = "camp_"

# Any authenticated principal may VIEW (mirrors GET /budget).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]
CampStoreDep = Annotated[CampStore, Depends(get_camp_store)]
ParamsDep = Annotated[Params, Depends(get_params)]
SheetsDep = Annotated[SheetsAdapter, Depends(get_sheets_adapter_dep)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]


# ===========================================================================
# Owner gate — an OPERATOR may write only when they OWN the camp workstream; a
# LEADER/ADMIN may write anything; everyone else is 403. The verified ROLE decides —
# never a client claim (the IDOR/spoof posture). Mirrors app.api.grassroots.
# ===========================================================================
def _operator_workstream(principal: Principal) -> str:
    """The single workstream an OPERATOR owns (keyed by the verified agent_id only)."""
    if principal.agent_id is not None:
        return OPERATOR_WORKSTREAMS.get(str(principal.agent_id), DEMO_OPERATOR_WORKSTREAM)
    return DEMO_OPERATOR_WORKSTREAM


def _require_camp_owner(principal: Principal) -> None:
    """OWNER gate for the camp cross-link — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the
    workstream they own is ``camp``; a foreign operator is 403. The verified ROLE
    decides — never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    if _operator_workstream(principal) != CAMP_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {CAMP_WORKSTREAM!r} workstream",
        )


# ===========================================================================
# Wire models — the reconcile response (existing fields kept; new ones additive).
# ===========================================================================
class CampusRow(BaseModel):
    """One campus's deduped rollup over the wire."""

    campus: str
    capacity: int
    registered: int
    paid: int
    lead: int
    seats_remaining: int
    pct_sold: float  # registered / capacity * 100


class Totals(BaseModel):
    """The whole-program deduped totals."""

    capacity: int
    registered: int
    paid: int
    lead: int


class SourceRow(BaseModel):
    """One source's raw (pre-dedup) row count — the dedup provenance."""

    source: str
    rows: int


class ConflictRow(BaseModel):
    """An ambiguous registrant held out of the counts (fail-closed; INV-4)."""

    dedup_key: str
    campuses: list[str]
    external_ids: list[str]
    summary: str


class DedupSummary(BaseModel):
    """The no-double-count proof: raw union vs unique, rows merged, sources, conflicts."""

    raw_source_rows: int
    unique_registrations: int
    duplicates_merged: int
    sources: list[SourceRow]
    conflicts: list[ConflictRow]


class RevenueSummary(BaseModel):
    """Camp revenue against the season target.

    When the Stripe camp-payment ledger has ≥1 succeeded charge, ``revenue_usd`` is the
    REAL collected revenue (``basis == "stripe_collected"``, ``collected_count`` charges,
    ``revenue_by_campus`` the per-campus split); otherwise it falls back to the honest
    synthetic ``paid × price`` estimate (``basis == "synthetic_paid_times_price"``).
    ``revenue_per_registered_usd`` is ``revenue_usd / registered`` (the yield per
    registered family).
    """

    paid_registrations: int
    price_per_seat_usd: int
    revenue_usd: int
    target_usd: int
    pct_to_target: float
    # Either "stripe_collected" (the camp ledger has succeeded charges) or the honest
    # "synthetic_paid_times_price" fallback (no Stripe charges collected yet).
    basis: str = "synthetic_paid_times_price"
    # Number of succeeded camp charges behind ``revenue_usd`` (0 in the synthetic fallback).
    collected_count: int = 0
    # Per-campus revenue (USD) — the collected split when stripe_collected, else paid×price.
    revenue_by_campus: dict[str, int] = {}
    # Revenue per registered family (USD) = revenue_usd / total registered.
    revenue_per_registered_usd: float = 0.0


class ChannelRow(BaseModel):
    """One signup-channel slice (the top channel + the breakdown)."""

    channel: str
    count: int
    pct: float


class FunnelRow(BaseModel):
    """One funnel stage — count + drop from the previous stage + pending honesty flag."""

    stage: str
    count: int
    drop_off_pct: float
    pending: bool


class SessionRow(BaseModel):
    """One camp session (the cohort calendar)."""

    session_id: UUID
    campus: str
    starts_on: date
    ends_on: date
    duration: str
    capacity: int
    status: str


class WaitlistRow(BaseModel):
    """One campus's overflow beyond capacity (registered − capacity, never negative)."""

    campus: str
    capacity: int
    registered: int
    waitlisted: int


class AppliedFilters(BaseModel):
    """The slice the response reflects (``None`` ⇒ that dimension was not filtered)."""

    campus: str | None = None
    grade_band: str | None = None
    source: str | None = None


class SummerReconcileResponse(BaseModel):
    """The summer-camp dual-source reconcile + Phase-1 dimensions over the wire."""

    program_id: str
    per_campus: list[CampusRow]
    totals: Totals
    dedup: DedupSummary
    revenue: RevenueSummary
    # Phase-1 additive dimensions.
    registration_channels: list[ChannelRow]
    funnel: list[FunnelRow]
    registrations_this_week: int
    days_to_camp_start: int | None
    sessions: list[SessionRow]
    waitlist: list[WaitlistRow]
    applied_filters: AppliedFilters


# ===========================================================================
# Reconcile.
# ===========================================================================
def _filter_rows(
    rows: list[CampRegistrationRow],
    *,
    campus: str | None,
    grade_band: str | None,
    source: str | None,
) -> list[CampRegistrationRow]:
    """Apply the optional campus / grade-band / source slice (each ANDs the previous)."""
    out = rows
    if campus:
        out = [r for r in out if r.campus == campus]
    if grade_band:
        out = [r for r in out if r.child_grade_band == grade_band]
    if source:
        out = [r for r in out if r.source == source]
    return out


def _to_response(
    result: SummerReconciliation,
    core_rows: list[CampRegistration],
    sessions: list[CampSession],
    *,
    price_per_seat_usd: int,
    revenue_target_usd: int,
    collected: dict[str, Any],
    now: datetime,
    window_days: int,
    applied: AppliedFilters,
) -> SummerReconcileResponse:
    """Project the pure reconcile result + Phase-1 dimensions onto the wire shape.

    Revenue reads the Stripe camp-payment ledger (``collected``): with ≥1 succeeded
    charge it reports REAL collected revenue (``basis == "stripe_collected"``); else it
    falls back to the honest synthetic ``paid × price`` estimate.
    """
    if collected["count"] > 0:
        revenue_usd = collected["total_cents"] // 100
        revenue_basis = "stripe_collected"
        revenue_by_campus = {
            campus: cents // 100 for campus, cents in collected["by_campus"].items()
        }
        collected_count = int(collected["count"])
    else:
        revenue_usd = result.total_paid * price_per_seat_usd
        revenue_basis = "synthetic_paid_times_price"
        revenue_by_campus = {c.campus: c.paid * price_per_seat_usd for c in result.per_campus}
        collected_count = 0
    revenue_per_registered_usd = (
        round(revenue_usd / result.total_registered, 2) if result.total_registered else 0.0
    )
    attended = sum(1 for r in core_rows if r.attended)  # 0 in this phase (camp is future)
    earliest_start = min((s.starts_on for s in sessions), default=None)
    countdown = days_until(earliest_start, now=now.date()) if earliest_start is not None else None
    return SummerReconcileResponse(
        program_id=result.program_id,
        per_campus=[
            CampusRow(
                campus=c.campus,
                capacity=c.capacity,
                registered=c.registered,
                paid=c.paid,
                lead=c.lead,
                seats_remaining=c.seats_remaining,
                pct_sold=round(c.registered / c.capacity * 100, 1) if c.capacity else 0.0,
            )
            for c in result.per_campus
        ],
        totals=Totals(
            capacity=result.total_capacity,
            registered=result.total_registered,
            paid=result.total_paid,
            lead=result.total_lead,
        ),
        dedup=DedupSummary(
            raw_source_rows=result.raw_source_rows,
            unique_registrations=result.unique_registrations,
            duplicates_merged=result.duplicates_merged,
            sources=[SourceRow(source=s.source, rows=s.rows) for s in result.sources],
            conflicts=[
                ConflictRow(
                    dedup_key=c.dedup_key,
                    campuses=list(c.campuses),
                    external_ids=list(c.external_ids),
                    summary=c.summary,
                )
                for c in result.conflicts
            ],
        ),
        revenue=RevenueSummary(
            paid_registrations=result.total_paid,
            price_per_seat_usd=price_per_seat_usd,
            revenue_usd=revenue_usd,
            target_usd=revenue_target_usd,
            pct_to_target=round(revenue_usd / revenue_target_usd * 100, 1)
            if revenue_target_usd
            else 0.0,
            basis=revenue_basis,
            collected_count=collected_count,
            revenue_by_campus=revenue_by_campus,
            revenue_per_registered_usd=revenue_per_registered_usd,
        ),
        registration_channels=[
            ChannelRow(channel=c.channel, count=c.count, pct=c.pct)
            for c in channel_breakdown(core_rows)
        ],
        funnel=[
            FunnelRow(stage=f.stage, count=f.count, drop_off_pct=f.drop_off_pct, pending=f.pending)
            for f in registration_funnel(result, attended)
        ],
        registrations_this_week=registrations_in_window(core_rows, now=now, days=window_days),
        days_to_camp_start=countdown,
        sessions=[
            SessionRow(
                session_id=s.session_id,
                campus=s.campus,
                starts_on=s.starts_on,
                ends_on=s.ends_on,
                duration=s.duration,
                capacity=s.capacity,
                status=s.status,
            )
            for s in sessions
        ],
        waitlist=[
            WaitlistRow(
                campus=w.campus,
                capacity=w.capacity,
                registered=w.registered,
                waitlisted=w.waitlisted,
            )
            for w in waitlist_by_campus(result)
        ],
        applied_filters=applied,
    )


@router.get("/summer/reconcile", response_model=SummerReconcileResponse)
def get_summer_reconcile(
    principal: AnyPrincipalDep,
    store: CampStoreDep,
    params: ParamsDep,
    campus: Annotated[str | None, Query()] = None,
    grade_band: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
) -> SummerReconcileResponse:
    """The deduped dual-source summer-camp rollup + Phase-1 dimensions (any VIEW).

    Reads BOTH sources' registration rows from the camp store (program ``summer_camp``),
    applies the optional ``campus`` / ``grade_band`` / ``source`` slice, runs the pure
    reconciler (each registrant counted ONCE; ambiguity fails closed) against the
    params-defined per-campus capacity, and returns the per-campus rollup, the dedup
    summary, the synthetic revenue-vs-target, the signup-channel breakdown, the funnel,
    the weekly-registration count + camp-start countdown (``now`` injected here — the
    core stays clock-free), the session calendar, and per-campus waitlist. Capacity /
    price / target / channels / window all from params.summer_camp (INV-11).
    """
    rows = _filter_rows(
        store.list_registrations(CAMP_PROGRAM),
        campus=campus,
        grade_band=grade_band,
        source=source,
    )
    core_rows = [r.to_core() for r in rows]

    capacities = dict(params.summer_camp.campus_capacity)
    if campus:
        # Slice the rollup to the requested campus (0 capacity if it is unknown).
        capacities = {campus: capacities.get(campus, 0)}

    sessions = store.list_sessions(CAMP_PROGRAM)
    if campus:
        sessions = [s for s in sessions if s.campus == campus]

    # Real collected camp revenue from the Stripe camp-payment ledger (0038). Empty ⇒
    # the synthetic paid × price fallback (handled in _to_response). This is the SEASON
    # collected total (program-scoped); it is not narrowed by the campus VIEW filter.
    collected = store.collected_revenue(CAMP_PROGRAM)

    now = datetime.now(UTC)
    return _to_response(
        reconcile(core_rows, capacities),
        core_rows,
        sessions,
        price_per_seat_usd=params.summer_camp.price_per_seat_usd,
        revenue_target_usd=params.summer_camp.revenue_target_usd,
        collected=collected,
        now=now,
        window_days=params.summer_camp.registration_window_days,
        applied=AppliedFilters(campus=campus, grade_band=grade_band, source=source),
    )


# ===========================================================================
# Camp content — the camp-tagged slice of the live content kanban (4c).
# ===========================================================================
def _camp_content_seed() -> list[ContentRow]:
    """The camp content rows seeded into the kanban so the camp slice is non-empty (INV-1).

    Synthetic content pieces, one per early stage, each carrying a ``camp_`` UTM so the
    :func:`get_summer_content` filter picks them up. Seeded idempotently (by title).
    """
    return [
        ContentRow(
            title="Camp guide interviews",
            type="article",
            stage="Backlog",
            owner="the Content Owner",
            channel="Substack",
            utm="camp_guide_interviews",
            target_date="Jul 20",
        ),
        ContentRow(
            title="Pilot outcomes recap",
            type="article",
            stage="Drafting",
            owner="Pamela Hobart",
            channel="Substack",
            utm="camp_pilot_outcomes",
            target_date="Jul 22",
        ),
        ContentRow(
            title="Welcome kit content",
            type="social",
            stage="Review",
            owner="the Content Owner",
            channel="Instagram",
            utm="camp_welcome_kit",
            target_date="Jul 25",
        ),
        ContentRow(
            title="Camp day-in-the-life",
            type="video",
            stage="Scheduled",
            owner="the Content Owner",
            channel="YouTube",
            utm="camp_day_in_the_life",
            target_date="Jul 28",
        ),
    ]


def _ensure_camp_content(adapter: SheetsAdapter) -> None:
    """Idempotently ensure the camp content rows exist on the sheet.

    ``ensure_seeded`` writes the header + camp rows to an EMPTY sheet (the first-read
    seed); on a NON-empty sheet it is a no-op, so the missing camp rows are added by an
    explicit upsert (by title). Re-running adds nothing new (idempotent).
    """
    seed = _camp_content_seed()
    adapter.ensure_seeded(seed)
    existing = {r.title for r in adapter.read_rows()}
    for row in seed:
        if row.title not in existing:
            adapter.upsert_row(row)


def _grouped_camp(rows: list[ContentRow]) -> list[dict[str, object]]:
    """Group camp rows into the five canonical kanban columns (stable stage order)."""
    by_stage: dict[str, list[dict[str, object]]] = {stage: [] for stage in STAGES}
    for row in rows:
        by_stage[row.stage].append(row.model_dump(mode="json"))
    return [{"stage": stage, "cards": by_stage[stage]} for stage in STAGES]


def _sync_block(settings: Settings) -> dict[str, object]:
    """The honest sync-status block (the EFFECTIVE seam — a kill-switched live = simulate)."""
    mode = registry.effective_sheets_mode(settings)
    live = mode == "live"
    return {
        "mode": mode,
        "synced": live,
        "tab": settings.gsheets_tab if live else None,
        "sheet_id": settings.gsheets_sheet_id if live else None,
    }


@router.get("/summer/content", response_model=dict[str, object])
def get_summer_content(
    adapter: SheetsDep,
    settings: SettingsDep,
    principal: AnyPrincipalDep,
) -> dict[str, object]:
    """The camp-tagged slice of the content kanban (rows whose ``utm`` starts ``camp_``).

    Reads via the SAME Sheets adapter ``GET /content/kanban`` uses, idempotently seeds a
    handful of camp content rows, then returns ONLY the camp rows — flat (``rows``) and
    grouped by stage (``columns``) — plus the canonical ``stages`` order and the honest
    ``sync`` block. The live sheet IS the backing store (writes land on the real sheet —
    intended for the "filter the live kanban" demo).
    """
    _ensure_camp_content(adapter)
    camp_rows = [r for r in adapter.read_rows() if r.utm.startswith(CAMP_UTM_PREFIX)]
    return {
        "stages": list(STAGES),
        "rows": [r.model_dump(mode="json") for r in camp_rows],
        "columns": _grouped_camp(camp_rows),
        "sync": _sync_block(settings),
    }


# ===========================================================================
# Leadership cross-link — owner-gated session/pricing change → Decision Queue.
# ===========================================================================
class SessionChangeRequest(BaseModel):
    """Body for ``POST /summer/session-change`` — propose a session/pricing change.

    There is DELIBERATELY no ``raised_by`` field: the route stamps it from the VERIFIED
    principal, never the body (the IDOR/spoof posture, INV-1). All fields are
    synthetic/operational labels — never PII.
    """

    campus: str
    change_type: str  # e.g. "pricing" | "session_dates" | "capacity"
    detail: str = ""
    recommendation: str = ""
    budget_ask: float | None = None
    due_date: date | None = None
    priority: str = PRIORITY_NORMAL


@router.post("/summer/session-change", response_model=DecisionResponse)
def propose_session_change(
    body: SessionChangeRequest,
    decisions: DecisionsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Escalate a camp pricing/session change into the leadership Decision Queue.

    Reuses the SAME :func:`app.api.decisions.flag_decision` feeder grassroots/budget use:
    enqueues ONE open ``summer_session_change`` decision on the ``camp`` workstream,
    ``raised_by`` STAMPED from the verified principal (never the body — INV-1).
    OWNER-gated (leader/admin always; an operator only when they own ``camp``).
    ``priority`` must be one of :data:`PRIORITIES` (a clean 422 otherwise; fail-closed).
    """
    _require_camp_owner(principal)
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}",
        )
    decision = flag_decision(
        decisions,
        program,
        source=SESSION_CHANGE_SOURCE,
        payload={
            "campus": body.campus,
            "change_type": body.change_type,
            "detail": body.detail,
        },
        question=f"Camp {body.change_type} change at {body.campus}",
        raised_by=_actor_token(principal),
        workstream=CAMP_WORKSTREAM,
        recommendation=body.recommendation,
        budget_ask=body.budget_ask,
        due_date=body.due_date,
        priority=body.priority,
    )
    return DecisionResponse.of(decision)

"""Summer-camp dual-source reconcile surface (D2) — ``GET /summer/reconcile``.

The thin HTTP composition over the pure dual-source reconciler
(:mod:`app.core.summer_reconcile`) and its synthetic registration sources
(:mod:`app.data.synthetic_summer`). Summer camp ingests registrations from TWO
overlapping sources — ``summer.gt.school`` and a standalone registration form — so a
raw union would double-count anyone in both. The deterministic core merges them on a
stable identity key and counts each registrant ONCE (INV-2); an ambiguous match is
held for human review, never silently merged (INV-4).

``GET /summer/reconcile`` returns:

  * the per-campus rollup (registered/paid vs capacity),
  * the dedup summary (raw union vs unique, rows merged, per-source counts, conflicts)
    — the data the cockpit's "deduplicated / no double-count" banner reflects, and
  * the revenue-vs-target (paid registrations × the per-seat price vs the target).

Open to ANY authenticated principal (``Depends(get_principal)``) — the VIEW posture
of ``GET /budget``. This module is a composition root (it may import ``app.core`` /
``app.data``); the core stays pure (INV-2). No live external call is ever made here —
the sources are deterministic synthetic stand-ins (INV-1/INV-9).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import Principal, get_params, get_principal
from app.core.params import Params
from app.core.summer_reconcile import SummerReconciliation, reconcile
from app.data.synthetic_summer import generate_summer_dataset

router = APIRouter(tags=["summer"])

# Any authenticated principal may VIEW the reconcile (mirrors GET /budget).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]
# Business tunables — per-campus capacity, per-seat price, season revenue target —
# live in params.summer_camp (the single home; INV-11), never a code literal here.
ParamsDep = Annotated[Params, Depends(get_params)]


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
    """Paid-registration revenue against the season target (separate P&L)."""

    paid_registrations: int
    price_per_seat_usd: int
    revenue_usd: int
    target_usd: int
    pct_to_target: float


class SummerReconcileResponse(BaseModel):
    """The summer-camp dual-source reconcile over the wire."""

    program_id: str
    per_campus: list[CampusRow]
    totals: Totals
    dedup: DedupSummary
    revenue: RevenueSummary


def _to_response(
    result: SummerReconciliation, *, price_per_seat_usd: int, revenue_target_usd: int
) -> SummerReconcileResponse:
    """Project the pure reconcile result onto the wire shape (prices from params)."""
    revenue_usd = result.total_paid * price_per_seat_usd
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
        ),
    )


@router.get("/summer/reconcile", response_model=SummerReconcileResponse)
def get_summer_reconcile(principal: AnyPrincipalDep, params: ParamsDep) -> SummerReconcileResponse:
    """The deduped dual-source summer-camp rollup (any authenticated VIEW).

    Builds the two deterministic synthetic sources, runs the pure reconciler (each
    registrant counted ONCE; ambiguity fails closed) against the params-defined
    per-campus capacity, and returns the per-campus rollup, the dedup summary, and
    the revenue-vs-target — capacity/price/target all from params.summer_camp (INV-11).
    """
    rows, _seed_capacities = generate_summer_dataset()
    capacities = dict(params.summer_camp.campus_capacity)
    return _to_response(
        reconcile(rows, capacities),
        price_per_seat_usd=params.summer_camp.price_per_seat_usd,
        revenue_target_usd=params.summer_camp.revenue_target_usd,
    )

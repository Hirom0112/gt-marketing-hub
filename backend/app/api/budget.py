"""Budget Tracker endpoints — the GET roll-up + the variance→Decision feeder (B4).

The composition layer that wires the B4 deterministic core (``app.core.budget``), the
store seam (``app.data.budget_store``), and the B2 Decision-Queue feeder
(``app.api.decisions.flag_decision``) behind REST. Thin by design: the variance
reconcile (:func:`app.core.budget.reconcile`) is pure/owned core (INV-2); this router
only builds the reconcile inputs, gates the edit by role, persists the ledger entry,
and — on a >10% overrun — enqueues ONE human decision via the B2 feeder.

  ``GET  /budget``
    The tracker for the active program — per-workstream planned/actual/committed/
    remaining/variance/flagged rows, the flagged-workstream list, the roll-up totals,
    and the burn series (planned vs actual per workstream for the chart). Open to ANY
    authenticated principal (``Depends(get_principal)``) — anyone may VIEW the tracker;
    only leadership edits.

  ``POST /budget/entry``
    Append one spend/commitment ledger line item — **admin/leader-gated**
    (``_BUDGET_GUARD``). An operator is 403. Adds the entry, RE-RECONCILES, and for any
    workstream now flagged (>10% overrun) emits ONE open ``budget_variance`` decision
    via :func:`app.api.decisions.flag_decision`.

**Idempotency (the "exactly one open decision" property).** Before emitting, the route
checks ``decisions_store.list_open(program)`` for an existing OPEN ``budget_variance``
decision carrying the same ``workstream`` in its payload — if present, it does NOT
create a duplicate. So repeated overrun POSTs for the same workstream yield exactly ONE
open decision until a human decides it; once decided (no longer open) a fresh overrun
re-flags.

The leader/admin gate is bound at MODULE level (``_BUDGET_GUARD``) so FastAPI resolves
it from the route's PEP-563 string annotation; a closure-local guard would be invisible
to ``get_type_hints`` and the route param would degrade to a 422-y query param (the same
wrinkle documented in :mod:`app.api.decisions`).

This module may import ``app.core`` / ``app.api`` (it is the composition root);
``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.decisions import flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_budget_store,
    get_decisions_store,
    get_params,
    get_principal,
    require_role,
)
from app.core.budget import BudgetEntry, BudgetReconciliation, reconcile
from app.core.params import Params
from app.core.program import Program
from app.data.budget_store import ENTRY_KINDS, BudgetStore
from app.data.decisions_store import DecisionsStore

router = APIRouter(tags=["budget"])

# The source tag every budget-variance Decision-Queue item carries (the idempotency key
# the feeder de-dupes on, alongside the workstream in the payload). A named constant —
# not a tunable (INV-11 carve-out: a fixed wire token, like decisions.DECISION_FLOW).
BUDGET_VARIANCE_SOURCE = "budget_variance"

# The admin/leader edit guard, built ONCE at MODULE level so FastAPI can resolve it from
# the route's (string, PEP 563) annotation — a closure-local guard is invisible to
# `get_type_hints` and the route param would degrade to a query param (then 422). The
# same wrinkle/fix as `app.api.decisions._DECIDE_GUARD`.
_BUDGET_GUARD = require_role("admin", "leader")

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/decisions.py).
StoreDep = Annotated[BudgetStore, Depends(get_budget_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
# Any authenticated principal (the VIEW path — NOT role-gated).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]
# The admin/leader-gated principal (the EDIT path).
LeaderDep = Annotated[Principal, Depends(_BUDGET_GUARD)]


class WorkstreamRow(BaseModel):
    """One workstream's reconciled budget row over the wire (the GET tracker row)."""

    workstream: str
    planned: float
    actual: float
    committed: float
    remaining: float
    variance: float
    flagged: bool


class BurnRow(BaseModel):
    """One workstream's planned-vs-actual point for the burn chart."""

    workstream: str
    planned: float
    actual: float


class RollUp(BaseModel):
    """The whole-budget roll-up totals (the reconcile's cohort aggregate)."""

    total_planned: float
    total_actual: float
    total_remaining: float
    total_usd: int


class BudgetResponse(BaseModel):
    """The Budget Tracker over the wire — rows + flagged + roll-up + burn series."""

    workstreams: list[WorkstreamRow]
    flagged: list[str]
    rollup: RollUp
    burn: list[BurnRow]


class EntryRequest(BaseModel):
    """Body for ``POST /budget/entry`` — one spend/commitment ledger line item."""

    workstream: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    amount_usd: Decimal = Field(ge=0)
    note: str | None = None


def _build_reconciliation(
    store: BudgetStore, program: Program, params: Params
) -> tuple[BudgetReconciliation, dict[str, Decimal]]:
    """Build one :class:`BudgetEntry` per workstream and run the pure reconcile (B4).

    ``planned`` comes from the seeded workstream allocation (params, INV-11);
    ``actual`` is the sum of ``actual``-kind ledger entries; ``committed`` is the sum of
    ``committed``-kind entries. The reconcile owns the variance + flag (>10% overrun).
    Returns the reconciliation plus the per-workstream committed sums (carried for the
    display row — the reconcile result itself does not surface ``committed``).
    """
    entries = store.list_entries(program)
    actual_by_ws: dict[str, Decimal] = {}
    committed_by_ws: dict[str, Decimal] = {}
    for entry in entries:
        if entry.kind == "actual":
            actual_by_ws[entry.workstream] = (
                actual_by_ws.get(entry.workstream, Decimal("0")) + entry.amount_usd
            )
        elif entry.kind == "committed":
            committed_by_ws[entry.workstream] = (
                committed_by_ws.get(entry.workstream, Decimal("0")) + entry.amount_usd
            )

    budget_entries = [
        BudgetEntry(
            workstream=ws.name,
            planned=Decimal(ws.planned_usd),
            actual=actual_by_ws.get(ws.name, Decimal("0")),
            committed=committed_by_ws.get(ws.name, Decimal("0")),
        )
        for ws in store.list_workstreams(program)
    ]
    return reconcile(budget_entries, params=params), committed_by_ws


def _to_response(
    reconciliation: BudgetReconciliation, committed_by_ws: dict[str, Decimal]
) -> BudgetResponse:
    """Project a :class:`BudgetReconciliation` onto the wire shape (money → float)."""
    rows = [
        WorkstreamRow(
            workstream=r.workstream,
            planned=float(r.planned),
            actual=float(r.actual),
            committed=float(committed_by_ws.get(r.workstream, Decimal("0"))),
            remaining=float(r.remaining),
            variance=float(r.variance),
            flagged=r.flagged,
        )
        for r in reconciliation.results
    ]
    return BudgetResponse(
        workstreams=rows,
        flagged=list(reconciliation.flagged),
        rollup=RollUp(
            total_planned=float(reconciliation.total_planned),
            total_actual=float(reconciliation.total_actual),
            total_remaining=float(reconciliation.total_remaining),
            total_usd=reconciliation.total_usd,
        ),
        burn=[
            BurnRow(workstream=r.workstream, planned=float(r.planned), actual=float(r.actual))
            for r in reconciliation.results
        ],
    )


@router.get("/budget", response_model=BudgetResponse)
def get_budget(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> BudgetResponse:
    """The reconciled Budget Tracker for the active program (any authenticated VIEW)."""
    reconciliation, committed_by_ws = _build_reconciliation(store, program, params)
    return _to_response(reconciliation, committed_by_ws)


@router.post("/budget/entry", response_model=BudgetResponse)
def add_budget_entry(
    body: EntryRequest,
    store: StoreDep,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: LeaderDep,
) -> BudgetResponse:
    """Append a ledger entry, re-reconcile, and feed any >10% overrun to the Decision Queue.

    Admin/leader-gated (an operator is 403). 422 on an unknown workstream or kind (the
    0030 FK / CHECK rejects them). After the append, the route RE-RECONCILES and emits
    ONE open ``budget_variance`` decision per newly-flagged workstream — idempotent: a
    workstream that already has an OPEN budget_variance decision is not re-flagged.
    """
    if body.kind not in ENTRY_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown kind; expected one of {ENTRY_KINDS}")
    try:
        store.add_entry(
            program,
            workstream=body.workstream,
            kind=body.kind,
            amount_usd=body.amount_usd,
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    reconciliation, committed_by_ws = _build_reconciliation(store, program, params)

    # The variance → Decision-Queue link (INV-2): each flagged workstream becomes ONE
    # open human decision. Idempotency — skip a workstream that already has an OPEN
    # budget_variance decision, so repeated overruns yield exactly one open item.
    open_variance_ws = {
        d.payload.get("workstream")
        for d in decisions_store.list_open(program)
        if d.source == BUDGET_VARIANCE_SOURCE
    }
    flagged_results = {r.workstream: r for r in reconciliation.results if r.flagged}
    for workstream, result in flagged_results.items():
        if workstream in open_variance_ws:
            continue  # already queued and undecided — do NOT duplicate.
        flag_decision(
            decisions_store,
            program,
            source=BUDGET_VARIANCE_SOURCE,
            payload={
                "workstream": workstream,
                "planned": float(result.planned),
                "actual": float(result.actual),
                "variance": float(result.variance),
            },
        )

    return _to_response(reconciliation, committed_by_ws)

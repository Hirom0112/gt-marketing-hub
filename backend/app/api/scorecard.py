"""Weekly KPI scorecard endpoint — the nine business KPIs + per-metric provenance (B5/B6).

The composition layer wiring the pure
:func:`app.core.weekly_scorecard.build_weekly_scorecard` transform into HTTP. The pure
core only *reshapes* an already-sampled per-metric series into the weekly table
(this-week / last-week / delta / sparkline / target / status / pace projection);
SAMPLING each KPI from its real source is the API's job — the documented reuse seam
(see ``app/core/weekly_scorecard.py``'s module docstring). This module owns that
sampling for the nine business KPIs the product spec lists, then threads the series +
``params`` + an injected ``as_of`` into the core.

  ``GET /scorecard/weekly``
    The weekly scorecard: per metric — this-week, last-week, ``delta`` (= this −
    last), a sparkline, the target, a green/yellow/red status, a deterministic pace
    projection, AND a ``provenance`` descriptor (where the number comes from — system,
    locator, kind, formula). Identical for everyone (no role gate) — gated only by
    ``Depends(get_principal)``. Read-only; nothing is logged.

THE NINE KPIs (STEP 1 — the API's job). Each is sampled ONCE from the SAME source the
rest of the cockpit reads — no second KPI engine:

* ``applicants``             — Supabase app spine (``repository.pipeline_counts``).
* ``deposits``               — Stripe → payment ledger (``payments_store``).
* ``conversion_top_channel`` — Supabase ``attribution_source`` (derived rate).
* ``engagement_clicked``     — CRM engagement seam (``read_engagement`` clicked share).
* ``followup_sla``           — assignment + contact log (``is_sla_breached``).
* ``objections``             — contact-outcome spine (``count_objections``, our DB).
* ``ambassador_enrollments`` — Grassroots reconcile (roster proxy, labeled).
* ``handoffs``               — funnel enroll/onboarding boundary (derived).
* ``event_to_consult``       — explicitly NOT INSTRUMENTED (labeled gap).

PROVENANCE (B6). Every metric carries a :class:`app.core.metric_provenance.MetricProvenance`
descriptor from the ONE canonical key→source map (:data:`PROVENANCE`); this module never
re-types a source string. The UI shows it verbatim so a leader sees, per row, real vs
honest-stood-in vs uninstrumented.

HONESTY (the brief: "surface what's broken rather than faking green"). These are
POINT-IN-TIME snapshots — there is no genuine weekly history for most of them — so each
metric is sampled as a SINGLE-POINT series. The pure core handles a one-point series
honestly (``last_week`` reads ``0.0``); we do NOT fabricate a multi-week trend. A
stood-in / uninstrumented KPI is LABELED via its provenance ``kind`` rather than faked.

PARAMS vs DEFAULTS (INV-11). The status BAND (``green_at``/``yellow_at``) and the pacing
``goal_date`` are read from ``params.kpi.scorecard`` (the one canonical home). The
per-metric ``target`` now comes from the leadership-editable KPI-goals STORE
(:class:`app.data.goals_store.GoalsStore`, migration 0033): the scorecard injects it and
reads ``get_goals(program)``. The store is SEEDED from the same spec defaults
(``goals_store.DEFAULT_GOALS`` — the targets' one canonical home, moved out of this
module), so an unedited program reads the spec values verbatim; a leader edits them via
``GET``/``PUT /scorecard/goals`` (every change logged to the 0033 change log).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.adapters.registry import (
    effective_crm_mode,
    effective_open_data_mode,
    effective_payments_mode,
    effective_sheets_mode,
)
from app.api.ambassadors import get_ambassador_sources
from app.api.deps import (
    Principal,
    get_active_program,
    get_crm_adapter_dep,
    get_goals_store,
    get_observability_log,
    get_params,
    get_payments_store,
    get_principal,
    get_repository,
    get_settings_dep,
    get_watermark_store,
    require_role,
)
from app.core.ambassador_reconcile import reconcile_ambassadors
from app.core.contact_log import last_contact_at
from app.core.lead_routing import is_sla_breached
from app.core.metric_provenance import PROVENANCE, MetricProvenance
from app.core.params import Params
from app.core.program import Program
from app.core.settings import Settings
from app.core.weekly_scorecard import MetricSeries, build_weekly_scorecard
from app.data.goals_store import GOAL_KEYS, GoalChangeEvent, GoalsStore
from app.data.models import Stage
from app.data.payments_store import InMemoryPaymentsStore, PaymentsStore
from app.data.repository import FamilyRepository
from app.data.synthetic_ambassadors import AmbassadorSources
from app.data.watermark_store import WatermarkStore
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["scorecard"])

# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/scoreboard.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CrmAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
PaymentsStoreDep = Annotated[PaymentsStore, Depends(get_payments_store)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
SourcesDep = Annotated[AmbassadorSources, Depends(get_ambassador_sources)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
WatermarkStoreDep = Annotated[WatermarkStore, Depends(get_watermark_store)]
GoalsStoreDep = Annotated[GoalsStore, Depends(get_goals_store)]
# Any authenticated principal — the scorecard is identical for everyone (no role gate).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# Editing a KPI goal is a leadership write (the KPI owner sets real goals). Built ONCE
# at MODULE level so FastAPI resolves it from the route's (string, PEP 563) annotation —
# a closure-local guard is invisible to `get_type_hints` and the route param would
# degrade to a query param (then 422). Admin shares the leadership lens here (mirrors
# the decisions VIEW gate); an operator is 403.
_GOALS_WRITE_GUARD = require_role("leader", "admin")
GoalsWriterDep = Annotated[Principal, Depends(_GOALS_WRITE_GUARD)]

# The CRM-poll watermark object type whose last-sync stands for "HubSpot freshness"
# (the deal stream — the pull crm_sync advances; A2). A named constant, not a tunable.
_CRM_WATERMARK_OBJECT = "deal"

# The connector freshness roster (the data-freshness strip, spec 6 "last sync per
# connector"). Each entry: (display name, provenance kind, mode resolver). A resolver
# of None means the connector has no live/simulate seam — it's always stood-in (a
# source we can't reach) or our own DB. ONE home for the roster.
_STOOD_IN_CONNECTORS: tuple[str, ...] = ("Meta Business Suite", "GA4", "X / Twitter")

# The human label per KPI key (the scorecard row title). Ordered to mirror the spec.
_LABELS: dict[str, str] = {
    "applicants": "Applicants (total)",
    "deposits": "Deposits vs Fall goal",
    "conversion_top_channel": "Conversion · top channel",
    "engagement_clicked": "Engagement-tier mix (clicked)",
    "followup_sla": "24-hr follow-up SLA",
    "objections": "Objections logged",
    "ambassador_enrollments": "Ambassador-influenced enrollments",
    "handoffs": "Marketing → onboarding handoffs",
    "event_to_consult": "Event-to-consult conversion",
}

# Per-KPI targets are no longer hardcoded here: they come from the leadership-editable
# KPI-goals store (:class:`app.data.goals_store.GoalsStore`, migration 0033), whose seed
# (``goals_store.DEFAULT_GOALS``) is the targets' ONE canonical home (INV-11). The series
# builder is handed the resolved ``goals`` dict; the GET/PUT routes read/edit it.

# The funnel stages that count as "reached onboarding handoff" — a family at enroll
# (or the downstream tuition stage) has crossed the marketing→onboarding boundary. A
# structural stage set, not a tunable (INV-11 governs numbers, not stage membership).
_HANDOFF_STAGES: tuple[Stage, ...] = (Stage.ENROLL, Stage.TUITION)


def _applicants_value(repository: FamilyRepository) -> float:
    """Total applicants — the sum across every funnel stage (``pipeline_counts``)."""
    return float(sum(repository.pipeline_counts().values()))


def _deposits_value(payments_store: PaymentsStore, program: Program) -> float:
    """Deposit count from the Stripe → payment ledger for the active program.

    Reads the in-memory ledger's row list (the v1 / CI store); the live Supabase
    ledger exposes no bulk read accessor, so under it this reads ``0.0`` until a
    ledger-count read is wired — an honest absence, never a fabricated figure.
    """
    if isinstance(payments_store, InMemoryPaymentsStore):
        return float(len(payments_store.list_payments(program)))
    return 0.0


def _conversion_top_channel_value(repository: FamilyRepository) -> float:
    """Enrolled / total for the BUSIEST attribution channel (a derived 0–1 rate).

    Groups families by ``attribution_source``, picks the channel with the most
    families, and returns its conversion rate (families at the enroll/tuition stage
    over the channel total). No families ⇒ ``0.0`` (no division).
    """
    totals: dict[str, int] = {}
    enrolled: dict[str, int] = {}
    for record in repository.list_families():
        source = record.attribution_source
        totals[source] = totals.get(source, 0) + 1
        if record.current_stage in _HANDOFF_STAGES:
            enrolled[source] = enrolled.get(source, 0) + 1
    if not totals:
        return 0.0
    top = max(totals, key=lambda s: totals[s])
    return enrolled.get(top, 0) / totals[top]


def _engagement_clicked_value(crm: CRMAdapter, repository: FamilyRepository) -> float:
    """Engagement-tier share — contacts in the CLICKED tier, via the CRM seam (0–1).

    Sourced through the :class:`CRMAdapter` engagement seam
    (:meth:`~app.adapters.hubspot.crm_adapter.CRMAdapter.read_engagement`): the cohort's
    family ids are read into an :class:`EngagementSnapshot` and the KPI is its
    ``clicked_share``. Under the default simulate seam this is a DETERMINISTIC synthetic
    tier (labeled ``derived`` in provenance — honest, never claiming a live HubSpot
    call); the live HubSpot read is a documented stub, so a ``NotImplementedError`` from
    an unwired live engagement read degrades to an honest ``0.0`` rather than a 500. No
    contacts ⇒ ``0.0``.
    """
    family_ids = [record.family_id for record in repository.list_families()]
    if not family_ids:
        return 0.0
    try:
        snapshot = crm.read_engagement(family_ids)
    except NotImplementedError:
        # The live HubSpot engagement read is not wired (a documented stub) — surface
        # an honest absence, never a fabricated share (INV-9). The default simulate
        # seam is the real, demoable path.
        return 0.0
    return snapshot.clicked_share


def _followup_sla_value(
    repository: FamilyRepository, log: ObservabilityLog, params: Params, now: datetime
) -> float:
    """24-hr follow-up SLA compliance — not-breached / assigned (a derived 0–1 rate).

    For every ASSIGNED lead (``assigned_at`` set), pairs the pure
    :func:`is_sla_breached` predicate with the lead's ``last_contact_at`` derived
    from the audit log; compliance is the share NOT breached. With no assigned leads
    there is nothing to breach ⇒ vacuously ``1.0`` (no false red).
    """
    assigned = [r for r in repository.list_families() if r.assigned_at is not None]
    if not assigned:
        return 1.0
    worked = 0
    for record in assigned:
        contacted = last_contact_at(log, record.family_id)
        if not is_sla_breached(record.assigned_at, contacted, now, params):
            worked += 1
    return worked / len(assigned)


def _ambassador_value(sources: AmbassadorSources) -> float:
    """STOOD-IN ambassador metric — the reconciled roster size (labeled in provenance).

    Enrollment attribution through ambassadors isn't tracked, so this surfaces the
    one available figure — the deduped roster size from the Grassroots dual-source
    reconcile — as an honest proxy, with the gap stated in the provenance formula.
    """
    result = reconcile_ambassadors(list(sources.hubspot.rows), list(sources.community.rows))
    return float(result.union_count)


def _handoffs_value(repository: FamilyRepository) -> float:
    """Marketing → onboarding handoffs — families past the enroll/onboarding boundary."""
    counts = repository.pipeline_counts()
    return float(sum(counts.get(stage, 0) for stage in _HANDOFF_STAGES))


def _build_metric_series(
    *,
    repository: FamilyRepository,
    crm: CRMAdapter,
    payments_store: PaymentsStore,
    log: ObservabilityLog,
    sources: AmbassadorSources,
    params: Params,
    program: Program,
    goals: dict[str, float],
    now: datetime,
) -> list[MetricSeries]:
    """Sample the nine business KPIs into single-point :class:`MetricSeries` (STEP 1).

    Each KPI is read ONCE from its real source (no second engine) into a one-element
    weekly series — an honest point-in-time snapshot (the pure core renders it with
    ``last_week`` = ``0.0``; no fabricated trend). Stood-in / uninstrumented KPIs are
    sampled to their honest value (a proxy or ``0.0``) and LABELED via provenance,
    never faked green. Pure in its injected inputs + ``now`` (the API reads the clock).

    The per-metric ``target`` is read from ``goals`` (the resolved KPI-goals store
    snapshot for ``program``) — the seed defaults until a leader edits one (INV-11).
    """
    values: dict[str, float] = {
        "applicants": _applicants_value(repository),
        "deposits": _deposits_value(payments_store, program),
        "conversion_top_channel": _conversion_top_channel_value(repository),
        "engagement_clicked": _engagement_clicked_value(crm, repository),
        "followup_sla": _followup_sla_value(repository, log, params, now),
        # Objections — a real count off the append-only contact-outcome spine (our DB):
        # one per outcome carrying a logged objection reason (Module 6). 0 ⇒ honest 0.0.
        "objections": float(log.count_objections()),
        "ambassador_enrollments": _ambassador_value(sources),
        "handoffs": _handoffs_value(repository),
        # Event-to-consult is explicitly NOT INSTRUMENTED — a 0.0 gap, labeled in provenance.
        "event_to_consult": 0.0,
    }
    return [
        MetricSeries(
            key=key,
            label=_LABELS[key],
            target=goals[key],
            weekly_values=(values[key],),
        )
        for key in _LABELS
    ]


def _provenance_json(provenance: MetricProvenance) -> Mapping[str, object]:
    """Serialize one provenance descriptor to the JSON the UI reads."""
    return asdict(provenance)


@router.get("/scorecard/weekly")
def get_weekly_scorecard(
    repository: RepositoryDep,
    crm: CrmAdapterDep,
    payments_store: PaymentsStoreDep,
    log: LogDep,
    sources: SourcesDep,
    params: ParamsDep,
    program: ProgramDep,
    goals_store: GoalsStoreDep,
    principal: AnyPrincipalDep,
) -> dict[str, object]:
    """The weekly KPI scorecard — the nine business KPIs + per-metric provenance (B5/B6).

    Samples each KPI from its real source (STEP 1 — the API's reuse seam), calls the
    pure :func:`build_weekly_scorecard` transform with ``params`` (the status band +
    pacing ``goal_date``, INV-11) and ``as_of``, then attaches each metric's canonical
    :class:`MetricProvenance`. ``now`` / ``as_of`` are read HERE at the composition
    root (the pure core never reads a clock). Returns the frozen scorecard serialized
    to JSON, each metric row carrying a ``provenance`` object. Read-only; any
    authenticated seat may view it.
    """
    now = datetime.now(UTC)
    as_of: date = now.date()
    # The per-metric targets come from the leadership-editable goals store (the seed
    # defaults until edited) — read once for the active program (INV-11).
    goals = goals_store.get_goals(program)
    series = _build_metric_series(
        repository=repository,
        crm=crm,
        payments_store=payments_store,
        log=log,
        sources=sources,
        params=params,
        program=program,
        goals=goals,
        now=now,
    )
    scorecard = build_weekly_scorecard(series, params=params, as_of=as_of)
    metrics: list[dict[str, object]] = []
    for metric in scorecard.metrics:
        row = asdict(metric)
        row["provenance"] = _provenance_json(PROVENANCE[metric.key])
        metrics.append(row)
    # goal_date — the pacing horizon (params.kpi.scorecard.goal_date, INV-11). The
    # Goal-pacing tab projects each metric to this date; surfaced alongside as_of.
    return {"metrics": metrics, "as_of": as_of, "goal_date": params.kpi.scorecard.goal_date}


@router.get("/scorecard/connectors")
def get_connector_freshness(
    settings: SettingsDep,
    watermarks: WatermarkStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> dict[str, object]:
    """Per-connector freshness for the data-freshness strip (spec 6; any seat).

    Reports each data source the scorecard reads, its trust ``mode`` (``live`` when
    its adapter is in live mode, ``simulate`` when defaulting to the offline impl,
    ``stood_in`` for a source we can't reach), and its ``last_sync`` where one exists.
    Only the CRM poll keeps a watermark (the ``deal`` stream, A2); the others report
    their mode without a timestamp. Read-only — REPORTS the effective modes via the
    registry's ``effective_*`` resolvers (the read-only status posture), never flips one.
    """
    crm_last_sync = watermarks.get_watermark(program, _CRM_WATERMARK_OBJECT)
    stood_in: list[dict[str, object]] = [
        {"name": name, "kind": "stood_in", "mode": "stood_in", "last_sync": None}
        for name in _STOOD_IN_CONNECTORS
    ]
    connectors: list[dict[str, object]] = [
        # Supabase is our own DB — the request-time source of record, always live.
        {"name": "Supabase", "kind": "our_db", "mode": "live", "last_sync": None},
        {
            "name": "HubSpot",
            "kind": "live" if effective_crm_mode(settings) == "live" else "simulate",
            "mode": effective_crm_mode(settings),
            "last_sync": crm_last_sync.isoformat() if crm_last_sync is not None else None,
        },
        {
            "name": "Stripe",
            "kind": "live" if effective_payments_mode(settings) == "live" else "simulate",
            "mode": effective_payments_mode(settings),
            "last_sync": None,
        },
        {
            "name": "Open Data",
            "kind": "live" if effective_open_data_mode(settings) == "live" else "simulate",
            "mode": effective_open_data_mode(settings),
            "last_sync": None,
        },
        {
            "name": "Google Sheets",
            "kind": "live" if effective_sheets_mode(settings) == "live" else "simulate",
            "mode": effective_sheets_mode(settings),
            "last_sync": None,
        },
        *stood_in,
    ]
    return {"connectors": connectors}


class GoalsUpdateRequest(BaseModel):
    """Body for ``PUT /scorecard/goals`` — set one or more KPI targets (leader/admin).

    ``goals`` maps a KPI key (one of the nine scorecard KPIs) to its new numeric target.
    ``note`` is an optional reason recorded on every change-log entry. An unknown key is
    rejected (422).
    """

    goals: dict[str, float] = Field(min_length=1)
    note: str | None = None


def _event_json(event: GoalChangeEvent) -> dict[str, object]:
    """Serialize one change-log entry to the JSON the UI reads."""
    return {
        "key": event.key,
        "old_target": event.old_target,
        "new_target": event.new_target,
        "changed_by": event.changed_by,
        "changed_at": event.changed_at.isoformat(),
        "note": event.note,
    }


@router.get("/scorecard/goals")
def get_scorecard_goals(
    goals_store: GoalsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> dict[str, object]:
    """The current KPI goals + the recent change log (any authenticated seat).

    Returns the resolved per-KPI targets for the active program (the seed defaults until
    a leader edits one) and the append-only change log so the UI can show who changed
    what. Read-only — identical for everyone, gated only by ``Depends(get_principal)``.
    """
    return {
        "goals": goals_store.get_goals(program),
        "events": [_event_json(e) for e in goals_store.list_events(program)],
    }


@router.put("/scorecard/goals")
def put_scorecard_goals(
    body: GoalsUpdateRequest,
    goals_store: GoalsStoreDep,
    program: ProgramDep,
    principal: GoalsWriterDep,
) -> dict[str, object]:
    """Set one or more KPI targets — LEADER/ADMIN only (the KPI owner sets real goals).

    An operator is 403 (the leadership write gate). Rejects an unknown KPI key with 422
    (fail-closed) BEFORE writing anything. Each provided key is set on the store and a
    change event is appended (old→new + the verified actor + the optional note — the
    audit the brief asks for). Returns the updated goals + the change log.
    """
    unknown = sorted(set(body.goals) - set(GOAL_KEYS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown KPI goal key(s): {unknown}")

    # The actor is taken from the VERIFIED principal — never a client claim.
    actor = str(principal.user_id) if principal.user_id is not None else principal.role
    for key, target in body.goals.items():
        goals_store.set_goal(program, key, float(target), changed_by=actor, note=body.note)

    return {
        "goals": goals_store.get_goals(program),
        "events": [_event_json(e) for e in goals_store.list_events(program)],
    }

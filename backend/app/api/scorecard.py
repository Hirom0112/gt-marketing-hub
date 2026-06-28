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
* ``engagement_clicked``     — HubSpot engagement (STOOD-IN proxy, labeled).
* ``followup_sla``           — assignment + contact log (``is_sla_breached``).
* ``objections``             — HubSpot conversations (STOOD-IN, not yet wired).
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
per-metric ``target`` has NO params home yet — the KPI-goals store is a later phase —
so each spec-default target is a documented API-layer named constant (:data:`_TARGETS`),
PROVISIONAL: promote it to a Supabase goals store once the KPI owner sets real goals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.ambassadors import get_ambassador_sources
from app.api.deps import (
    Principal,
    get_active_program,
    get_observability_log,
    get_params,
    get_payments_store,
    get_principal,
    get_repository,
)
from app.core.ambassador_reconcile import reconcile_ambassadors
from app.core.contact_log import last_contact_at
from app.core.lead_routing import is_sla_breached
from app.core.metric_provenance import PROVENANCE, MetricProvenance
from app.core.params import Params
from app.core.program import Program
from app.core.weekly_scorecard import MetricSeries, build_weekly_scorecard
from app.data.models import Stage
from app.data.payments_store import InMemoryPaymentsStore, PaymentsStore
from app.data.repository import FamilyRepository
from app.data.synthetic_ambassadors import AmbassadorSources
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["scorecard"])

# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/scoreboard.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
PaymentsStoreDep = Annotated[PaymentsStore, Depends(get_payments_store)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
SourcesDep = Annotated[AmbassadorSources, Depends(get_ambassador_sources)]
# Any authenticated principal — the scorecard is identical for everyone (no role gate).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

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

# Per-KPI targets — the spec defaults (INV-11): named, NOT bare literals, and
# PROVISIONAL (no params home yet — see the module docstring; a later phase moves these
# to a Supabase goals store). Spec values: deposits 180, SLA 90%, conversion 40%,
# engagement-tier 35%, ambassador 30. Applicants / objections / handoffs /
# event-to-consult have no spec goal yet ⇒ a provisional ``0.0`` (a target of 0 reads
# green for any non-negative value — the pure core's documented zero-target degrade).
_TARGET_DEPOSITS = 180.0
_TARGET_SLA = 0.90
_TARGET_CONVERSION = 0.40
_TARGET_ENGAGEMENT = 0.35
_TARGET_AMBASSADOR = 30.0
_TARGET_PROVISIONAL = 0.0

_TARGETS: dict[str, float] = {
    "applicants": _TARGET_PROVISIONAL,
    "deposits": _TARGET_DEPOSITS,
    "conversion_top_channel": _TARGET_CONVERSION,
    "engagement_clicked": _TARGET_ENGAGEMENT,
    "followup_sla": _TARGET_SLA,
    "objections": _TARGET_PROVISIONAL,
    "ambassador_enrollments": _TARGET_AMBASSADOR,
    "handoffs": _TARGET_PROVISIONAL,
    "event_to_consult": _TARGET_PROVISIONAL,
}

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


def _engagement_clicked_value(repository: FamilyRepository) -> float:
    """STOOD-IN engagement-tier share — families with any email engagement (0–1).

    The real HubSpot "clicked" tier isn't wired, so this is an HONEST proxy
    (labeled ``stood_in`` in provenance): the share of families whose
    ``community_profile.engagement_signals`` shows ANY email opens. A presence
    check, not a tuned threshold (INV-11). No families ⇒ ``0.0``.
    """
    joined = repository.list_joined()
    if not joined:
        return 0.0
    engaged = 0
    for row in joined:
        signals = row.community_profile.engagement_signals if row.community_profile else {}
        opens = signals.get("email_opens", 0)
        if isinstance(opens, int) and opens > 0:
            engaged += 1
    return engaged / len(joined)


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
    payments_store: PaymentsStore,
    log: ObservabilityLog,
    sources: AmbassadorSources,
    params: Params,
    program: Program,
    now: datetime,
) -> list[MetricSeries]:
    """Sample the nine business KPIs into single-point :class:`MetricSeries` (STEP 1).

    Each KPI is read ONCE from its real source (no second engine) into a one-element
    weekly series — an honest point-in-time snapshot (the pure core renders it with
    ``last_week`` = ``0.0``; no fabricated trend). Stood-in / uninstrumented KPIs are
    sampled to their honest value (a proxy or ``0.0``) and LABELED via provenance,
    never faked green. Pure in its injected inputs + ``now`` (the API reads the clock).
    """
    values: dict[str, float] = {
        "applicants": _applicants_value(repository),
        "deposits": _deposits_value(payments_store, program),
        "conversion_top_channel": _conversion_top_channel_value(repository),
        "engagement_clicked": _engagement_clicked_value(repository),
        "followup_sla": _followup_sla_value(repository, log, params, now),
        # Objections have no source yet — an honest 0.0, labeled stood_in in provenance.
        "objections": 0.0,
        "ambassador_enrollments": _ambassador_value(sources),
        "handoffs": _handoffs_value(repository),
        # Event-to-consult is explicitly NOT INSTRUMENTED — a 0.0 gap, labeled in provenance.
        "event_to_consult": 0.0,
    }
    return [
        MetricSeries(
            key=key,
            label=_LABELS[key],
            target=_TARGETS[key],
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
    payments_store: PaymentsStoreDep,
    log: LogDep,
    sources: SourcesDep,
    params: ParamsDep,
    program: ProgramDep,
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
    series = _build_metric_series(
        repository=repository,
        payments_store=payments_store,
        log=log,
        sources=sources,
        params=params,
        program=program,
        now=now,
    )
    scorecard = build_weekly_scorecard(series, params=params, as_of=as_of)
    metrics: list[dict[str, object]] = []
    for metric in scorecard.metrics:
        row = asdict(metric)
        row["provenance"] = _provenance_json(PROVENANCE[metric.key])
        metrics.append(row)
    return {"metrics": metrics, "as_of": as_of}

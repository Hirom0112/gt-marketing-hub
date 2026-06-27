"""Weekly KPI scorecard endpoint — the canonical weekly metric table (B5).

The composition layer wiring the pure
:func:`app.core.weekly_scorecard.build_weekly_scorecard` transform into HTTP. The
pure core only *reshapes* an already-sampled per-metric weekly series; building that
series from the existing metric sources is the API's job — the documented reuse seam
(see ``app/core/weekly_scorecard.py``'s module docstring). This module owns that
sampling, then threads the series + ``params`` + an injected ``as_of`` into the core.

  ``GET /scorecard/weekly``
    The weekly scorecard for the active audit log: per metric — this-week,
    last-week, ``delta`` (= this − last), a 4-week sparkline, the target, a
    green/yellow/red status, and a deterministic pace projection. Identical for
    everyone (no role gate) — gated only by ``Depends(get_principal)``, so any
    authenticated seat may view it. Read-only; nothing is logged.

THE SERIES (STEP 1 — the API's job). The metrics are sampled from the SAME audit
spine the scoreboard/agent-KPI rollups read (``app/observability/log_store.py``) — no
second KPI engine. Each canonical metric is a count of a spine fact stream, BUCKETED
BY ISO WEEK on the fact's own timestamp:

* ``proposals``    — AI proposals logged (``ProposalRecord.created_at``).
* ``evals_passed`` — eval runs that PASSED (``EvalRecord.created_at`` where passed).
* ``approvals``    — human APPROVE decisions (``DecisionRecord.created_at``).

These three are the streams the in-memory log exposes GLOBALLY (via
``list_proposals`` + ``get_audit`` — the scoreboard's read pattern); the contact-
outcome / dismiss / lost streams are family-keyed (no global list) and so are not
sampled here.

HONESTY (the brief: "surface what's broken rather than faking green"). The series is
built ONLY from real record timestamps — no fabricated multi-week trend. The week
grid spans from the earliest logged record's ISO week (capped at
:data:`_MAX_SPARKLINE_WINDOW_WEEKS` back from ``as_of``) up to ``as_of``'s ISO week.
A genuinely quiet week reads as a real ``0.0`` (the truth), not a smoothed value, and
a point-in-time synthetic log with only a couple of weeks of history yields only a
couple of points — the pure core handles short series (``last_week`` is ``0.0`` for a
one-point series).

PARAMS vs DEFAULTS (INV-11). The status BAND (``green_at``/``yellow_at``) and the
pacing ``goal_date`` are read from ``params.kpi.scorecard`` (the one canonical home).
The per-metric WEEKLY-COUNT ``target`` has NO params home yet — there is no honest
``kpi`` lever for "proposals per week" (the levers are per-channel conversion RATES,
not counts) — so each target is a documented API-layer default surfaced as a named
constant in :data:`_METRICS`, NOTED here as provisional: promote it to ``params`` once
the KPI owner sets real weekly goals. The pure core and the params-owned band stay the
canonical homes; only this provisional default lives in code.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Principal, get_observability_log, get_params, get_principal
from app.core.params import Params
from app.core.weekly_scorecard import MetricSeries, build_weekly_scorecard
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["scorecard"])

# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/scoreboard.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
# Any authenticated principal — the scorecard is identical for everyone (no role gate).
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]

# The longest sparkline/series window. The pure core's sparkline is the trailing four
# weeks; we sample a little deeper so the 4-week window and the week-over-week delta
# have history, while never padding BEFORE the first real record (honesty). A calendar
# bound on the sample depth, not a tunable dial — hence a const, not a param.
_MAX_SPARKLINE_WINDOW_WEEKS = 8

_DAYS_PER_WEEK = 7

# The canonical log-derived metrics: key -> (label, provisional weekly-count target).
# The target is an API-layer DEFAULT (no params home yet — see the module docstring),
# surfaced as a named constant rather than a bare literal and noted as provisional.
_METRICS: dict[str, tuple[str, float]] = {
    "proposals": ("AI proposals", 5.0),
    "evals_passed": ("Evals passed", 5.0),
    "approvals": ("Human approvals", 4.0),
}


def _week_monday(d: date) -> date:
    """The Monday that starts ``d``'s ISO week (ISO weeks run Monday→Sunday).

    Monday-date arithmetic is the robust way to bucket by ISO week: subtracting
    whole weeks never crosses an ISO year boundary wrongly the way naive
    ``isocalendar()`` year/week tuples can.
    """
    return d - timedelta(days=d.weekday())


def _gather_streams(log: ObservabilityLog) -> dict[str, list[datetime]]:
    """Collect each metric's fact timestamps from the audit spine (the scoreboard read).

    Walks ``list_proposals`` + ``get_audit`` (the same public query path the
    scoreboard uses — no private state, no second engine) and tags each fact's
    ``created_at`` to its metric stream. Pure read; no clock, no I/O.
    """
    proposals: list[datetime] = []
    evals_passed: list[datetime] = []
    approvals: list[datetime] = []
    for proposal in log.list_proposals():
        proposals.append(proposal.created_at)
        audit = log.get_audit(proposal.proposal_id)
        if audit is None:
            continue
        for ev in audit.evals:
            if ev.passed:
                evals_passed.append(ev.created_at)
        for dec in audit.decisions:
            if dec.action is DecisionAction.APPROVE:
                approvals.append(dec.created_at)
    return {"proposals": proposals, "evals_passed": evals_passed, "approvals": approvals}


def _weekly_values(
    timestamps: list[datetime], *, start_monday: date, weeks: int
) -> tuple[float, ...]:
    """Count ``timestamps`` into ``weeks`` ISO-week buckets, oldest → newest.

    Bucket 0 is ``start_monday``'s week; bucket ``weeks - 1`` is the ``as_of`` week
    (so the last element is this week, matching the pure core's ``this_week =
    values[-1]``). A timestamp outside the window is dropped — empty windows that
    fall inside the span stay a real ``0.0`` (honesty), never padded away.
    """
    counts = [0.0] * weeks
    for ts in timestamps:
        idx = (_week_monday(ts.date()) - start_monday).days // _DAYS_PER_WEEK
        if 0 <= idx < weeks:
            counts[idx] += 1.0
    return tuple(counts)


def _build_metric_series(log: ObservabilityLog, *, as_of: date) -> list[MetricSeries]:
    """Sample the audit spine into per-metric weekly :class:`MetricSeries` (STEP 1).

    Buckets each metric's fact stream by ISO week over a SHARED week grid so every
    row spans the same weeks: from the earliest logged record's ISO week (capped at
    :data:`_MAX_SPARKLINE_WINDOW_WEEKS` back from ``as_of``) up to ``as_of``'s ISO
    week. With no records the grid is the single ``as_of`` week (one ``0.0`` point —
    the core still renders it; ``last_week`` reads ``0.0``). Pure in ``log`` + the
    injected ``as_of``.
    """
    streams = _gather_streams(log)
    as_of_monday = _week_monday(as_of)

    all_mondays = [_week_monday(ts.date()) for stream in streams.values() for ts in stream]
    if all_mondays:
        earliest = min(all_mondays)
        cap = as_of_monday - timedelta(days=_DAYS_PER_WEEK * (_MAX_SPARKLINE_WINDOW_WEEKS - 1))
        start_monday = max(earliest, cap)
    else:
        start_monday = as_of_monday
    # Clamp the start at/before as_of so the span is always ≥1 week (a future-dated
    # record can't push the window past today).
    start_monday = min(start_monday, as_of_monday)
    weeks = (as_of_monday - start_monday).days // _DAYS_PER_WEEK + 1

    series: list[MetricSeries] = []
    for key, (label, target) in _METRICS.items():
        series.append(
            MetricSeries(
                key=key,
                label=label,
                target=target,
                weekly_values=_weekly_values(streams[key], start_monday=start_monday, weeks=weeks),
            )
        )
    return series


@router.get("/scorecard/weekly")
def get_weekly_scorecard(
    log: LogDep, params: ParamsDep, principal: AnyPrincipalDep
) -> dict[str, object]:
    """The weekly KPI scorecard for the active audit log (B5; any authenticated seat).

    Samples the per-metric weekly series from the audit spine (STEP 1 — the API's
    reuse seam), then calls the pure :func:`build_weekly_scorecard` transform with
    ``params`` (the status band + pacing ``goal_date``, INV-11) and ``as_of`` —
    ``datetime.now(UTC).date()`` read HERE at the composition root (the pure core
    never reads a clock). Returns the frozen scorecard serialized to JSON. Read-only,
    deterministic given the log + ``as_of``.
    """
    as_of = datetime.now(UTC).date()
    series = _build_metric_series(log, as_of=as_of)
    scorecard = build_weekly_scorecard(series, params=params, as_of=as_of)
    return asdict(scorecard)

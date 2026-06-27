"""Pure weekly KPI scorecard transform (TODO_v2 §B5; INV-2/INV-11; A-7).

The canonical weekly scorecard is ONE shared metric table (identical for everyone):
per metric — this-week, last-week, **delta (= this_week − last_week)**, a 4-week
sparkline, the target, a status (green/yellow/red), and a deterministic pace
projection ("at this pace → X by the goal date").

This module is the *pure transform* that reshapes already-computed KPI series into
that table. It does NOT add a second KPI engine: the per-metric weekly series come
from the existing sources — :func:`app.core.agent_kpis.agent_kpis` (the agent KPI
rollup) and :func:`app.core.scoreboard.build_scoreboard` (the leadership summaries).
The API layer samples those sources once per week into a :class:`MetricSeries` and
threads the series in here; this module only reshapes + projects.

Purity (CLAUDE.md §3, INV-2, A-7): a total function of its arguments + ``params``.
It imports stdlib + :class:`app.core.params.Params` only — no repository, adapter,
or ``httpx`` import, and NO ``datetime.now()`` (the reference date ``as_of`` is
INJECTED). The core-purity test guards the import surface. Same series + params +
``as_of`` ⇒ same scorecard.

Status reads ``params.kpi.scorecard`` (``green_at``/``yellow_at`` as fractions of
target); the pacing horizon is ``params.kpi.scorecard.goal_date`` (INV-11) — no
threshold or horizon is hardcoded here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.core.params import Params

# Days per week — the calendar constant for converting (goal_date - as_of) to weeks.
# A true constant of the Gregorian calendar, not a tunable, so it is not a param.
_DAYS_PER_WEEK = 7

# The sparkline is the trailing four weekly points (the "4-week sparkline" shape).
_SPARKLINE_WEEKS = 4


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """One metric's weekly series — the pure transform's input row.

    The API layer builds these from ``agent_kpis``/``scoreboard`` (one weekly
    sample per element); this module never re-derives the numbers.

    Attributes:
        key: Stable metric token (e.g. ``"enrollments"``).
        label: Human label for the table row.
        target: The metric's target value (the status band is a fraction of this).
        weekly_values: Chronological weekly values, oldest → newest; the last
            element is this week. May be a single point (a brand-new series).
    """

    key: str
    label: str
    target: float
    weekly_values: tuple[float, ...]

    def __init__(
        self,
        key: str,
        label: str,
        target: float,
        weekly_values: Sequence[float],
    ) -> None:
        # Freeze the series as a tuple so a computed metric can't mutate its input.
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "weekly_values", tuple(weekly_values))


@dataclass(frozen=True, slots=True)
class ScorecardMetric:
    """One reshaped metric row of the weekly scorecard (frozen output row).

    Attributes:
        key: The metric token (carried through from the series).
        label: The metric label.
        this_week: The latest weekly value.
        last_week: The prior weekly value (``0.0`` when the series has one point).
        delta: ``this_week - last_week`` — the worked invariant.
        sparkline: The trailing ≤4 weekly values, in order.
        target: The metric's target.
        status: ``"green"`` / ``"yellow"`` / ``"red"`` from the params band.
        projection: ``this_week + avg_weekly_delta * weeks_to_goal`` — the
            deterministic pace projection at ``params.kpi.scorecard.goal_date``.
    """

    key: str
    label: str
    this_week: float
    last_week: float
    delta: float
    sparkline: tuple[float, ...]
    target: float
    status: str
    projection: float


@dataclass(frozen=True, slots=True)
class WeeklyScorecard:
    """The whole weekly scorecard — the per-metric rows + the reference date.

    Attributes:
        metrics: The reshaped :class:`ScorecardMetric` rows, in input order.
        as_of: The injected reference date the projection extrapolated from.
    """

    metrics: tuple[ScorecardMetric, ...]
    as_of: date


def _status(this_week: float, target: float, *, green_at: float, yellow_at: float) -> str:
    """Map a value to green/yellow/red against fractions of ``target`` (INV-11).

    ``green`` when ``this_week >= green_at * target``; else ``yellow`` when
    ``this_week >= yellow_at * target``; else ``red``. Comparing against absolute
    thresholds (fraction × target) means a ``target`` of ``0`` degrades cleanly —
    both thresholds collapse to ``0`` and any non-negative value reads ``green`` —
    with no division.
    """
    if this_week >= green_at * target:
        return "green"
    if this_week >= yellow_at * target:
        return "yellow"
    return "red"


def _avg_weekly_delta(sparkline: tuple[float, ...]) -> float:
    """The average weekly change across the sparkline window (the pace rate).

    ``(last - first) / (points - 1)`` over the sparkline — the mean per-week step.
    A series with fewer than two points carries no inferable rate, so the pace is
    ``0.0`` (a flat hold, never a divide-by-zero).
    """
    if len(sparkline) < 2:
        return 0.0
    return (sparkline[-1] - sparkline[0]) / (len(sparkline) - 1)


def _project(
    this_week: float, sparkline: tuple[float, ...], *, as_of: date, goal_date: date
) -> float:
    """Project ``this_week`` to ``goal_date`` at the recent average weekly rate.

    ``this_week + avg_weekly_delta * weeks_to_goal`` where ``weeks_to_goal`` is
    ``(goal_date - as_of)`` in weeks, clamped at ``0`` (a goal already reached or
    past projects no further). Pure arithmetic on the injected ``as_of`` — no clock.
    """
    days_to_goal = (goal_date - as_of).days
    weeks_to_goal = max(days_to_goal, 0) / _DAYS_PER_WEEK
    return this_week + _avg_weekly_delta(sparkline) * weeks_to_goal


def build_weekly_scorecard(
    series: Iterable[MetricSeries],
    *,
    params: Params,
    as_of: date,
) -> WeeklyScorecard:
    """Reshape per-metric weekly series into the weekly scorecard (B5) — pure.

    For each :class:`MetricSeries`: ``this_week`` is the latest weekly value,
    ``last_week`` the prior one (``0.0`` for a one-point series), ``delta =
    this_week - last_week`` (the worked invariant), the ``sparkline`` is the
    trailing four weekly points, ``status`` comes from ``params.kpi.scorecard``
    (green/yellow/red vs target), and ``projection`` extrapolates the current value
    to ``params.kpi.scorecard.goal_date`` at the average weekly rate over the
    sparkline. The reference date ``as_of`` is injected; nothing reads a clock.

    Args:
        series: The per-metric weekly series (built by the API layer from
            ``agent_kpis``/``scoreboard`` — this module does not re-derive them).
            Consumed once (materialized internally), so a one-shot iterator is fine.
        params: Loaded params; supplies ``kpi.scorecard`` (the status band + the
            pacing ``goal_date``).
        as_of: The injected reference date weeks-to-goal is measured from.

    Returns:
        The :class:`WeeklyScorecard` the ``GET /scorecard/weekly`` route renders.

    Raises:
        ValueError: if a series carries no weekly values (this-week is undefined).
    """
    cfg = params.kpi.scorecard

    metrics: list[ScorecardMetric] = []
    for s in series:
        values = s.weekly_values
        if not values:
            raise ValueError(
                f"weekly scorecard: metric {s.key!r} has no weekly values; this_week is undefined"
            )
        this_week = values[-1]
        last_week = values[-2] if len(values) >= 2 else 0.0
        sparkline = values[-_SPARKLINE_WEEKS:]
        metrics.append(
            ScorecardMetric(
                key=s.key,
                label=s.label,
                this_week=this_week,
                last_week=last_week,
                delta=this_week - last_week,
                sparkline=sparkline,
                target=s.target,
                status=_status(this_week, s.target, green_at=cfg.green_at, yellow_at=cfg.yellow_at),
                projection=_project(this_week, sparkline, as_of=as_of, goal_date=cfg.goal_date),
            )
        )

    return WeeklyScorecard(metrics=tuple(metrics), as_of=as_of)

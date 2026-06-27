"""Pure weekly KPI scorecard transform (TODO_v2 §B5; INV-2/INV-11).

Pins the worked invariants of :func:`app.core.weekly_scorecard.build_weekly_scorecard`:

* ``delta == this_week - last_week`` exactly (the worked invariant);
* status (green/yellow/red) read from ``params.kpi.scorecard`` thresholds;
* a deterministic pace projection computed by hand for a known series + ``as_of``;
* the 4-week sparkline is the last four weekly points.

The transform is pure: the per-metric weekly series is threaded in (the API layer
builds it from ``agent_kpis``/``scoreboard``) and the reference date is injected.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.params import load_params
from app.core.weekly_scorecard import (
    MetricSeries,
    WeeklyScorecard,
    build_weekly_scorecard,
)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_delta_is_this_week_minus_last_week() -> None:
    """delta == this_week - last_week, exactly (the worked invariant)."""
    params = load_params(EXAMPLE_PARAMS)
    series = MetricSeries(
        key="enrollments",
        label="Enrollments",
        target=100.0,
        weekly_values=(10.0, 12.0, 14.0, 16.0),
    )
    card = build_weekly_scorecard([series], params=params, as_of=date(2026, 9, 2))

    assert isinstance(card, WeeklyScorecard)
    assert card.as_of == date(2026, 9, 2)
    metric = card.metrics[0]
    assert metric.this_week == 16.0
    assert metric.last_week == 14.0
    assert metric.delta == 16.0 - 14.0 == 2.0


def test_sparkline_is_last_four_weekly_points() -> None:
    """The sparkline is the trailing four weekly values, in order."""
    params = load_params(EXAMPLE_PARAMS)
    series = MetricSeries(
        key="contacts",
        label="Contacts",
        target=50.0,
        weekly_values=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
    )
    metric = build_weekly_scorecard([series], params=params, as_of=date(2026, 9, 2)).metrics[0]
    assert metric.sparkline == (3.0, 4.0, 5.0, 6.0)


def test_status_from_params_thresholds() -> None:
    """green/yellow/red read from params.kpi.scorecard (green_at=1.0, yellow_at=0.7)."""
    params = load_params(EXAMPLE_PARAMS)
    as_of = date(2026, 9, 2)
    target = 100.0

    # this_week == target (100 >= 1.0*100) ⇒ green.
    green = build_weekly_scorecard(
        [MetricSeries("a", "A", target, (90.0, 100.0))], params=params, as_of=as_of
    ).metrics[0]
    assert green.status == "green"

    # 80 >= 0.7*100=70 but < 100 ⇒ yellow.
    yellow = build_weekly_scorecard(
        [MetricSeries("b", "B", target, (70.0, 80.0))], params=params, as_of=as_of
    ).metrics[0]
    assert yellow.status == "yellow"

    # 50 < 0.7*100=70 ⇒ red.
    red = build_weekly_scorecard(
        [MetricSeries("c", "C", target, (40.0, 50.0))], params=params, as_of=as_of
    ).metrics[0]
    assert red.status == "red"


def test_pace_projection_is_deterministic() -> None:
    """Projection = this_week + avg_weekly_delta * weeks_to_goal (hand-computed)."""
    params = load_params(EXAMPLE_PARAMS)
    # goal_date is 2026-09-30; as_of 2026-09-02 ⇒ 28 days ⇒ exactly 4.0 weeks.
    assert params.kpi.scorecard.goal_date == date(2026, 9, 30)
    as_of = date(2026, 9, 2)

    series = MetricSeries(
        key="enrollments",
        label="Enrollments",
        target=100.0,
        weekly_values=(10.0, 12.0, 14.0, 16.0),
    )
    metric = build_weekly_scorecard([series], params=params, as_of=as_of).metrics[0]

    # avg weekly delta over the 4-pt sparkline = (16 - 10) / (4 - 1) = 2.0/week.
    # weeks_to_goal = 28 / 7 = 4.0. projection = 16 + 2.0 * 4.0 = 24.0.
    assert metric.projection == 24.0


def test_single_point_series_has_zero_pace_and_zero_last_week() -> None:
    """A one-week series can't infer a rate: last_week 0, delta = this_week, flat pace."""
    params = load_params(EXAMPLE_PARAMS)
    series = MetricSeries("solo", "Solo", 100.0, (7.0,))
    metric = build_weekly_scorecard([series], params=params, as_of=date(2026, 9, 2)).metrics[0]
    assert metric.last_week == 0.0
    assert metric.delta == 7.0
    assert metric.sparkline == (7.0,)
    # No rate inferable ⇒ projection holds at the current value.
    assert metric.projection == 7.0

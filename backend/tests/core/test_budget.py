"""Budget params partition guard + pure reconcile/variance core (TODO_v2 §B4).

Two pure pieces, both reading every number from params (CLAUDE.md INV-11):

  - the ``budget`` params block — the single canonical home for the $365K
    marketing budget and its per-workstream partition. A ``model_validator``
    enforces the PARTITION GUARD: the workstream amounts MUST sum to
    ``total_usd`` exactly (drift fails the build, CLAUDE.md §4.1).
  - ``core/budget.py`` — the pure variance reconciler: per workstream it
    computes ``(actual - planned) / planned`` and flags an OVERRUN past
    ``budget.variance_threshold`` (read from params, never a 0.10 literal).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.budget import (
    HEALTH_AT_RISK,
    HEALTH_ON_TRACK,
    HEALTH_WATCH,
    BudgetEntry,
    build_burn_series,
    project_burnout,
    reconcile,
    weekly_burn_rate,
)
from app.core.params import Budget, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_workstreams_sum_to_total() -> None:
    """The committed workstreams partition the $365K total exactly (partition guard)."""
    budget = load_params(EXAMPLE_PARAMS).budget

    assert isinstance(budget, Budget)
    assert budget.total_usd == 365000
    assert sum(budget.workstreams.values()) == budget.total_usd == 365000
    # The worked partition (TODO_v2 §B4).
    assert budget.workstreams == {
        "grassroots": 210000,
        "content": 90000,
        "guerrilla": 40000,
        "ops": 25000,
    }

    # A Budget whose workstreams do NOT sum to total is rejected at load (guard).
    with pytest.raises(ValidationError):
        Budget(
            total_usd=365000,
            variance_threshold=0.10,
            workstreams={"grassroots": 210000, "content": 90000},  # sums to 300000
        )


def test_variance_flags() -> None:
    """An OVERRUN past the params threshold flags; under/at threshold does not."""
    params = load_params(EXAMPLE_PARAMS)
    threshold = params.budget.variance_threshold
    assert threshold == 0.10  # the test reads the param; a drift fails here

    planned = 100_000
    # 11% over ⇒ flagged.
    over_11 = reconcile([BudgetEntry("grassroots", planned, 111_000)], params=params)
    row_11 = over_11.results[0]
    assert row_11.flagged is True
    assert row_11.variance == Decimal("0.11")
    assert "grassroots" in over_11.flagged

    # 9% over ⇒ NOT flagged.
    under_9 = reconcile([BudgetEntry("content", planned, 109_000)], params=params)
    assert under_9.results[0].flagged is False
    assert under_9.flagged == ()

    # Exactly at threshold (10% over) ⇒ NOT flagged (strict >, not >=).
    at_thresh = reconcile([BudgetEntry("guerrilla", planned, 110_000)], params=params)
    assert at_thresh.results[0].flagged is False

    # Under budget (actual < planned) ⇒ never flagged (negative variance).
    under_budget = reconcile([BudgetEntry("ops", planned, 90_000)], params=params)
    row_ub = under_budget.results[0]
    assert row_ub.flagged is False
    assert row_ub.variance == Decimal("-0.1")
    assert row_ub.remaining == Decimal("10000")


def test_reconcile_rolls_up_totals() -> None:
    """The reconciliation exposes the roll-up vs the params total_usd."""
    params = load_params(EXAMPLE_PARAMS)
    recon = reconcile(
        [
            BudgetEntry("grassroots", 210_000, 200_000),
            BudgetEntry("content", 90_000, 95_000),
        ],
        params=params,
    )
    assert recon.total_planned == Decimal("300000")
    assert recon.total_actual == Decimal("295000")
    assert recon.total_remaining == Decimal("5000")
    assert recon.total_usd == 365000


# ----------------------------------------------------------------------- health bands
def test_health_bands() -> None:
    """on_track / watch / at_risk follow the params bands (watch_frac + variance_threshold)."""
    params = load_params(EXAMPLE_PARAMS)
    assert params.budget.watch_frac == 0.85  # the test reads the param; a drift fails here

    planned = 100_000
    recon = reconcile(
        [
            BudgetEntry("grassroots", planned, 50_000),  # 50% → on_track
            BudgetEntry("content", planned, 90_000),  # 90% (>= 85%, not over) → watch
            BudgetEntry("guerrilla", planned, 105_000),  # over budget (5% over) → at_risk
            BudgetEntry("ops", planned, 130_000),  # 30% over (> threshold) → at_risk
        ],
        params=params,
    )
    by_ws = {r.workstream: r.health for r in recon.results}
    assert by_ws["grassroots"] == HEALTH_ON_TRACK
    assert by_ws["content"] == HEALTH_WATCH
    assert by_ws["guerrilla"] == HEALTH_AT_RISK
    assert by_ws["ops"] == HEALTH_AT_RISK


# ------------------------------------------------------------------- weekly burn series
def test_burn_series_buckets_cumulatively() -> None:
    """Per-ISO-week buckets carry the CUMULATIVE actual; the plan line rises to total."""
    # Three actuals across three consecutive ISO weeks (Mondays 2026-06-01/08/15).
    dated_actuals = [
        (date(2026, 6, 3), Decimal("10000")),  # week of 06-01
        (date(2026, 6, 10), Decimal("20000")),  # week of 06-08
        (date(2026, 6, 17), Decimal("30000")),  # week of 06-15
    ]
    series = build_burn_series(
        dated_actuals, total_planned=Decimal("120000"), as_of=date(2026, 6, 17)
    )
    assert [w.week_start for w in series.weeks] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
    ]
    # Cumulative actual rises monotonically: 10k → 30k → 60k.
    assert [w.cumulative_actual for w in series.weeks] == [
        Decimal("10000"),
        Decimal("30000"),
        Decimal("60000"),
    ]
    # Straight plan line: total_planned apportioned linearly across the 3 buckets.
    assert series.weeks[-1].cumulative_planned == Decimal("120000")
    assert series.weeks[0].cumulative_planned == Decimal("40000")


def test_burn_series_empty_with_no_actuals() -> None:
    """No dated actuals ⇒ no period to bucket ⇒ an empty series (never a backwards range)."""
    series = build_burn_series([], total_planned=Decimal("100000"), as_of=date(2026, 6, 15))
    assert series.weeks == ()


# ------------------------------------------------------------------ projected burn-out
def test_projected_burnout_math_and_zero_burn() -> None:
    """Burn-out = as_of + remaining/rate weeks; None when the rate is zero (no div-by-0)."""
    as_of = date(2026, 6, 15)
    # remaining 40000 at 10000/week ⇒ 4 weeks ⇒ 28 days out.
    out = project_burnout(Decimal("40000"), Decimal("10000"), as_of=as_of)
    assert out == date(2026, 7, 13)

    # Zero burn rate ⇒ no burn-out date (idle budget; never a divide-by-zero).
    assert project_burnout(Decimal("40000"), Decimal("0"), as_of=as_of) is None

    # Already over budget (non-positive remaining) ⇒ burned out as of now.
    assert project_burnout(Decimal("-5000"), Decimal("10000"), as_of=as_of) == as_of


def test_weekly_burn_rate_zero_for_empty_series() -> None:
    """An empty burn series yields a zero weekly rate (the burn-out denominator guard)."""
    series = build_burn_series([], total_planned=Decimal("100000"), as_of=date(2026, 6, 15))
    assert weekly_burn_rate(series) == Decimal("0")

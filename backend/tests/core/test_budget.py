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

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.budget import BudgetEntry, reconcile
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

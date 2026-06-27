"""kpi.scorecard params sub-block — parse + drift guard (TODO_v2 §B5; INV-11).

The weekly KPI scorecard reads its status thresholds and pacing horizon from
``params.kpi.scorecard`` (never a code literal). This pins that the committed
example parses into a strict :class:`Scorecard` sub-model and that a drifted
band (``yellow_at`` above ``green_at``) fails the build at load.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import Scorecard, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_scorecard_block_loads() -> None:
    """``kpi.scorecard`` parses: status band fractions + the pacing goal_date."""
    scorecard = load_params(EXAMPLE_PARAMS).kpi.scorecard

    assert isinstance(scorecard, Scorecard)
    # Status band: green_at >= yellow_at, both fractions of target.
    assert scorecard.green_at == 1.0
    assert scorecard.yellow_at == 0.7
    # The pacing horizon — the projection target date.
    assert isinstance(scorecard.goal_date, date)
    assert scorecard.goal_date == date(2026, 9, 30)


def test_scorecard_band_drift_raises() -> None:
    """A band with yellow_at ABOVE green_at is rejected at load (drift guard)."""
    with pytest.raises(ValidationError):
        Scorecard(green_at=0.7, yellow_at=1.0, goal_date=date(2026, 9, 30))

    # A non-positive band fraction is also rejected.
    with pytest.raises(ValidationError):
        Scorecard(green_at=1.0, yellow_at=0.0, goal_date=date(2026, 9, 30))

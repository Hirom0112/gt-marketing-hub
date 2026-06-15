"""Nudge-trigger precision/recall eval tests — FR-4.1 (CLAUDE §4.1, INV-3/INV-11).

The nudge classifier ships ONLY if both precision and recall clear their
thresholds in `params/params.yaml` (INV-3, fail-closed). This module pins the
WORKED NUMERIC TARGET from CLAUDE §4.1: on a golden set with TP=8, FP=1, FN=2 ⇒
precision 8/9 = 0.8889 and recall 8/10 = 0.8000 (to 4 dp). The `passed` gate
reads `min_precision`/`min_recall` from params (never a hardcoded 0.85/0.70), so
a param drift fails the test (INV-11). Thresholds are read from the committed
example params, mirroring the other eval tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.evals.metrics import PrecisionRecall, evaluate_nudge, precision_recall

from app.core.params import Params, load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def test_nudge_precision_recall_worked_target(params: Params) -> None:
    """TP=8, FP=1, FN=2 ⇒ precision 0.8889, recall 0.8000; passed reads params."""
    pr = precision_recall(tp=8, fp=1, fn=2)
    assert isinstance(pr, PrecisionRecall)
    # Worked target to 4 dp (CLAUDE §4.1): precision 8/9, recall 8/10.
    assert round(pr.precision, 4) == 0.8889
    assert round(pr.recall, 4) == 0.8000

    # The `passed` gate must read both thresholds from params (INV-11): a drift
    # in min_precision/min_recall must move this assertion, so we read them here.
    result = evaluate_nudge(tp=8, fp=1, fn=2, params=params)
    min_precision = params.eval_thresholds.nudge_trigger.min_precision
    min_recall = params.eval_thresholds.nudge_trigger.min_recall
    assert round(result.precision, 4) == 0.8889
    assert round(result.recall, 4) == 0.8000
    # 0.8889 ≥ 0.85 ✓ AND 0.8000 ≥ 0.70 ✓ ⇒ passed True (with example params).
    assert result.passed is (result.precision >= min_precision and result.recall >= min_recall)
    assert result.passed is True


def test_nudge_clears_precision_but_misses_recall_fails_closed(params: Params) -> None:
    """Precision cleared, recall below threshold ⇒ passed False (fail-closed)."""
    # TP=7, FP=0, FN=5 ⇒ precision 1.0 (≥0.85) but recall 7/12 ≈ 0.5833 (<0.70).
    result = evaluate_nudge(tp=7, fp=0, fn=5, params=params)
    min_recall = params.eval_thresholds.nudge_trigger.min_recall
    assert result.precision == 1.0
    assert result.recall < min_recall
    assert result.passed is False


def test_precision_recall_degenerate_cases_no_zero_division() -> None:
    """tp+fp==0 ⇒ precision 0.0; tp+fn==0 ⇒ recall 0.0 (no ZeroDivisionError)."""
    no_predictions = precision_recall(tp=0, fp=0, fn=4)
    assert no_predictions.precision == 0.0
    assert round(no_predictions.recall, 4) == 0.0

    no_positives = precision_recall(tp=0, fp=3, fn=0)
    assert no_positives.recall == 0.0
    assert round(no_positives.precision, 4) == 0.0

    empty = precision_recall(tp=0, fp=0, fn=0)
    assert empty.precision == 0.0
    assert empty.recall == 0.0

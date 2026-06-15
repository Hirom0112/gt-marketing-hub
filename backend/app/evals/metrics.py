"""Pure eval metrics — nudge precision/recall + doc-extraction accuracy.

FR-4.1 (nudge classifier) and FR-4.2 (application-form field extraction);
CLAUDE §4.1 worked numeric targets; INV-3/INV-11.

Two layers:

* Pure metric functions (`precision_recall`, `extraction_accuracy`) compute the
  numbers and define every degenerate case so they never raise — a
  classifier-quality metric must always return a value.
* Thin `evaluate_*` gates fold in the `passed` decision by reading the relevant
  threshold from `params` (INV-11): the action ships ONLY when the metric clears
  its threshold, and fails closed otherwise (INV-3). The thresholds have their
  single home in `params/params.yaml`; nothing here hardcodes 0.85/0.70/0.90.

This module is PURE: stdlib + typing + the `Params` *type* from
`app.core.params`. No anthropic/langgraph, no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from app.core.params import Params


class PrecisionRecall(NamedTuple):
    """Precision and recall over a golden set (immutable result, FR-4.1)."""

    precision: float
    recall: float


class NudgeEval(NamedTuple):
    """Nudge precision/recall plus the params-gated pass/fail (FR-4.1, INV-3)."""

    precision: float
    recall: float
    min_precision: float
    min_recall: float
    passed: bool


def precision_recall(tp: int, fp: int, fn: int) -> PrecisionRecall:
    """Precision = tp/(tp+fp), recall = tp/(tp+fn) over a golden set.

    Worked target (CLAUDE §4.1): tp=8, fp=1, fn=2 ⇒ precision 8/9 = 0.8889,
    recall 8/10 = 0.8000 (to 4 dp). Degenerate cases are defined so the metric
    never raises: with no predicted positives (tp+fp == 0) precision is 0.0, and
    with no actual positives (tp+fn == 0) recall is 0.0.
    """
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return PrecisionRecall(precision=precision, recall=recall)


def evaluate_nudge(*, tp: int, fp: int, fn: int, params: Params) -> NudgeEval:
    """Compute nudge precision/recall and gate on params thresholds (INV-3/11).

    `passed` is True ONLY when precision ≥
    `params.eval_thresholds.nudge_trigger.min_precision` AND recall ≥
    `...min_recall` — both read from params, never hardcoded. Missing either
    threshold disables the action (fail-closed).
    """
    pr = precision_recall(tp, fp, fn)
    thresholds = params.eval_thresholds.nudge_trigger
    passed = pr.precision >= thresholds.min_precision and pr.recall >= thresholds.min_recall
    return NudgeEval(
        precision=pr.precision,
        recall=pr.recall,
        min_precision=thresholds.min_precision,
        min_recall=thresholds.min_recall,
        passed=passed,
    )


class DocExtractionEval(NamedTuple):
    """Doc-extraction accuracy plus the params-gated pass/fail (FR-4.2, INV-3)."""

    accuracy: float
    min_accuracy: float
    passed: bool


def extraction_accuracy(
    predicted: Mapping[str, object],
    ground_truth: Mapping[str, object],
) -> float:
    """Field-level accuracy of extracted fields vs ground truth (ARCH §4.3).

    Accuracy = (# ground-truth fields whose predicted value equals the
    ground-truth value) / (total ground-truth fields). A missing predicted key
    counts as a miss. Extra predicted keys not present in ground truth are
    ignored — only the ground-truth fields are scored. Empty ground truth ⇒ 1.0
    (vacuously perfect; never divides by zero).
    """
    total = len(ground_truth)
    if total == 0:
        return 1.0
    _sentinel = object()
    matched = sum(
        1 for key, value in ground_truth.items() if predicted.get(key, _sentinel) == value
    )
    return matched / total


def evaluate_doc_extraction(
    predicted: Mapping[str, object],
    ground_truth: Mapping[str, object],
    *,
    params: Params,
) -> DocExtractionEval:
    """Compute doc-extraction accuracy and gate on the params threshold.

    `passed` is True iff accuracy ≥
    `params.eval_thresholds.doc_extraction.min_accuracy` (read from params,
    INV-11). Below threshold disables the extraction action (fail-closed,
    INV-3).
    """
    accuracy = extraction_accuracy(predicted, ground_truth)
    min_accuracy = params.eval_thresholds.doc_extraction.min_accuracy
    return DocExtractionEval(
        accuracy=accuracy,
        min_accuracy=min_accuracy,
        passed=accuracy >= min_accuracy,
    )

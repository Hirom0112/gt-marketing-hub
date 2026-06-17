"""Doc-extraction accuracy eval tests — FR-4.2 (CLAUDE §4.1, INV-3/INV-11).

Field-level accuracy of an LLM's extracted application-form fields vs the
ground-truth `app_form.extracted_fields` (ARCH §4.3). Accuracy is the fraction
of ground-truth fields the prediction reproduces exactly. The extraction action
ships ONLY when accuracy clears `params.eval_thresholds.doc_extraction
.min_accuracy` (INV-3, fail-closed); the threshold is read from params (INV-11),
never hardcoded. The expected accuracy is computed from the fixture in the test
(not a baked literal), so the metric must reproduce it exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.params import Params, load_params
from app.evals.metrics import evaluate_doc_extraction, extraction_accuracy

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# Ground-truth application form fields (ARCH §4.3 `app_form.extracted_fields`).
_GROUND_TRUTH: dict[str, object] = {
    "student_first_name": "Ada",
    "student_last_name": "Lovelace",
    "grade_level": 6,
    "guardian_email": "guardian@example.com",
    "guardian_phone": "555-0100",
    "program": "Gifted Online",
    "start_term": "Fall 2026",
    "homeschool": False,
    "iep_on_file": True,
    "household_size": 4,
}

# A prediction that matches 9 of 10 fields (grade_level wrong) ⇒ accuracy 0.90,
# which is exactly at the example-params threshold (≥ ⇒ passes).
_PREDICTED_AT_THRESHOLD: dict[str, object] = {
    "student_first_name": "Ada",
    "student_last_name": "Lovelace",
    "grade_level": 7,  # wrong
    "guardian_email": "guardian@example.com",
    "guardian_phone": "555-0100",
    "program": "Gifted Online",
    "start_term": "Fall 2026",
    "homeschool": False,
    "iep_on_file": True,
    "household_size": 4,
}

# A weaker prediction matching 6 of 10 ⇒ accuracy 0.60 (< 0.90 ⇒ fails).
_PREDICTED_BELOW: dict[str, object] = {
    "student_first_name": "Ada",
    "student_last_name": "Lovelace",
    "grade_level": 7,  # wrong
    "guardian_email": "wrong@example.com",  # wrong
    "guardian_phone": "555-9999",  # wrong
    "program": "Gifted Online",
    "start_term": "Spring 2027",  # wrong
    "homeschool": False,
    "iep_on_file": True,
    "household_size": 4,
}


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _expected_accuracy(predicted: dict[str, object], ground_truth: dict[str, object]) -> float:
    """Recompute accuracy from the fixture (not a baked literal)."""
    matched = sum(1 for k, v in ground_truth.items() if predicted.get(k) == v)
    return matched / len(ground_truth)


def test_doc_extraction_accuracy_vs_ground_truth(params: Params) -> None:
    """Accuracy == fixture-derived fraction; passed iff ≥ params min_accuracy."""
    expected = _expected_accuracy(_PREDICTED_AT_THRESHOLD, _GROUND_TRUTH)
    # Fixture is constructed to be exactly at the threshold (0.90).
    assert expected == 0.90

    accuracy = extraction_accuracy(_PREDICTED_AT_THRESHOLD, _GROUND_TRUTH)
    assert accuracy == expected

    min_accuracy = params.eval_thresholds.doc_extraction.min_accuracy
    result = evaluate_doc_extraction(_PREDICTED_AT_THRESHOLD, _GROUND_TRUTH, params=params)
    assert result.accuracy == expected
    # passed reads threshold from params (INV-11): drift here fails the test.
    assert result.passed is (result.accuracy >= min_accuracy)
    assert result.passed is True


def test_doc_extraction_below_threshold_fails_closed(params: Params) -> None:
    """Accuracy below params min_accuracy ⇒ passed False (fail-closed)."""
    expected = _expected_accuracy(_PREDICTED_BELOW, _GROUND_TRUTH)
    accuracy = extraction_accuracy(_PREDICTED_BELOW, _GROUND_TRUTH)
    assert accuracy == expected

    min_accuracy = params.eval_thresholds.doc_extraction.min_accuracy
    result = evaluate_doc_extraction(_PREDICTED_BELOW, _GROUND_TRUTH, params=params)
    assert result.accuracy < min_accuracy
    assert result.passed is False


def test_extraction_accuracy_empty_ground_truth_is_one() -> None:
    """Empty ground_truth ⇒ 1.0 (vacuously perfect; no div-by-zero)."""
    assert extraction_accuracy({}, {}) == 1.0
    # Extra predicted fields beyond ground truth do not lower accuracy.
    assert extraction_accuracy({"a": 1}, {}) == 1.0

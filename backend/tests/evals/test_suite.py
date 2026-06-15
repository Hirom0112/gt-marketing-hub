"""Consolidated eval-suite runner tests — FR-4.5 (ARCH §6; INV-3/INV-11).

ONE runner over all four evals — nudge_trigger, doc_extraction,
message_safety_grounding, geo_tracking — producing the green/red scoreboard
(FR-4.5). Each eval becomes one `EvalRow(eval_name, score, threshold, passed)`;
`overall_green = all(rows passed)`. The consolidation is **fail-closed**: a
single red eval turns the whole scoreboard red (INV-3) — the same discipline
the per-eval gates enforce, lifted to the suite.

Every threshold a row reports READS from params (INV-11) — these tests assert
each row's `threshold` equals the params value loaded from the committed
`params/params.example.yaml`, so a param drift fails the suite, never a magic
number in code. The grounding row reuses the exact golden-set scoring approach
of `test_message_safety_grounding.py` (run `evaluate_message` per jsonl row,
accuracy = fraction where `result.passed == row.expected_passed`), with an
on-brand judge injected so the PASS rows actually pass V-4. The geo row runs
the simulated adapter at a fixed seed for determinism (no wall-clock).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.adapters.geo_sampling.base import GeoObservation
from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.ai.schemas.enrollment_draft import EnrollmentDraftProposal
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.evals.suite import EvalRow, EvalSuiteResult, run_suite

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
GOLDEN = Path(__file__).resolve().parent / "golden" / "enrollment_drafts.jsonl"
CLOSE_TIPS_GOLDEN = Path(__file__).resolve().parent / "golden" / "close_tips.jsonl"

_PROMPT_SET = (
    "best gifted school online",
    "accelerated learning program for gifted kids",
    "personalized gifted education",
)

# Nudge counts that clear BOTH thresholds (min_precision 0.85, min_recall 0.70):
# tp=18, fp=2, fn=2 ⇒ precision 0.9000, recall 0.9000 — both above floor.
_NUDGE_PASS = {"tp": 18, "fp": 2, "fn": 2}
# Recall below the 0.70 floor: tp=6, fp=0, fn=6 ⇒ precision 1.0, recall 0.5000.
_NUDGE_FAIL_RECALL = {"tp": 6, "fp": 0, "fn": 6}

# A doc-extraction golden that clears min_accuracy 0.90: 10/10 fields correct.
_DOC_PREDICTED = {f"f{i}": i for i in range(10)}
_DOC_GROUND_TRUTH = {f"f{i}": i for i in range(10)}

_EVAL_NAMES = {
    "nudge_trigger",
    "doc_extraction",
    "message_safety_grounding",
    "geo_tracking",
}


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


@pytest.fixture
def settings_no_key() -> Settings:
    s = Settings()
    assert s.llm_available is False
    return s


def _on_brand_judge(proposal: EnrollmentDraftProposal, never_rules: list[str]) -> float:
    """Deterministic stub judge: a high conformance score (on-brand)."""
    return 0.99


def _golden_drafts() -> list[dict[str, object]]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _close_tips_golden() -> list[dict[str, object]]:
    return [json.loads(line) for line in CLOSE_TIPS_GOLDEN.read_text().splitlines() if line.strip()]


def _geo_observations(min_samples: int) -> list[GeoObservation]:
    return SimulatedGeoSamplingAdapter().sample(
        _PROMPT_SET,
        engine="sim-engine",
        min_samples_per_prompt=min_samples,
        seed=0,
    )


def _row_by_name(result: EvalSuiteResult, name: str) -> EvalRow:
    return next(r for r in result.rows if r.eval_name == name)


def test_eval_suite_returns_scoreboard(params: Params, settings_no_key: Settings) -> None:
    """Green path: all four evals clear threshold ⇒ overall_green True (FR-4.5)."""
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt

    result = run_suite(
        settings=settings_no_key,
        params=params,
        golden_drafts=_golden_drafts(),
        nudge_counts=_NUDGE_PASS,
        doc_golden=(_DOC_PREDICTED, _DOC_GROUND_TRUTH),
        geo_observations=_geo_observations(min_samples),
        brand_judge=_on_brand_judge,
    )

    assert isinstance(result, EvalSuiteResult)
    # Exactly the four named eval rows, no duplicates.
    assert len(result.rows) == 4
    assert {r.eval_name for r in result.rows} == _EVAL_NAMES

    # Each row's threshold equals the params value (INV-11 — no magic number).
    nudge = _row_by_name(result, "nudge_trigger")
    assert nudge.threshold == params.eval_thresholds.nudge_trigger.min_precision
    assert nudge.passed is True

    doc = _row_by_name(result, "doc_extraction")
    assert doc.threshold == params.eval_thresholds.doc_extraction.min_accuracy
    assert doc.score == pytest.approx(1.0)
    assert doc.passed is True

    grounding = _row_by_name(result, "message_safety_grounding")
    assert grounding.threshold == (params.eval_thresholds.message_safety_grounding.min_grounding)
    assert grounding.passed is True

    geo = _row_by_name(result, "geo_tracking")
    assert geo.threshold == float(params.eval_thresholds.geo_tracking.min_samples_per_prompt)
    assert geo.passed is True

    # All rows green ⇒ overall green.
    assert all(r.passed for r in result.rows)
    assert result.overall_green is True


def test_eval_suite_is_fail_closed_when_one_eval_is_red(
    params: Params, settings_no_key: Settings
) -> None:
    """A single red eval (nudge recall below floor) turns the scoreboard red.

    Fail-closed consolidation (INV-3): the other three evals still pass, but the
    overall verdict goes red because the nudge row failed.
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt

    result = run_suite(
        settings=settings_no_key,
        params=params,
        golden_drafts=_golden_drafts(),
        nudge_counts=_NUDGE_FAIL_RECALL,
        doc_golden=(_DOC_PREDICTED, _DOC_GROUND_TRUTH),
        geo_observations=_geo_observations(min_samples),
        brand_judge=_on_brand_judge,
    )

    assert len(result.rows) == 4
    nudge = _row_by_name(result, "nudge_trigger")
    assert nudge.passed is False
    # The other three still pass — only nudge is red.
    assert _row_by_name(result, "doc_extraction").passed is True
    assert _row_by_name(result, "message_safety_grounding").passed is True
    assert _row_by_name(result, "geo_tracking").passed is True
    # One red eval ⇒ the whole scoreboard is red (fail-closed).
    assert result.overall_green is False


def test_eval_suite_includes_close_tips_row_when_golden_supplied(
    params: Params, settings_no_key: Settings
) -> None:
    """S9 W5: a supplied close-tips golden set appends a green ``close_tips`` row.

    The row is ADDITIVE (existing 4-row callers are unchanged): with the committed
    close-tips golden set + an on-brand judge, the row clears its
    ``close_tips.min_grounding`` floor (INV-11) and the scoreboard stays green.
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    result = run_suite(
        settings=settings_no_key,
        params=params,
        golden_drafts=_golden_drafts(),
        nudge_counts=_NUDGE_PASS,
        doc_golden=(_DOC_PREDICTED, _DOC_GROUND_TRUTH),
        geo_observations=_geo_observations(min_samples),
        close_tips_golden=_close_tips_golden(),
        brand_judge=_on_brand_judge,
    )
    assert len(result.rows) == 5
    close_tips = _row_by_name(result, "close_tips")
    assert close_tips.threshold == params.eval_thresholds.close_tips.min_grounding
    assert close_tips.passed is True
    assert result.overall_green is True


def test_eval_row_is_frozen(params: Params, settings_no_key: Settings) -> None:
    """`EvalRow` is immutable — a scoreboard verdict is never mutated post hoc."""
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    result = run_suite(
        settings=settings_no_key,
        params=params,
        golden_drafts=_golden_drafts(),
        nudge_counts=_NUDGE_PASS,
        doc_golden=(_DOC_PREDICTED, _DOC_GROUND_TRUTH),
        geo_observations=_geo_observations(min_samples),
        brand_judge=_on_brand_judge,
    )
    row = result.rows[0]
    with pytest.raises(ValidationError):
        row.passed = False  # type: ignore[misc]

"""GEO tracking eval tests — FR-4.4 (CONTENT_SPEC §7.4, RESEARCH Q5, INV-3/INV-11).

GEO coverage is *stochastic*: identical prompts yield different citations, so a
single snapshot is invalid (CONTENT_SPEC §7.4, LOCKED). The eval measures
coverage by **repeated sampling with variance reported**, against the **0%
baseline** (`params.geo.baseline_coverage`), and reports the **lift**
(coverage − baseline). Fewer than `min_samples_per_prompt` runs ⇒ a point
estimate CANNOT be asserted: the result is flagged insufficient and the GEO
action is **disabled/red** (fail-closed, INV-3; ARCH §9 failure table).

INV-11: `min_samples_per_prompt` has ONE home — params. The eval reads it from
params; `test_param_drift` proves the insufficient-samples boundary moves with
the param (no hardcoded `5`). All thresholds read from the committed example
params, mirroring `test_geo_metrics.py` / `test_content_gate.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.evals.geo_tracking_eval import GeoTrackingResult, evaluate_geo_tracking

from app.adapters.geo_sampling.base import GeoObservation
from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.core.params import GeoTracking, Params, load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# A small fixed prompt set — the simulated adapter is deterministic under
# (prompt, run_index, seed), so observations and stats are stable across runs.
_PROMPT_SET = (
    "best gifted school online",
    "accelerated learning program for gifted kids",
    "personalized gifted education",
)


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _with_min_samples(params: Params, min_samples_per_prompt: int) -> Params:
    """Return a copy of `params` whose geo_tracking threshold is overridden.

    Used by `test_param_drift` to prove the eval's insufficient-samples boundary
    is param-derived (INV-11), not a hardcoded constant.
    """
    geo_tracking = GeoTracking(
        min_samples_per_prompt=min_samples_per_prompt,
        report_variance=params.eval_thresholds.geo_tracking.report_variance,
    )
    eval_thresholds = params.eval_thresholds.model_copy(update={"geo_tracking": geo_tracking})
    return params.model_copy(update={"eval_thresholds": eval_thresholds})


def _sample(min_samples_per_prompt: int) -> list[GeoObservation]:
    """Deterministic observation stream over the fixed prompt set (offline)."""
    return SimulatedGeoSamplingAdapter().sample(
        _PROMPT_SET,
        engine="sim-engine",
        min_samples_per_prompt=min_samples_per_prompt,
        seed=0,
    )


def test_reads_threshold_and_reports_variance(params: Params) -> None:
    """Eval reads the params threshold, measures coverage vs 0% baseline, and
    reports variance with enough runs (FR-4.4, INV-3).
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    observations = _sample(min_samples)

    result = evaluate_geo_tracking(observations, params=params)

    assert isinstance(result, GeoTrackingResult)
    # Baseline is the 0% baseline from params — coverage measured against it.
    assert result.baseline == params.geo.baseline_coverage == 0.0
    # Enough distinct runs (== min_samples) ⇒ a point estimate is assertable.
    assert result.sample_count == min_samples
    assert result.insufficient_samples is False
    assert result.enabled is True
    # Coverage is a valid fraction and lift is coverage minus the baseline.
    assert 0.0 <= result.coverage_mean <= 1.0
    assert result.lift == pytest.approx(result.coverage_mean - result.baseline)
    # Variance is reported (population variance, non-negative).
    assert result.variance >= 0.0


def test_insufficient_samples_disables_action(params: Params) -> None:
    """Fewer than `min_samples_per_prompt` runs ⇒ insufficient + fail-closed
    (action disabled/red), per ARCH §9 and INV-3.
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    observations = _sample(min_samples - 1)

    result = evaluate_geo_tracking(observations, params=params)

    assert result.sample_count == min_samples - 1
    assert result.insufficient_samples is True
    assert result.enabled is False


def test_empty_observations_fail_closed(params: Params) -> None:
    """No observations at all ⇒ insufficient and disabled (fail-closed)."""
    result = evaluate_geo_tracking([], params=params)

    assert result.sample_count == 0
    assert result.insufficient_samples is True
    assert result.enabled is False
    assert result.coverage_mean == 0.0


def test_param_drift(params: Params) -> None:
    """INV-11: the insufficient-samples boundary must move with the param.

    A hardcoded threshold (e.g. `5`) would NOT shift when params change, so this
    test fails if anyone hardcodes it. With a fixed number of runs, raising the
    threshold above it must flip the result from enabled to disabled.
    """
    runs = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    observations = _sample(runs)

    # At threshold == runs: sufficient, action enabled.
    at_boundary = evaluate_geo_tracking(observations, params=_with_min_samples(params, runs))
    assert at_boundary.insufficient_samples is False
    assert at_boundary.enabled is True

    # Raise the param above the run count: now insufficient + disabled. If the
    # eval hardcoded the threshold, this would NOT change — the test would fail.
    raised = evaluate_geo_tracking(observations, params=_with_min_samples(params, runs + 1))
    assert raised.insufficient_samples is True
    assert raised.enabled is False

    # Lower the param below the run count: comfortably sufficient + enabled.
    lowered = evaluate_geo_tracking(observations, params=_with_min_samples(params, runs - 1))
    assert lowered.insufficient_samples is False
    assert lowered.enabled is True


def test_report_variance_param_gates_surfacing(params: Params) -> None:
    """`report_variance=False` suppresses the surfaced variance (set to 0.0),
    while the rest of the report is unchanged (param honored).
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    observations = _sample(min_samples)

    eval_thresholds_off = params.eval_thresholds.model_copy(
        update={
            "geo_tracking": GeoTracking(min_samples_per_prompt=min_samples, report_variance=False)
        }
    )
    params_off = params.model_copy(update={"eval_thresholds": eval_thresholds_off})

    result = evaluate_geo_tracking(observations, params=params_off)
    assert result.variance == 0.0
    # Coverage/lift/enabled are unaffected by the variance-surfacing toggle.
    assert result.enabled is True
    assert 0.0 <= result.coverage_mean <= 1.0

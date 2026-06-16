"""Params-loader tests (S0; ARCHITECTURE.md §8, CLAUDE.md INV-11, §4.1).

The params file is the single home for every magic number. `load_params`
parses it into typed Pydantic models; every consumer reads values from here,
never hardcoded. These tests assert the committed values come *from the YAML*
and that drift (missing key / wrong type) fails the build (TDD strict, §4.1).

The tests are deterministic without a local `params/params.yaml` (gitignored,
not created): they pass the committed `params/params.example.yaml` explicitly.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.core.params import (
    ContactWindows,
    CreatorScoringFit,
    MessageSafetyGrounding,
    load_params,
)

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_loads_work_queue_and_funding_and_thresholds() -> None:
    """Typed params expose §8 values read from the YAML, not hardcoded."""
    params = load_params(EXAMPLE_PARAMS)

    assert params.work_queue.w_recoverability == 0.6
    assert params.funding.award_amounts.tefa_standard == 10474
    assert params.funding.installment_split == [0.25, 0.25, 0.50]
    assert params.eval_thresholds.message_safety_grounding.min_grounding == 0.95
    # V-4 brand-voice bar is a DISTINCT param from the V-2 grounding floor (INV-11).
    assert params.eval_thresholds.message_safety_grounding.min_brand_score == 0.80


def test_missing_required_key_raises(tmp_path: Path) -> None:
    """A params doc missing a required key fails loudly — drift fails the build.

    `funding.award_amounts` drops `tefa_standard`; strict validation must raise
    a clear, typed `ValidationError` naming the offending field, never silently
    default it (CLAUDE.md §4.1, INV-11).
    """
    broken = textwrap.dedent(
        """\
        work_queue:
          w_recoverability: 0.6
          w_value: 0.4
          recoverability:
            stall_recency_weight: 0.5
            stage_proximity_weight: 0.3
            responsiveness_weight: 0.2
          value:
            tuition_annual_default: 10400
            max_children: 5
          stall_window_days: 14
        funding:
          award_amounts:
            tefa_disability: 30000
            tefa_homeschool: 2000
          installment_split: [0.25, 0.25, 0.50]
          tuition_unlock_state: first_installment_received
        eval_thresholds:
          nudge_trigger:
            min_precision: 0.85
            min_recall: 0.70
          doc_extraction:
            min_accuracy: 0.90
          message_safety_grounding:
            min_grounding: 0.95
            max_unverifiable_claims: 0
            require_coppa_safe: true
          geo_tracking:
            min_samples_per_prompt: 5
            report_variance: true
        cost_caps:
          anthropic_per_run_usd: 5.00
          media_gen_per_run_usd: 0.00
        latency_budget_ms:
          ai_proposal: 8000
        geo:
          prompt_set_size: 30
          cadence: weekly
          baseline_coverage: 0.0
        """
    )
    broken_path = tmp_path / "params.yaml"
    broken_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        load_params(broken_path)
    assert "tefa_standard" in str(excinfo.value)


def test_wrong_type_raises(tmp_path: Path) -> None:
    """A value of the wrong type fails validation too (drift fails the build).

    `w_recoverability` is set to a non-numeric string; strict typing must raise
    a clear, typed `ValidationError` naming the field.
    """
    broken = textwrap.dedent(
        """\
        work_queue:
          w_recoverability: not_a_number
          w_value: 0.4
          recoverability:
            stall_recency_weight: 0.5
            stage_proximity_weight: 0.3
            responsiveness_weight: 0.2
          value:
            tuition_annual_default: 10400
            max_children: 5
          stall_window_days: 14
        funding:
          award_amounts:
            tefa_standard: 10474
            tefa_disability: 30000
            tefa_homeschool: 2000
          installment_split: [0.25, 0.25, 0.50]
          tuition_unlock_state: first_installment_received
        eval_thresholds:
          nudge_trigger:
            min_precision: 0.85
            min_recall: 0.70
          doc_extraction:
            min_accuracy: 0.90
          message_safety_grounding:
            min_grounding: 0.95
            max_unverifiable_claims: 0
            require_coppa_safe: true
          geo_tracking:
            min_samples_per_prompt: 5
            report_variance: true
        cost_caps:
          anthropic_per_run_usd: 5.00
          media_gen_per_run_usd: 0.00
        latency_budget_ms:
          ai_proposal: 8000
        geo:
          prompt_set_size: 30
          cadence: weekly
          baseline_coverage: 0.0
        """
    )
    broken_path = tmp_path / "params.yaml"
    broken_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        load_params(broken_path)
    assert "w_recoverability" in str(excinfo.value)


def test_params_loads_s6_blocks() -> None:
    """S6 param blocks parse + validate from the committed example (INV-11).

    `creator_scoring` (FR-3.8), `kpi.levers` (FR-3.11), and `scheduler`
    (FR-3.6/OUT-2) are the single home for every S6 magic number; S6 consumers
    read them from here, never hardcoded. This asserts the three blocks load,
    the fit sub-weights partition to 1.0, every KPI channel lever is present at
    a 0.0 baseline, and dispatch is SIMULATED in v1 (INV-9). The drift guard
    proves a fit-weight set that does NOT sum to 1.0 fails to load.
    """
    params = load_params(EXAMPLE_PARAMS)

    # creator_scoring — fit + authenticity sub-weights, surface threshold (FR-3.8)
    fit = params.creator_scoring.fit
    assert fit.topic_match_weight == 0.5
    assert fit.audience_match_weight == 0.3
    assert fit.brand_alignment_weight == 0.2
    assert (
        fit.topic_match_weight + fit.audience_match_weight + fit.brand_alignment_weight
        == pytest.approx(1.0)
    )
    auth = params.creator_scoring.authenticity
    assert (
        auth.follower_authenticity_weight
        + auth.engagement_consistency_weight
        + auth.spam_signal_weight
        == pytest.approx(1.0)
    )
    assert params.creator_scoring.surface_threshold == 0.6

    # kpi.levers — 8 per-channel baseline/target pairs, all baseline 0.0 (FR-3.11)
    levers = params.kpi.levers
    expected_channels = {
        "instagram",
        "tiktok",
        "x",
        "linkedin",
        "email",
        "blog",
        "landing_page",
        "geo",
    }
    assert set(levers) == expected_channels
    for channel in expected_channels:
        assert levers[channel].baseline == 0.0
    assert levers["email"].target == 0.10

    # scheduler — dispatch is SIMULATED in v1 (FR-3.6 / OUT-2 / INV-9)
    assert params.scheduler.dispatch_mode == "simulated"


def test_params_loads_enrollment_contact_block() -> None:
    """S9 `enrollment.contact` recency windows parse from the example (INV-11).

    `grey_window_days` / `overdue_days` are the single home for the contact-status
    color thresholds (S9 W1); the recency deriver reads them from here, never
    hardcoded. This asserts the block loads with the committed values.
    """
    params = load_params(EXAMPLE_PARAMS)

    assert params.enrollment.contact.grey_window_days == 3
    assert params.enrollment.contact.overdue_days == 4


def test_enrollment_contact_missing_key_raises(tmp_path: Path) -> None:
    """A missing `enrollment.contact` key fails loudly — drift fails the build.

    Dropping `overdue_days` must raise a clear, typed `ValidationError` naming the
    offending field, never silently default it (CLAUDE.md §4.1, INV-11).
    """
    broken = textwrap.dedent(
        """\
        enrollment:
          contact:
            grey_window_days: 3
        """
    )
    broken_path = tmp_path / "contact.yaml"
    broken_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        ContactWindows.model_validate(
            yaml.safe_load(broken_path.read_text())["enrollment"]["contact"]
        )
    assert "overdue_days" in str(excinfo.value)


def test_enrollment_contact_wrong_type_raises() -> None:
    """A non-int window value fails validation (drift fails the build, INV-11)."""
    with pytest.raises(ValidationError) as excinfo:
        ContactWindows(grey_window_days="three", overdue_days=4)  # type: ignore[arg-type]
    assert "grey_window_days" in str(excinfo.value)


def test_message_safety_grounding_requires_min_brand_score() -> None:
    """`min_brand_score` is REQUIRED — dropping it fails to load (INV-11, §4.1).

    The V-4 brand-voice bar is its own canonical param, distinct from the V-2
    `min_grounding` floor. Constructing the model without it must raise so config
    drift fails the build, never silently defaults.
    """
    with pytest.raises(ValidationError) as excinfo:
        MessageSafetyGrounding(  # type: ignore[call-arg]
            min_grounding=0.95,
            max_unverifiable_claims=0,
            require_coppa_safe=True,
        )
    assert "min_brand_score" in str(excinfo.value)


def test_message_safety_grounding_min_brand_score_wrong_type_raises() -> None:
    """A non-numeric `min_brand_score` fails validation (drift fails the build)."""
    with pytest.raises(ValidationError) as excinfo:
        MessageSafetyGrounding(
            min_grounding=0.95,
            min_brand_score="high",  # type: ignore[arg-type]
            max_unverifiable_claims=0,
            require_coppa_safe=True,
        )
    assert "min_brand_score" in str(excinfo.value)


def test_creator_scoring_fit_weights_must_sum_to_one() -> None:
    """A fit-weight set that does not sum to 1.0 fails to load (INV-11, §4.1).

    Constructing the model directly with drifted weights must raise — this is
    how the params file stays honest: a consumer can trust the sub-weights are
    a true partition.
    """
    with pytest.raises(ValidationError):
        CreatorScoringFit(
            topic_match_weight=0.5,
            audience_match_weight=0.3,
            brand_alignment_weight=0.3,  # sums to 1.1 — drift
        )

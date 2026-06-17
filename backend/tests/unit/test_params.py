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
    ConversionWeights,
    CreatorScoringFit,
    MessageSafetyGrounding,
    PostedGallery,
    PostedGalleryEngagement,
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


def test_assignment_and_sis_blocks_load() -> None:
    """M0 `assignment:` + `sis:` blocks parse from the committed example (INV-11).

    The owner-authority assignment split (`closer_rank_max`, value/likelihood
    routing thresholds, deadline/unowned alarms, per-tier load cap) and the SIS
    reconcile bucket rules (`match_confidence_cutoff` + the three bucket-rule
    thresholds) are the single home for those tunables (MULTI_AGENT_COCKPIT.md §4,
    §2.2, §6); M2 (assignment) and M5 (SIS reconcile) read them from here, never a
    code literal. This asserts both blocks load with the committed typed values.
    """
    params = load_params(EXAMPLE_PARAMS)

    # assignment — closer tier = rank <= 1 (§2.2 demo), routing + alarm + load cap
    assert params.assignment.closer_rank_max == 1
    assert params.assignment.high_value_threshold == 12000.0
    assert params.assignment.high_likelihood_threshold == 0.6
    assert params.assignment.deadline_alarm_days == 14
    assert params.assignment.unowned_alarm_days == 3
    assert params.assignment.per_tier_load_cap == 40

    # sis — reconcile match cutoff + the three bucket-rule thresholds (§6)
    assert params.sis.match_confidence_cutoff == 0.9
    assert params.sis.confirmed_min_confidence == 0.9
    assert params.sis.paid_not_in_sis_max_confidence == 0.5
    assert params.sis.records_lag_days == 7


def test_assignment_block_missing_key_raises(tmp_path: Path) -> None:
    """A params doc missing the `assignment:` block fails loudly — drift fails the build.

    The loader is `extra=forbid` and the block is REQUIRED on the root `Params`
    model, so a YAML that omits `assignment:` must raise a clear, typed
    `ValidationError` naming the field, never silently default it
    (CLAUDE.md §4.1, INV-11). Mirrors `test_enrollment_contact_missing_key_raises`.
    """
    broken = textwrap.dedent(
        """\
        sis:
          match_confidence_cutoff: 0.9
          confirmed_min_confidence: 0.9
          paid_not_in_sis_max_confidence: 0.5
          records_lag_days: 7
        """
    )
    broken_path = tmp_path / "params.yaml"
    broken_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        load_params(broken_path)
    assert "assignment" in str(excinfo.value)


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


def test_params_loads_posted_gallery_block() -> None:
    """The `posted_gallery` block parses from the committed example (FR-3.4; INV-11).

    The posted-content gallery's synthetic per-post value band (`value_min` /
    `value_max`) and the deterministic-posted_at window (`posted_within_days`) are
    the single home for those tunables; the gallery reads them from here, never a
    code literal. This asserts the committed band/window load.
    """
    params = load_params(EXAMPLE_PARAMS)

    assert params.posted_gallery.value_min == 1.0
    assert params.posted_gallery.value_max == 100.0
    assert params.posted_gallery.posted_within_days == 365


def test_params_loads_posted_gallery_engagement_weights() -> None:
    """The `posted_gallery.engagement` weights parse from the committed example (INV-11).

    The REAL-catalog gallery path ranks posts by a real engagement composite
    (likes/views/comments), so the three weights live here — the single home for the
    catalog `value` formula, never a code literal. (Distinct from the synthetic
    `value_min`/`value_max`/`posted_within_days` band above, which the library-fallback
    path keeps using.)
    """
    params = load_params(EXAMPLE_PARAMS)

    assert params.posted_gallery.engagement.like_weight == 1.0
    assert params.posted_gallery.engagement.view_weight == 0.1
    assert params.posted_gallery.engagement.comment_weight == 3.0


def test_posted_gallery_engagement_weights_must_be_non_negative() -> None:
    """A negative engagement weight fails to load (drift fails the build, INV-11)."""
    with pytest.raises(ValidationError):
        PostedGalleryEngagement(like_weight=-1.0, view_weight=0.1, comment_weight=3.0)


def test_posted_gallery_band_must_be_ordered() -> None:
    """A value band whose max <= min fails to load (drift fails the build, INV-11)."""
    with pytest.raises(ValidationError):
        PostedGallery(value_min=100.0, value_max=1.0, posted_within_days=365)


def test_posted_gallery_window_must_be_positive() -> None:
    """A non-positive posted_within_days fails to load (drift fails the build, INV-11)."""
    with pytest.raises(ValidationError):
        PostedGallery(value_min=1.0, value_max=100.0, posted_within_days=0)


def test_params_loads_conversion_block() -> None:
    """The DH-1 `conversion:` block parses from the committed example (INV-11).

    The conversion-likelihood scorer's five dimension WEIGHTS, band cutoffs, the
    neighborhood→affluence table (+ its default), the income reference + neutral,
    and the child-count cap are the single home for that signal's tunables; the
    scorer reads them from here, never a code literal. This asserts the block
    loads, the committed values are present, and the five weights partition to 1.0.
    """
    params = load_params(EXAMPLE_PARAMS)
    conversion = params.conversion

    # Five dimension weights — MUST sum to 1.0 (the value term partition guard).
    w = conversion.weights
    assert (w.affluence, w.income, w.children, w.funding, w.depth) == (
        0.20,
        0.20,
        0.15,
        0.25,
        0.20,
    )
    assert w.affluence + w.income + w.children + w.funding + w.depth == pytest.approx(1.0)

    # Band cutoffs (High >= high_cutoff, Med >= med_cutoff, else Low).
    assert conversion.band_high_cutoff == 0.65
    assert conversion.band_med_cutoff == 0.40

    # Neighborhood→affluence table (aggregate area labels only — P-4/INV-6) + default.
    assert conversion.neighborhood_affluence["Highland Park"] == 0.95
    assert conversion.neighborhood_affluence["Riverside"] == 0.60
    assert conversion.neighborhood_affluence["Lakeview"] == 0.60
    assert conversion.neighborhood_affluence["Eastgate"] == 0.30
    assert conversion.neighborhood_affluence_default == 0.50

    # Income reference + the NEUTRAL value used when income is None.
    assert conversion.income_reference == 200_000
    assert conversion.income_neutral == 0.50

    # Child-count normalizer + the funding-type affinity table (+ default).
    assert conversion.num_children_cap == 5
    assert conversion.funding_affinity["tefa_standard"] == 0.90
    assert conversion.funding_affinity["tefa_disability"] == 0.90
    assert conversion.funding_affinity["tefa_homeschool"] == 0.70
    assert conversion.funding_affinity["self_pay"] == 0.50
    assert conversion.funding_affinity_default == 0.50


def test_conversion_weights_must_sum_to_one() -> None:
    """A conversion-weight set that does not sum to 1.0 fails to load (INV-11, §4.1).

    The five dimension weights MUST partition to 1.0 so the consumer can trust the
    conversion score stays in [0,1]; a drifted set raises at load.
    """
    with pytest.raises(ValidationError):
        ConversionWeights(
            affluence=0.20,
            income=0.20,
            children=0.15,
            funding=0.25,
            depth=0.30,  # sums to 1.10 — drift
        )


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

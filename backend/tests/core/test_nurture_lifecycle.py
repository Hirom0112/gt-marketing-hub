"""Pure-core tests for the Module-5 Nurture & Lifecycle derivations (app.core.nurture).

Covers the eight sub-view cores with INJECTED inputs (no clock, no I/O): tier mix,
the engagement×attribute heatmap, SLA compliance, pipeline distribution, handoff
metrics, SMS theme tagging (keyword v1 + LLM degrade), sequence health, and the
segment builder. Every figure is asserted against a hand-computed expectation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core import nurture as n

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_TIERS = ["clicked", "opened", "cold"]


def test_tier_mix_reachability_computed() -> None:
    mix = n.tier_mix(clicked=6, opened=3, cold=1)
    assert mix.total == 10
    assert mix.reachable == 9
    assert mix.reachability_pct == 90


def test_tier_mix_empty_is_zero_not_div_by_zero() -> None:
    mix = n.tier_mix(0, 0, 0)
    assert mix.total == 0
    assert mix.reachability_pct == 0


def test_engagement_attribute_heatmap_bakes_conversion_from_data() -> None:
    families = [
        n.HeatmapFamily("clicked", {"income": "gt_160k"}, converted=True),
        n.HeatmapFamily("clicked", {"income": "gt_160k"}, converted=True),
        n.HeatmapFamily("clicked", {"income": "lt_65k"}, converted=False),
        n.HeatmapFamily("cold", {"income": "gt_160k"}, converted=False),
    ]
    matrix = n.engagement_attribute_heatmap(families, tiers=_TIERS, dimensions=["income"])
    cells = {(c.engagement_tier, c.attribute_value): c for c in matrix["income"]}
    # clicked × gt_160k: 2 of 2 converted ⇒ 100%.
    assert cells[("clicked", "gt_160k")].conversion_pct == 100
    # clicked × lt_65k: 0 of 1 ⇒ 0%.
    assert cells[("clicked", "lt_65k")].conversion_pct == 0
    # cold × gt_160k: 0 of 1 ⇒ 0%. The grid is full (every tier × observed value).
    assert cells[("cold", "gt_160k")].conversion_pct == 0
    # opened row exists for every observed value even with no families (empty cell).
    assert cells[("opened", "gt_160k")].total == 0


def test_sla_compliance_window_late_and_per_owner() -> None:
    contacts = [
        # contacted within 24h ⇒ in window.
        n.SlaContactView("A", _NOW - timedelta(hours=10), _NOW - timedelta(hours=8), "rep_a"),
        # contacted after 30h ⇒ late.
        n.SlaContactView("B", _NOW - timedelta(hours=40), _NOW - timedelta(hours=10), "rep_a"),
        # uncontacted, window elapsed ⇒ late.
        n.SlaContactView("C", _NOW - timedelta(hours=40), None, "rep_b"),
        # uncontacted, still inside window ⇒ pending (not late, not compliant).
        n.SlaContactView("D", _NOW - timedelta(hours=2), None, "rep_b"),
    ]
    out = n.sla_compliance(contacts, now=_NOW, window_hours=24)
    assert out.total == 4
    assert out.in_window == 1
    assert out.pending == 1
    assert out.compliance_pct == 25  # 1 of 4
    assert {i.applicant_label for i in out.late} == {"B", "C"}
    by_owner = {o.owner: o for o in out.per_owner}
    assert by_owner["rep_a"].total == 2
    assert by_owner["rep_a"].in_window == 1
    assert by_owner["rep_a"].compliance_pct == 50


def test_pipeline_distribution_velocity_and_stuck() -> None:
    stages = [
        n.PipelineStageView("interest", count=5, stuck=1),
        n.PipelineStageView("apply", count=3, stuck=2),
        n.PipelineStageView("enroll", count=2, stuck=0),
    ]
    dist = n.pipeline_distribution(stages, stage_order=["interest", "apply", "enroll", "tuition"])
    assert dist.total == 10
    assert dist.stuck_total == 3
    # velocity = beyond the first stage (5) / total (10) = 50%.
    assert dist.velocity_pct == 50
    rows = {s.stage: s for s in dist.stages}
    assert rows["interest"].pct == 50
    # tuition is in the stage_order but absent from input ⇒ a zero row.
    assert rows["tuition"].count == 0


def test_handoff_metrics_cumulative_and_conversion() -> None:
    stages = [
        n.PipelineStageView("interest", 5, 0),
        n.PipelineStageView("enroll", 3, 0),
        n.PipelineStageView("tuition", 2, 0),
    ]
    h = n.handoff_metrics(
        stages, handoff_stages=["enroll", "tuition"], handoff_week=1, handoff_month=4
    )
    assert h.cumulative == 5  # 3 + 2
    assert h.total_deals == 10
    assert h.conversion_pct == 50
    assert h.weekly == 1
    assert h.monthly == 4


def test_sms_theme_tag_keyword_v1() -> None:
    rules = {"tuition": ["cost", "price"], "ready": ["enroll", "sign up"]}
    tags, mode = n.sms_theme_tag("What is the cost to enroll?", keyword_rules=rules)
    assert mode == "keyword"
    assert tags == ["ready", "tuition"]  # sorted


def test_sms_theme_tag_llm_layer_used_when_it_succeeds() -> None:
    rules = {"tuition": ["cost"]}
    tags, mode = n.sms_theme_tag(
        "hello", keyword_rules=rules, llm_tagger=lambda _m: ["funding", "scheduling"]
    )
    assert mode == "llm"
    assert tags == ["funding", "scheduling"]


def test_sms_theme_tag_degrades_to_keyword_on_llm_failure() -> None:
    rules = {"tuition": ["cost"]}

    def _boom(_m: str) -> list[str]:
        raise RuntimeError("llm down")

    tags, mode = n.sms_theme_tag("the cost is high", keyword_rules=rules, llm_tagger=_boom)
    assert mode == "keyword"
    assert tags == ["tuition"]


def test_sequence_health_flags_below_floor() -> None:
    healthy = [n.SequenceStepView(1, 60.0, 20.0), n.SequenceStepView(2, 50.0, 15.0)]
    assert n.sequence_health(healthy, min_open_pct=35.0, min_click_pct=8.0) is False
    # click avg below the floor ⇒ unhealthy.
    low_click = [n.SequenceStepView(1, 60.0, 4.0), n.SequenceStepView(2, 50.0, 5.0)]
    assert n.sequence_health(low_click, min_open_pct=35.0, min_click_pct=8.0) is True
    # no steps ⇒ unhealthy.
    assert n.sequence_health([], min_open_pct=35.0, min_click_pct=8.0) is True


def test_segment_builder_counts_matching_population() -> None:
    pop = [
        n.SegmentCandidate("clicked", {"income": "gt_160k", "region": "TX"}),
        n.SegmentCandidate("clicked", {"income": "lt_65k", "region": "TX"}),
        n.SegmentCandidate("opened", {"income": "gt_160k", "region": "CA"}),
        n.SegmentCandidate("cold", {"income": "gt_160k", "region": "TX"}),
    ]
    # No filter ⇒ everyone.
    assert n.segment_builder(pop) == 4
    # clicked only.
    assert n.segment_builder(pop, engagement_tiers=["clicked"]) == 2
    # clicked AND gt_160k.
    assert (
        n.segment_builder(
            pop, engagement_tiers=["clicked"], attribute_filters={"income": ["gt_160k"]}
        )
        == 1
    )
    # gt_160k in TX, any tier.
    assert n.segment_builder(pop, attribute_filters={"income": ["gt_160k"], "region": ["TX"]}) == 2

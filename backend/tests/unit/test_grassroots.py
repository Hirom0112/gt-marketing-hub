"""Unit tests for the pure Grassroots-Engine core (Module 2; app.core.grassroots).

Covers the five deterministic derivations: pipeline_counts (zero-filled, ordered),
goal_progress (value/target/pct, clamp at 100, no fake delta), market_map_summary
(contacted/total/coverage), sprint_health (on_pace AND behind via an INJECTED as_of,
plus the closed/degenerate edges), and attribute_enrollments (the documented
sum-of-conversions stand-in). Pure functions — no app/store/clock dependency.
"""

from __future__ import annotations

from datetime import date

from app.core.grassroots import (
    PIPELINE_STAGES,
    SPRINT_BEHIND,
    SPRINT_CLOSED,
    SPRINT_ON_PACE,
    AmbassadorView,
    NodeView,
    SprintView,
    attribute_enrollments,
    goal_progress,
    market_map_summary,
    pipeline_counts,
    sprint_health,
)


def _amb(status: str, intros: int = 0, p2p: int = 0) -> AmbassadorView:
    return AmbassadorView(status=status, intros=intros, p2p_calls=p2p)


# ------------------------------------------------------------------- pipeline_counts
def test_pipeline_counts_zero_filled_and_ordered() -> None:
    """Every stage is present (zero-filled) in pipeline order; counts are correct."""
    ambassadors = [
        _amb("prospect"),
        _amb("active"),
        _amb("active"),
        _amb("champion"),
    ]
    counts = pipeline_counts(ambassadors)
    assert list(counts.keys()) == list(PIPELINE_STAGES)
    assert counts == {
        "prospect": 1,
        "outreached": 0,
        "onboarded": 0,
        "active": 2,
        "champion": 1,
    }


def test_pipeline_counts_ignores_unknown_status() -> None:
    """An unknown status is ignored (the migration CHECK keeps the column to the set)."""
    counts = pipeline_counts([_amb("active"), _amb("bogus")])
    assert counts["active"] == 1
    assert sum(counts.values()) == 1


# --------------------------------------------------------------------- goal_progress
def test_goal_progress_values_targets_and_pct() -> None:
    """The four bars carry the real value, the injected target, and the integer pct."""
    ambassadors = [
        _amb("active", intros=10, p2p=3),
        _amb("champion", intros=5, p2p=2),
        _amb("onboarded", intros=1, p2p=0),
        _amb("prospect"),
    ]
    bars = goal_progress(
        ambassadors,
        influenced_enrollments=6,
        target_active_ambassadors=4,
        target_warm_intros=32,
        target_p2p_calls=10,
        target_influenced_enrollments=30,
    )
    # active + champion = 2 of 4 → 50%.
    assert bars["active_ambassadors"].value == 2
    assert bars["active_ambassadors"].target == 4
    assert bars["active_ambassadors"].pct == 50
    # warm intros 16 of 32 → 50%.
    assert bars["warm_intros"].value == 16
    assert bars["warm_intros"].pct == 50
    # p2p 5 of 10 → 50%.
    assert bars["p2p_calls"].value == 5
    assert bars["p2p_calls"].pct == 50
    # influenced 6 of 30 → 20%.
    assert bars["influenced_enrollments"].value == 6
    assert bars["influenced_enrollments"].pct == 20


def test_goal_progress_pct_clamped_to_100() -> None:
    """A value over target reads 100% (the bar never overflows) — no fake delta."""
    bars = goal_progress(
        [_amb("champion", intros=500, p2p=0)],
        influenced_enrollments=0,
        target_active_ambassadors=1,
        target_warm_intros=10,
        target_p2p_calls=1,
        target_influenced_enrollments=1,
    )
    assert bars["warm_intros"].value == 500
    assert bars["warm_intros"].pct == 100


# ----------------------------------------------------------------- market_map_summary
def test_market_map_summary_coverage() -> None:
    """Per-category total/contacted/leads/coverage; contacted = left the COLD state."""
    nodes = [
        NodeView(category="Parent groups", status="active", leads_generated=5),
        NodeView(category="Parent groups", status="cold", leads_generated=0),
        NodeView(category="Chess clubs", status="outreach", leads_generated=2),
    ]
    summary = {c.category: c for c in market_map_summary(nodes)}
    pg = summary["Parent groups"]
    assert pg.total == 2
    assert pg.contacted == 1  # the cold node is NOT contacted
    assert pg.leads == 5
    assert pg.coverage_pct == 50
    cc = summary["Chess clubs"]
    assert cc.total == 1
    assert cc.contacted == 1
    assert cc.coverage_pct == 100


def test_market_map_summary_preserves_first_seen_order() -> None:
    """Categories appear in first-seen (deterministic) order."""
    nodes = [
        NodeView(category="Z", status="cold", leads_generated=0),
        NodeView(category="A", status="cold", leads_generated=0),
    ]
    assert [c.category for c in market_map_summary(nodes)] == ["Z", "A"]


# --------------------------------------------------------------------- sprint_health
def _sprint(
    start: date, end: date, identified: int, conv: int, status: str = "active"
) -> SprintView:
    return SprintView(
        window_start=start,
        window_end=end,
        families_identified=identified,
        conversions=conv,
        status=status,
    )


def test_sprint_health_on_pace() -> None:
    """At half-elapsed with conversions above the pace band → on_pace (injected as_of)."""
    sprint = _sprint(date(2026, 6, 1), date(2026, 6, 29), identified=20, conv=12)
    # as_of half-way: elapsed 14/28 = 0.5 → expected 10; 12 >= 0.8*10=8 → on_pace.
    assert sprint_health(sprint, as_of=date(2026, 6, 15), behind_pace_frac=0.8) == SPRINT_ON_PACE


def test_sprint_health_behind() -> None:
    """At three-quarters elapsed with lagging conversions → behind (injected as_of)."""
    sprint = _sprint(date(2026, 6, 1), date(2026, 6, 29), identified=18, conv=10)
    # elapsed 21/28 = 0.75 → expected 13.5; 10 < 0.8*13.5=10.8 → behind.
    assert sprint_health(sprint, as_of=date(2026, 6, 22), behind_pace_frac=0.8) == SPRINT_BEHIND


def test_sprint_health_closed_short_circuits() -> None:
    """A CLOSED sprint reads closed regardless of pace."""
    sprint = _sprint(date(2026, 6, 1), date(2026, 6, 29), identified=20, conv=0, status="closed")
    assert sprint_health(sprint, as_of=date(2026, 6, 29), behind_pace_frac=0.8) == SPRINT_CLOSED


def test_sprint_health_before_window_is_on_pace() -> None:
    """An as_of before the window (nothing expected yet) → on_pace (never div-by-0)."""
    sprint = _sprint(date(2026, 6, 10), date(2026, 6, 30), identified=20, conv=0)
    assert sprint_health(sprint, as_of=date(2026, 6, 1), behind_pace_frac=0.8) == SPRINT_ON_PACE


def test_sprint_health_degenerate_window() -> None:
    """A zero-length window is treated as fully elapsed (the whole goal is due)."""
    sprint = _sprint(date(2026, 6, 1), date(2026, 6, 1), identified=10, conv=2)
    # expected = 10*1.0 = 10; 2 < 8 → behind.
    assert sprint_health(sprint, as_of=date(2026, 6, 1), behind_pace_frac=0.8) == SPRINT_BEHIND


# ------------------------------------------------------------- attribute_enrollments
def test_attribute_enrollments_sums_conversions() -> None:
    """The influenced-enrollment stand-in is the sum of sprint conversions."""
    sprints = [
        _sprint(date(2026, 6, 1), date(2026, 6, 29), identified=20, conv=12),
        _sprint(date(2026, 6, 1), date(2026, 6, 29), identified=18, conv=10),
    ]
    assert attribute_enrollments(sprints) == 22


def test_attribute_enrollments_empty_is_zero() -> None:
    """No sprints → zero influenced enrollments (never a div-by-0 downstream)."""
    assert attribute_enrollments([]) == 0

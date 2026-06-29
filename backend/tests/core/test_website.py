"""Pure-core tests for Module 13 website derivations (app.core.website).

Each public core function is covered with the simulated GA4 snapshot (its aggregate models
satisfy the core Protocols structurally) — no I/O, no clock. The UTM validation reuses the
real ``check_utm`` rule set over the real params (the CRM-Ops cross-link), so the broken
count is computed, never asserted by hand-wave.
"""

from __future__ import annotations

from app.adapters.analytics.base import AnalyticsWindow
from app.adapters.analytics.simulated import SimulatedAnalyticsAdapter
from app.core import website as core
from app.core.params import load_params

_SNAP = SimulatedAnalyticsAdapter().snapshot(AnalyticsWindow(start="2026-06-08", end="2026-06-15"))
_PARAMS = load_params()


def test_site_rollup_sums_and_weighted_means() -> None:
    roll = core.site_rollup(_SNAP.sites)
    assert roll["total_sessions"] == 11530
    assert roll["total_pageviews"] == 28570
    assert roll["new_pct"] == 73
    assert roll["returning_pct"] == 27
    # Session-weighted bounce (a small high-bounce site cannot drag the blend).
    assert roll["avg_bounce_rate"] == 0.4551
    assert roll["avg_session_duration_s"] == 126.7


def test_top_landing_pages_ranks_by_pageviews() -> None:
    top = core.top_landing_pages(_SNAP.pages, n=5)
    assert [p.pageviews for p in top] == sorted([p.pageviews for p in top], reverse=True)
    assert top[0].page_path == "/" and top[0].pageviews == 5200
    assert len(top) == 5


def test_page_trend_pct_signed() -> None:
    page = next(p for p in _SNAP.pages if p.page_path == "/tuition" and p.site == "gt.school")
    assert core.page_trend_pct(page) == round(100 * (3100 - 2600) / 2600)


def test_refresh_candidates_clear_threshold() -> None:
    cands = core.refresh_candidates(_SNAP.pages, bounce_warn_pct=0.60)
    paths = {p.page_path for p in cands}
    assert "/blog/2-hour-learning" in paths  # 0.62
    assert "/online-program" in paths  # 0.66
    assert "/tuition" not in paths  # 0.34
    # Sorted worst-bounce first.
    assert cands[0].bounce_rate == max(p.bounce_rate for p in cands)


def test_traffic_breakdown_merges_social_and_splits_platforms() -> None:
    bd = core.traffic_breakdown(_SNAP.sources)
    assert bd["total_sessions"] == 11530
    channels = {c["channel"]: c for c in bd["channels"]}
    assert channels["social"]["sessions"] == 2180  # x + facebook + instagram
    assert channels["organic"]["sessions"] == 4900
    assert [c["channel"] for c in bd["channels"]][0] == "organic"  # sorted desc
    platforms = {p["platform"] for p in bd["social_platforms"]}
    assert platforms == {"x", "facebook", "instagram"}


def test_validate_campaign_utms_flags_three_broken() -> None:
    v = core.validate_campaign_utms(_SNAP.campaigns, params=_PARAMS)
    assert v["total"] == 6
    assert v["broken_count"] == 3
    assert v["healthy"] == 3
    assert v["health_pct"] == 50
    # Each broken row carries offending keys + human reasons (the CRM-Ops drill-in feed).
    assert all(b["offending_keys"] and b["reasons"] for b in v["broken_campaigns"])


def test_download_summary_wow_delta() -> None:
    s = core.download_summary(_SNAP.downloads)
    assert s["total_weekly"] == 523
    assert s["total_cumulative"] == 5800
    assert s["wow_delta_pct"] == round(100 * (523 - 449) / 449)


def test_key_conversion_pages_ranked_by_rate() -> None:
    pages = core.key_conversion_pages(_SNAP.conversion_pages, n=5)
    rates = [core.conversion_page_rate(p) for p in pages]
    assert rates == sorted(rates, reverse=True)
    assert pages[0].page_path == "/apply" and pages[0].site == "gt.school"


def test_funnel_dropoff_of_top_and_drop_from_prev() -> None:
    rows = core.funnel_dropoff(_SNAP.funnel)
    assert rows[0]["of_top_pct"] == 100
    assert rows[0]["drop_from_prev_pct"] == 0
    assert rows[-1]["stage"] == "apply_submit"
    # Each later stage loses some share vs the prior.
    assert all(r["drop_from_prev_pct"] >= 0 for r in rows)
    assert rows[1]["drop_from_prev_pct"] == round(100 * (11530 - 4200) / 11530)


def test_empty_inputs_never_divide_by_zero() -> None:
    assert core.site_rollup([])["total_sessions"] == 0
    assert core.traffic_breakdown([])["total_sessions"] == 0
    assert core.download_summary([])["wow_delta_pct"] == 0
    assert core.funnel_dropoff([]) == []

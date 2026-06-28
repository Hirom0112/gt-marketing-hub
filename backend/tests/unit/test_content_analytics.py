"""Pure Content-analytics core tests (Module 3) — the deterministic derivations.

Covers the four pure functions in :mod:`app.core.content_analytics`:

- ``overview_rollup`` — productions in flight + on-track, this-week publish, top piece,
  and the X conversion rate (computed from reach/clicks, with the param fallback).
- ``detect_calendar_conflicts`` — the threshold-from-params conflict days (both the
  conflict and the no-conflict case).
- ``channel_breakdown`` — rates COMPUTED (not faked), X top / Facebook bottom flagged.
- ``piece_rankings`` — top/bottom by conversions + the UTM-attributable filter.
"""

from __future__ import annotations

from datetime import date

from app.core.content_analytics import (
    CalendarEntryView,
    ChannelMetricView,
    PiecePerfView,
    channel_breakdown,
    detect_calendar_conflicts,
    overview_rollup,
    piece_rankings,
)

_D = date  # brevity


def _cal(day: date, channel: str, status: str) -> CalendarEntryView:
    return CalendarEntryView(scheduled_date=day, channel=channel, status=status)


# ------------------------------------------------------------------ conflicts
def test_detect_calendar_conflicts_threshold_flags_overbooked_day() -> None:
    """A day with >= threshold entries is flagged; below it is not (threshold from params)."""
    entries = [
        _cal(_D(2026, 6, 15), "x", "planned"),
        _cal(_D(2026, 6, 15), "substack", "planned"),
        _cal(_D(2026, 6, 15), "email", "planned"),
        _cal(_D(2026, 6, 16), "x", "planned"),
        _cal(_D(2026, 6, 17), "x", "planned"),
        _cal(_D(2026, 6, 17), "substack", "planned"),
        _cal(_D(2026, 6, 17), "email", "planned"),
        _cal(_D(2026, 6, 17), "instagram", "planned"),
    ]
    # threshold 4 ⇒ only 06-17 (4 entries) is a conflict; 06-15 (3) is not.
    assert detect_calendar_conflicts(entries, threshold=4) == [_D(2026, 6, 17)]
    # threshold 3 ⇒ both 06-15 and 06-17 (sorted ascending).
    assert detect_calendar_conflicts(entries, threshold=3) == [_D(2026, 6, 15), _D(2026, 6, 17)]


def test_detect_calendar_conflicts_no_conflict() -> None:
    """No day reaches the threshold ⇒ empty list."""
    entries = [
        _cal(_D(2026, 6, 15), "x", "planned"),
        _cal(_D(2026, 6, 16), "x", "planned"),
    ]
    assert detect_calendar_conflicts(entries, threshold=4) == []


# --------------------------------------------------------------- channel breakdown
def test_channel_breakdown_rates_computed_x_top_facebook_bottom() -> None:
    """Conversion rates are computed (conversions/clicks); X is top, Facebook bottom."""
    metrics = [
        ChannelMetricView("substack", reach=8200, clicks=540, conversions=70, source_kind="manual"),
        ChannelMetricView("x", reach=15400, clicks=500, conversions=210, source_kind="stood_in"),
        ChannelMetricView(
            "facebook", reach=6300, clicks=280, conversions=8, source_kind="stood_in"
        ),
    ]
    rows = channel_breakdown(metrics)
    by_channel = {r.channel: r for r in rows}
    assert by_channel["x"].conversion_rate_pct == 42  # 210/500 = 42% (the engine)
    assert by_channel["substack"].conversion_rate_pct == 13  # 70/540 ≈ 13%
    assert by_channel["facebook"].conversion_rate_pct == 3  # 8/280 ≈ 3%
    assert by_channel["x"].is_top is True
    assert by_channel["facebook"].is_bottom is True
    assert by_channel["x"].is_bottom is False
    assert by_channel["facebook"].is_top is False


def test_channel_breakdown_zero_clicks_is_zero_rate() -> None:
    """A channel with no clicks reads 0% (never a div-by-zero)."""
    rows = channel_breakdown(
        [ChannelMetricView("podcast", reach=100, clicks=0, conversions=0, source_kind="manual")]
    )
    assert rows[0].conversion_rate_pct == 0


# ----------------------------------------------------------------- piece rankings
def test_piece_rankings_top_bottom_and_utm_filter() -> None:
    """Top/bottom by conversions; content-to-conversion lists ONLY UTM-attributed pieces."""
    pieces = [
        PiecePerfView("A", "x", reach=1, clicks=500, conversions=210, utm_attributed=True),
        PiecePerfView("B", "x", reach=1, clicks=420, conversions=95, utm_attributed=False),
        PiecePerfView("C", "instagram", reach=1, clicks=360, conversions=40, utm_attributed=True),
        PiecePerfView("D", "facebook", reach=1, clicks=280, conversions=8, utm_attributed=False),
    ]
    ranked = piece_rankings(pieces, top_n=2, bottom_n=2)
    assert [r.piece_title for r in ranked.top] == ["A", "B"]  # highest conversions
    assert [r.piece_title for r in ranked.bottom] == ["D", "C"]  # lowest, ascending
    # content-to-conversion = only the UTM-attributable pieces (highest first).
    assert [r.piece_title for r in ranked.content_to_conversion] == ["A", "C"]
    assert ranked.unattributable_count == 2  # B and D are not UTM-attributable


def test_piece_rankings_empty() -> None:
    """No pieces ⇒ empty rankings + zero unattributable."""
    ranked = piece_rankings([], top_n=3, bottom_n=3)
    assert ranked.top == []
    assert ranked.bottom == []
    assert ranked.content_to_conversion == []
    assert ranked.unattributable_count == 0


# -------------------------------------------------------------------- overview
def _overview_inputs() -> tuple[
    list[CalendarEntryView], list[ChannelMetricView], list[PiecePerfView]
]:
    calendar = [
        # conflict day (4 in-flight entries on 06-15)
        _cal(_D(2026, 6, 15), "x", "scheduled"),
        _cal(_D(2026, 6, 15), "substack", "scheduled"),
        _cal(_D(2026, 6, 15), "email", "planned"),
        _cal(_D(2026, 6, 15), "instagram", "planned"),
        # two non-conflict in-flight entries on 06-16
        _cal(_D(2026, 6, 16), "x", "planned"),
        _cal(_D(2026, 6, 16), "podcast", "planned"),
        # a published (NOT in flight) entry, outside the week window
        _cal(_D(2026, 6, 10), "x", "published"),
    ]
    metrics = [
        ChannelMetricView("x", reach=15400, clicks=500, conversions=210, source_kind="stood_in"),
        ChannelMetricView(
            "facebook", reach=6300, clicks=280, conversions=8, source_kind="stood_in"
        ),
    ]
    pieces = [
        PiecePerfView("Top piece", "x", reach=1, clicks=500, conversions=210, utm_attributed=True),
        PiecePerfView(
            "Low piece", "facebook", reach=1, clicks=280, conversions=8, utm_attributed=False
        ),
    ]
    return calendar, metrics, pieces


def test_overview_rollup_hero_figures() -> None:
    """The 3a rollup: in-flight + on-track, this-week, top piece, computed X rate."""
    calendar, metrics, pieces = _overview_inputs()
    rollup = overview_rollup(
        calendar,
        metrics,
        pieces,
        library_count=5,
        testimonial_stub_count=2,
        conflict_threshold=4,
        this_week_start=_D(2026, 6, 15),
        this_week_end=_D(2026, 6, 21),
        x_channel="x",
        x_conversion_rate_fallback=0.42,
    )
    assert rollup.productions_in_flight == 6  # published excluded
    assert rollup.on_track == 2  # the 06-16 pair (06-15 is a conflict day)
    assert rollup.on_track_pct == 33  # round(100 * 2 / 6)
    assert rollup.this_week_publish_count == 6  # entries in the 06-15..06-21 window
    assert rollup.top_piece_title == "Top piece"
    assert rollup.top_piece_conversions == 210
    assert rollup.x_conversion_rate_pct == 42  # computed 210/500
    assert rollup.library_count == 5
    assert rollup.testimonial_stub_count == 2
    assert {s.channel for s in rollup.channel_standins} == {"x", "facebook"}


def test_overview_rollup_x_rate_falls_back_to_param() -> None:
    """When X has no clicks to compute from, the param fallback rate is surfaced."""
    calendar, _metrics, pieces = _overview_inputs()
    metrics = [ChannelMetricView("x", reach=100, clicks=0, conversions=0, source_kind="stood_in")]
    rollup = overview_rollup(
        calendar,
        metrics,
        pieces,
        library_count=0,
        testimonial_stub_count=0,
        conflict_threshold=4,
        this_week_start=_D(2026, 6, 15),
        this_week_end=_D(2026, 6, 21),
        x_channel="x",
        x_conversion_rate_fallback=0.42,
    )
    assert rollup.x_conversion_rate_pct == 42  # round(100 * 0.42)

"""Pure-core tests for Module 9 admissions derivations (app.core.admissions).

Each public core function is covered with hand-built inputs (the store dataclasses satisfy
the core Protocols structurally) — no I/O, no clock (``now`` is injected). Aggregate-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core import admissions as core
from app.data.admissions_store import ContentBridge, FeedbackItem, Objection, VoiceQuote

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _obj(theme: str, week_count: int, trend: str = "stable") -> Objection:
    return Objection(
        objection_id=UUID(int=hash(theme) & 0xFFFF),
        theme=theme,
        week_count=week_count,
        cumulative_count=week_count * 4,
        trend=trend,
        source="form",
        example_quote="synthetic example",
        persona="synthetic persona",
        urgency="normal",
    )


def _quote(sentiment: str) -> VoiceQuote:
    return VoiceQuote(
        quote_id=UUID(int=0),
        quote="synthetic",
        sentiment=sentiment,
        theme="curriculum",
        source="form",
        is_quote_of_week=False,
        week_of=None,
    )


def _bridge(produced: bool, surfaced_day: int, published_day: int | None) -> ContentBridge:
    from datetime import timedelta

    return ContentBridge(
        bridge_id=UUID(int=abs(surfaced_day)),
        objection_theme="cost",
        brief_entry_id=None,
        produced=produced,
        surfaced_at=_NOW + timedelta(days=surfaced_day),
        published_at=(_NOW + timedelta(days=published_day)) if published_day is not None else None,
        freq_before=18,
        freq_after=14,
    )


def _feedback(created_day: int, actioned_day: int | None) -> FeedbackItem:
    from datetime import timedelta

    return FeedbackItem(
        item_id=UUID(int=created_day & 0xFFFF),
        summary="synthetic",
        category="messaging_gap",
        status="actioned" if actioned_day is not None else "open",
        actionable=True,
        owner="admissions",
        decision_id=None,
        created_at=_NOW + timedelta(days=created_day),
        actioned_at=(_NOW + timedelta(days=actioned_day)) if actioned_day is not None else None,
    )


def test_top_objections_by_frequency() -> None:
    objs = [_obj("cost", 14), _obj("accreditation", 11), _obj("social", 3), _obj("scheduling", 6)]
    top = core.top_objections(objs, n=3)
    assert [o.theme for o in top] == ["cost", "accreditation", "scheduling"]


def test_top_objections_respects_n() -> None:
    objs = [_obj("cost", 14), _obj("accreditation", 11)]
    assert len(core.top_objections(objs, n=1)) == 1
    assert core.top_objections(objs, n=0) == []


def test_objection_trend_per_theme() -> None:
    objs = [_obj("cost", 14, "up"), _obj("social", 3, "down"), _obj("curriculum", 4, "stable")]
    trend = core.objection_trend(objs)
    assert trend == {"cost": "up", "social": "down", "curriculum": "stable"}


def test_objection_to_resolution_time_avg_days() -> None:
    # cost: surfaced day-14 → published day-10 (4 days); accreditation: day-12 → day-6 (6 days).
    bridges = [_bridge(True, -14, -10), _bridge(True, -12, -6), _bridge(False, -3, None)]
    assert core.objection_to_resolution_time(bridges) == 5.0


def test_objection_to_resolution_time_none_published() -> None:
    assert core.objection_to_resolution_time([_bridge(False, -3, None)]) == 0.0


def test_bridge_hit_rate() -> None:
    bridges = [
        _bridge(True, -14, -10),
        _bridge(True, -12, -6),
        _bridge(False, -3, None),
        _bridge(False, -2, None),
    ]
    out = core.bridge_hit_rate(bridges)
    assert out["produced"] == 2
    assert out["total"] == 4
    assert out["hit_rate_pct"] == 50
    assert out["avg_resolution_days"] == 5.0


def test_bridge_hit_rate_empty() -> None:
    out = core.bridge_hit_rate([])
    assert out["hit_rate_pct"] == 0
    assert out["total"] == 0


def test_sentiment_ratio_over_quotes() -> None:
    quotes = [_quote("positive"), _quote("positive"), _quote("neutral"), _quote("negative")]
    out = core.sentiment_ratio(quotes)
    assert out["positive"] == 2
    assert out["neutral"] == 1
    assert out["negative"] == 1
    assert out["total"] == 4
    assert out["positive_pct"] == 50


def test_sentiment_ratio_over_summary() -> None:
    class _Summary:
        positive = 60
        neutral = 30
        negative = 10

    out = core.sentiment_ratio(_Summary())
    assert out["total"] == 100
    assert out["positive_pct"] == 60
    assert out["negative_pct"] == 10


def test_sentiment_ratio_empty_no_div_by_zero() -> None:
    out = core.sentiment_ratio([])
    assert out["total"] == 0
    assert out["positive_pct"] == 0


def test_feedback_closure_rate() -> None:
    # actioned within 7d: -10→-6 (4d, in), -8→-5 (3d, in); outside: -12→-2 (10d); open: -4→None.
    items = [_feedback(-10, -6), _feedback(-12, -2), _feedback(-8, -5), _feedback(-4, None)]
    out = core.feedback_closure_rate(items, now=_NOW, sla_days=7)
    assert out["total"] == 4
    assert out["actioned"] == 3
    assert out["within_sla"] == 2
    assert out["open_count"] == 1
    assert out["closure_rate_pct"] == round(100 * 2 / 3)


def test_feedback_closure_rate_none_actioned() -> None:
    out = core.feedback_closure_rate([_feedback(-4, None)], now=_NOW, sla_days=7)
    assert out["actioned"] == 0
    assert out["closure_rate_pct"] == 0

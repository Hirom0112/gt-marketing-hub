"""Pure Admissions & Voice-of-Customer derivations (Module 9; INV-2 / INV-6 / INV-11).

The deterministic core behind the Admissions listening post: given the objection log,
the voice-quote feed, the feedback items, and the objection→content bridges, compute

1. the TOP objections (the 9a/9b headline) by weekly frequency,
2. the per-theme objection TREND (rising / stable / falling) over the recent weeks,
3. the content-bridge HIT RATE (briefs produced / total) + the objection-to-resolution
   time (surfaced → published), so the 9c bridge figures are computed, never faked,
4. the family SENTIMENT ratio (positive / neutral / negative) over either the voice
   quotes OR an aggregate sentiment summary (INV-6: aggregate only), and
5. the feedback CLOSURE RATE — the share of actioned items closed within the params SLA.

This is the deterministic, *pure* core (mirrors :mod:`app.core.field_events` /
:mod:`app.core.content_analytics`): a function of its inputs + the injected params dials
alone — no repository, adapter (incl. the sentiment adapter), decision-queue, httpx, or
LLM import (the core-purity test guards this). The sentiment summary is read STRUCTURALLY
(a ``SentimentLike`` protocol) so the core never imports ``app.adapters``. Every
threshold/size is INJECTED from params (INV-11); nothing is a code literal except the
closed trend/sentiment wire-sets. Aggregate-only (INV-6): no child-keyed field enters
here, and every quote/example is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Closed wire-sets — named tokens, not tunables (the INV-11 carve-out, like
# field_events.STATUS_*). The 0042 CHECKs mirror these.
# ---------------------------------------------------------------------------
TREND_UP = "up"
TREND_STABLE = "stable"
TREND_DOWN = "down"
TRENDS: tuple[str, ...] = (TREND_UP, TREND_STABLE, TREND_DOWN)

SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEUTRAL = "neutral"
SENTIMENT_NEGATIVE = "negative"
SENTIMENTS: tuple[str, ...] = (SENTIMENT_POSITIVE, SENTIMENT_NEUTRAL, SENTIMENT_NEGATIVE)


# ---------------------------------------------------------------------------
# Structural shapes the core reads (source-agnostic). The store dataclasses satisfy
# these structurally, so the API passes rows straight in and the pure core never imports
# the store / adapter layer (the field_events ``*Like`` pattern).
# ---------------------------------------------------------------------------
class ObjectionLike(Protocol):
    """The shape the core reads off one objection row."""

    @property
    def theme(self) -> str: ...
    @property
    def week_count(self) -> int: ...
    @property
    def trend(self) -> str: ...


class VoiceQuoteLike(Protocol):
    """The shape the core reads off one voice-quote row."""

    @property
    def sentiment(self) -> str: ...


@runtime_checkable
class SentimentLike(Protocol):
    """An aggregate sentiment summary read structurally (no app.adapters import; INV-6)."""

    @property
    def positive(self) -> int: ...
    @property
    def neutral(self) -> int: ...
    @property
    def negative(self) -> int: ...


class BridgeLike(Protocol):
    """The shape the core reads off one objection→content bridge row."""

    @property
    def produced(self) -> bool: ...
    @property
    def surfaced_at(self) -> datetime: ...
    @property
    def published_at(self) -> datetime | None: ...


class FeedbackLike(Protocol):
    """The shape the core reads off one feedback item."""

    @property
    def created_at(self) -> datetime: ...
    @property
    def actioned_at(self) -> datetime | None: ...


def top_objections[O: ObjectionLike](objections: Iterable[O], *, n: int) -> list[O]:
    """The top ``n`` objections by weekly frequency (descending; first-seen tie-break).

    ``n`` is read from ``params.admissions.top_objections_n`` at the edge (INV-11). A
    stable sort over the input order keeps ties in first-seen order. Returns the SAME
    row objects so the API serializes them directly.
    """
    rows = list(objections)
    ranked = sorted(rows, key=lambda o: o.week_count, reverse=True)
    return ranked[: max(0, n)]


def objection_trend(objections: Iterable[ObjectionLike]) -> dict[str, str]:
    """The per-theme objection trend (theme → ``up`` / ``stable`` / ``down``).

    The honest direction the recent-weeks window carries on each themed row (the 9b
    trend column). When a theme appears more than once the last row wins (the latest
    snapshot). A falling objection is a GOOD thing (the brief landed).
    """
    out: dict[str, str] = {}
    for o in objections:
        trend = o.trend if o.trend in TRENDS else TREND_STABLE
        out[o.theme] = trend
    return out


def objection_to_resolution_time(bridges: Iterable[BridgeLike]) -> float:
    """Average objection→resolution time in DAYS over published bridges (0.0 when none).

    Measured ``surfaced_at`` → ``published_at`` for every bridge that has been published.
    A pending (unpublished) bridge has no resolution time yet and is excluded. Returns a
    rounded-to-one-decimal mean (the 9a "objection → resolution" headline).
    """
    spans = [
        (b.published_at - b.surfaced_at).total_seconds() / 86400.0
        for b in bridges
        if b.published_at is not None
    ]
    if not spans:
        return 0.0
    return round(sum(spans) / len(spans), 1)


def bridge_hit_rate(bridges: Iterable[BridgeLike]) -> dict[str, object]:
    """The content-bridge hit rate + objection-to-resolution time (9c).

    Keys:
    - ``produced`` / ``total`` — the produced-brief count and the bridge count.
    - ``hit_rate_pct`` — ``round(100 * produced / total)`` (0 when no bridges).
    - ``avg_resolution_days`` — :func:`objection_to_resolution_time` over the bridges.
    """
    rows = list(bridges)
    total = len(rows)
    produced = sum(1 for b in rows if b.produced)
    hit_rate_pct = round(100 * produced / total) if total else 0
    return {
        "produced": produced,
        "total": total,
        "hit_rate_pct": hit_rate_pct,
        "avg_resolution_days": objection_to_resolution_time(rows),
    }


def sentiment_ratio(source: Iterable[VoiceQuoteLike] | SentimentLike) -> dict[str, object]:
    """The positive / neutral / negative sentiment ratio over quotes OR a summary (INV-6).

    Accepts EITHER an iterable of voice quotes (counted by their ``sentiment`` label) OR
    an aggregate :class:`SentimentLike` summary (read structurally — no ``app.adapters``
    import, aggregate only). Returns counts + the share of each bucket (rounded percent)
    + the total. A non-positive total yields all-zero shares (never a div-by-0).
    """
    if isinstance(source, SentimentLike):  # an aggregate summary
        pos, neu, neg = source.positive, source.neutral, source.negative
    else:
        pos = neu = neg = 0
        for q in source:
            if q.sentiment == SENTIMENT_POSITIVE:
                pos += 1
            elif q.sentiment == SENTIMENT_NEGATIVE:
                neg += 1
            else:
                neu += 1
    total = pos + neu + neg

    def _pct(part: int) -> int:
        return round(100 * part / total) if total else 0

    return {
        "positive": pos,
        "neutral": neu,
        "negative": neg,
        "total": total,
        "positive_pct": _pct(pos),
        "neutral_pct": _pct(neu),
        "negative_pct": _pct(neg),
    }


def feedback_closure_rate(
    items: Sequence[FeedbackLike], *, now: datetime, sla_days: int
) -> dict[str, object]:
    """The feedback closure rate — share of ACTIONED items closed within the SLA (9e).

    ``now`` is INJECTED (the core reads no clock); ``sla_days`` is read from
    ``params.admissions.sla_closure_days`` at the edge (INV-11). An item counts as
    "closed in SLA" when it has an ``actioned_at`` and ``actioned_at - created_at <=
    sla_days``. The rate's denominator is the ACTIONED items (those with an
    ``actioned_at``) — an item still open has no closure time yet. Keys:

    - ``actioned`` — items with an ``actioned_at``.
    - ``within_sla`` — actioned items closed within ``sla_days``.
    - ``total`` — all feedback items.
    - ``open_count`` — items still awaiting action (no ``actioned_at``).
    - ``closure_rate_pct`` — ``round(100 * within_sla / actioned)`` (0 when none actioned).
    """
    total = len(items)
    actioned = [f for f in items if f.actioned_at is not None]
    within = sum(
        1
        for f in actioned
        if f.actioned_at is not None
        and (f.actioned_at - f.created_at).total_seconds() <= sla_days * 86400.0
    )
    closure_rate_pct = round(100 * within / len(actioned)) if actioned else 0
    return {
        "actioned": len(actioned),
        "within_sla": within,
        "total": total,
        "open_count": total - len(actioned),
        "closure_rate_pct": closure_rate_pct,
    }

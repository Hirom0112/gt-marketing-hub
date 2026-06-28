"""Pure Content & Thought-Leadership derivations (Module 3; INV-2 / INV-6 / INV-11).

The deterministic core behind the Content analytics surface: given the editorial
calendar, the per-channel metrics, and the per-piece performance rows, compute

1. the OVERVIEW rollup (the 3a hero figures): productions in flight + on-track ratio,
   this-week publish count, the top-performing piece, the X/Twitter conversion rate,
   and the per-channel subscriber/listen stand-ins,
2. the calendar CONFLICTS — the days carrying at least the params ``conflict_threshold``
   entries (the over-booked days the month grid flags),
3. the per-channel BREAKDOWN — reach/clicks/conversion-rate (rates COMPUTED, never
   faked) with the top + bottom channel flagged, and
4. the per-piece RANKINGS — top-N + bottom-N by conversions, plus the
   content-to-conversion list filtered to UTM-attributed pieces (the rest are honestly
   reported as unattributable — the broken-UTM reality stays visible).

This is the deterministic, *pure* core (mirrors :mod:`app.core.grassroots` /
:mod:`app.core.budget`): a function of its inputs + the injected params dials alone —
no repository, adapter, decision-queue, httpx, or LLM import (the core-purity test
guards this). Every threshold/size is INJECTED from params (INV-11); nothing is a code
literal. Aggregate-only (INV-6): channel/piece labels are aggregate and no child-keyed
field ever enters here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------------------
# Calendar statuses — the closed set (named wire tokens, not tunables; the INV-11
# carve-out, like grassroots.STAGE_*). The migration CHECK mirrors these. An entry is
# "in flight" until it is published.
# ---------------------------------------------------------------------------
STATUS_PLANNED = "planned"
STATUS_SCHEDULED = "scheduled"
STATUS_PUBLISHED = "published"
STATUS_DRAFT = "draft"

# The statuses that count as a production STILL IN FLIGHT (not yet published).
IN_FLIGHT_STATUSES: frozenset[str] = frozenset({STATUS_PLANNED, STATUS_SCHEDULED, STATUS_DRAFT})


# ---------------------------------------------------------------------------
# Core-local, source-agnostic views of the inputs. The store dataclasses are
# converted to these (or duck-typed against them) so the pure core never imports the
# store/adapter layer (the grassroots.AmbassadorView pattern).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendarEntryView:
    """One editorial-calendar slot as the core reads it (synthetic; INV-1).

    Attributes:
        scheduled_date: The day the piece is slotted on.
        channel: The publishing channel label (a ``content.channels`` token).
        status: The slot lifecycle (one of the ``STATUS_*`` tokens).
    """

    scheduled_date: date
    channel: str
    status: str


@dataclass(frozen=True, slots=True)
class ChannelMetricView:
    """One channel's period metrics as the core reads it.

    Attributes:
        channel: The channel label.
        reach: Reach (the subscriber/listen/impression stand-in).
        clicks: Clicks recorded for the period.
        conversions: Conversions recorded for the period.
        source_kind: The provenance label (``stood_in`` / ``manual`` / …) — the
            honesty badge a channel without a real adapter carries (INV-9).
    """

    channel: str
    reach: int
    clicks: int
    conversions: int
    source_kind: str


@dataclass(frozen=True, slots=True)
class PiecePerfView:
    """One piece's performance as the core reads it.

    Attributes:
        piece_title: The synthetic piece title (content, never PII; INV-1).
        channel: The channel the piece ran on.
        reach: Reach for the piece.
        clicks: Clicks for the piece.
        conversions: Conversions credited to the piece.
        utm_attributed: Whether the conversions are UTM-attributable (the honesty
            flag — an un-attributed piece is reported as unattributable).
    """

    piece_title: str
    channel: str
    reach: int
    clicks: int
    conversions: int
    utm_attributed: bool


# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelStandin:
    """One channel's reach stand-in for the overview (subscriber/listen stand-in)."""

    channel: str
    reach: int
    source_kind: str


@dataclass(frozen=True, slots=True)
class OverviewRollup:
    """The 3a hero figures — real measurements, no fabricated deltas (honesty mandate).

    Attributes:
        productions_in_flight: Calendar entries not yet published.
        on_track: In-flight entries NOT slotted on a same-day conflict day.
        on_track_pct: ``round(100 * on_track / productions_in_flight)`` (0 when none).
        this_week_publish_count: Entries slotted within the injected this-week window.
        top_piece_title: The piece with the most conversions (``None`` if none).
        top_piece_conversions: That piece's conversions (0 if none).
        x_conversion_rate_pct: The X/Twitter conversion rate as an integer percent —
            COMPUTED from the seeded reach/clicks (the "42% conversion engine"), or the
            injected param fallback when X has no clicks to compute from.
        channel_standins: Per-channel reach stand-ins (with provenance).
        library_count: Kept+validated library asset count (passed in).
        testimonial_stub_count: Recently-captured grassroots testimonial DRAFTs.
    """

    productions_in_flight: int
    on_track: int
    on_track_pct: int
    this_week_publish_count: int
    top_piece_title: str | None
    top_piece_conversions: int
    x_conversion_rate_pct: int
    channel_standins: list[ChannelStandin]
    library_count: int
    testimonial_stub_count: int


@dataclass(frozen=True, slots=True)
class ChannelBreakdown:
    """One channel's reach/clicks/conversion-rate row (rate COMPUTED, not faked).

    Attributes:
        channel: The channel label.
        reach: Reach for the period.
        clicks: Clicks for the period.
        conversions: Conversions for the period.
        conversion_rate_pct: ``round(100 * conversions / clicks)`` clamped to [0, 100]
            (0 when there were no clicks — never a div-by-zero).
        source_kind: The provenance/honesty label.
        is_top: Whether this is the highest-conversion-rate channel.
        is_bottom: Whether this is the lowest-conversion-rate channel.
    """

    channel: str
    reach: int
    clicks: int
    conversions: int
    conversion_rate_pct: int
    source_kind: str
    is_top: bool
    is_bottom: bool


@dataclass(frozen=True, slots=True)
class PieceRanking:
    """One ranked piece row (top/bottom/content-to-conversion)."""

    piece_title: str
    channel: str
    reach: int
    clicks: int
    conversions: int
    conversion_rate_pct: int
    utm_attributed: bool


@dataclass(frozen=True, slots=True)
class PieceRankings:
    """The per-piece ranking rollup.

    Attributes:
        top: The top-N pieces by conversions (desc).
        bottom: The bottom-N pieces by conversions (asc).
        content_to_conversion: The pieces whose conversions ARE UTM-attributable
            (``utm_attributed=True``), highest conversions first — the only honestly
            attributable conversions.
        unattributable_count: How many pieces are NOT UTM-attributable (the rest) —
            surfaced so the broken-UTM reality stays visible (never hidden).
    """

    top: list[PieceRanking]
    bottom: list[PieceRanking]
    content_to_conversion: list[PieceRanking]
    unattributable_count: int


def _rate_pct(conversions: int, clicks: int) -> int:
    """Integer conversion-rate percent of ``conversions`` over ``clicks``, clamped [0,100].

    Returns ``0`` for non-positive clicks (the rate is undefined — never a div-by-0).
    """
    if clicks <= 0:
        return 0
    return max(0, min(100, round(100 * conversions / clicks)))


def detect_calendar_conflicts(
    entries: Iterable[CalendarEntryView], *, threshold: int
) -> list[date]:
    """The days carrying at least ``threshold`` entries (the over-booked conflict days).

    A pure count-by-day: a day with ``>= threshold`` slots is a SCHEDULING CONFLICT
    (too many pieces competing for one day). ``threshold`` is INJECTED from
    ``params.content.calendar.conflict_threshold`` (INV-11) — never a code literal.
    Returns the conflict days sorted ascending (deterministic), or an empty list when
    none reach the threshold.
    """
    counts: dict[date, int] = {}
    for entry in entries:
        counts[entry.scheduled_date] = counts.get(entry.scheduled_date, 0) + 1
    return sorted(day for day, count in counts.items() if count >= threshold)


def channel_breakdown(metrics: Iterable[ChannelMetricView]) -> list[ChannelBreakdown]:
    """Per-channel reach/clicks/conversion-rate with the top + bottom channel flagged.

    The conversion rate is COMPUTED (``conversions / clicks``), never faked. The
    highest-rate channel is flagged ``is_top`` and the lowest ``is_bottom`` (the FIRST
    occurrence on a tie, deterministic over a stable input order). Input order is
    preserved in the output. An empty input yields an empty list.
    """
    rows = [
        ChannelBreakdown(
            channel=m.channel,
            reach=m.reach,
            clicks=m.clicks,
            conversions=m.conversions,
            conversion_rate_pct=_rate_pct(m.conversions, m.clicks),
            source_kind=m.source_kind,
            is_top=False,
            is_bottom=False,
        )
        for m in metrics
    ]
    if not rows:
        return rows
    top_idx = max(range(len(rows)), key=lambda i: rows[i].conversion_rate_pct)
    bottom_idx = min(range(len(rows)), key=lambda i: rows[i].conversion_rate_pct)
    result: list[ChannelBreakdown] = []
    for i, row in enumerate(rows):
        result.append(
            ChannelBreakdown(
                channel=row.channel,
                reach=row.reach,
                clicks=row.clicks,
                conversions=row.conversions,
                conversion_rate_pct=row.conversion_rate_pct,
                source_kind=row.source_kind,
                is_top=(i == top_idx),
                is_bottom=(i == bottom_idx),
            )
        )
    return result


def _to_ranking(piece: PiecePerfView) -> PieceRanking:
    """Project a :class:`PiecePerfView` onto a :class:`PieceRanking`."""
    return PieceRanking(
        piece_title=piece.piece_title,
        channel=piece.channel,
        reach=piece.reach,
        clicks=piece.clicks,
        conversions=piece.conversions,
        conversion_rate_pct=_rate_pct(piece.conversions, piece.clicks),
        utm_attributed=piece.utm_attributed,
    )


def piece_rankings(pieces: Sequence[PiecePerfView], *, top_n: int, bottom_n: int) -> PieceRankings:
    """Top-N + bottom-N pieces by conversions, plus the UTM-attributable subset.

    Pieces are ranked by ``conversions`` (the documented metric): the top-N are the
    highest, the bottom-N the lowest (a stable secondary sort on title keeps ties
    deterministic). ``content_to_conversion`` is the pieces whose conversions ARE
    UTM-attributable (highest first) — the only honestly attributable conversions; the
    rest are counted in ``unattributable_count`` so the broken-UTM reality stays
    visible. ``top_n``/``bottom_n`` are INJECTED from ``params.content.rankings``
    (INV-11). An empty input yields empty rankings.
    """
    ranked = sorted(
        (_to_ranking(p) for p in pieces),
        key=lambda r: (-r.conversions, r.piece_title),
    )
    top = ranked[:top_n]
    # bottom-N by conversions ascending; reverse a tail slice of the desc sort so a
    # bottom row keeps the same deterministic ordering shape.
    bottom = list(reversed(ranked[-bottom_n:])) if ranked else []
    attributed = [r for r in ranked if r.utm_attributed]
    return PieceRankings(
        top=top,
        bottom=bottom,
        content_to_conversion=attributed,
        unattributable_count=len(ranked) - len(attributed),
    )


def overview_rollup(
    calendar_entries: Sequence[CalendarEntryView],
    channel_metrics: Sequence[ChannelMetricView],
    piece_perf: Sequence[PiecePerfView],
    *,
    library_count: int,
    testimonial_stub_count: int,
    conflict_threshold: int,
    this_week_start: date,
    this_week_end: date,
    x_channel: str,
    x_conversion_rate_fallback: float,
) -> OverviewRollup:
    """The 3a hero rollup — pure, every dial INJECTED (INV-11), no fabricated deltas.

    * productions in flight = entries not yet published; on-track = those NOT slotted on
      a same-day conflict day (the conflict days derive from
      :func:`detect_calendar_conflicts` with the injected ``conflict_threshold``).
    * this-week publish count = entries slotted within ``[this_week_start,
      this_week_end]`` (the window is injected at the edge — the core reads no clock).
    * top piece = the most-converting piece (``None`` when there are none).
    * X conversion rate = the COMPUTED rate for ``x_channel`` (conversions/clicks); when
      X has no clicks to compute from, the injected param fallback is surfaced instead
      (so the "42% conversion engine" is a real number, derived not hardcoded).
    * channel stand-ins = each channel's reach + provenance (the subscriber/listen
      stand-ins), honestly labeled.
    """
    in_flight = [e for e in calendar_entries if e.status in IN_FLIGHT_STATUSES]
    conflict_days = set(detect_calendar_conflicts(calendar_entries, threshold=conflict_threshold))
    on_track = sum(1 for e in in_flight if e.scheduled_date not in conflict_days)
    on_track_pct = _pct(on_track, len(in_flight))

    this_week = sum(
        1 for e in calendar_entries if this_week_start <= e.scheduled_date <= this_week_end
    )

    top_piece_title: str | None = None
    top_piece_conversions = 0
    if piece_perf:
        top = max(piece_perf, key=lambda p: (p.conversions, p.piece_title))
        top_piece_title = top.piece_title
        top_piece_conversions = top.conversions

    x_metric = next((m for m in channel_metrics if m.channel == x_channel), None)
    if x_metric is not None and x_metric.clicks > 0:
        x_conversion_rate_pct = _rate_pct(x_metric.conversions, x_metric.clicks)
    else:
        x_conversion_rate_pct = max(0, min(100, round(100 * x_conversion_rate_fallback)))

    channel_standins = [
        ChannelStandin(channel=m.channel, reach=m.reach, source_kind=m.source_kind)
        for m in channel_metrics
    ]

    return OverviewRollup(
        productions_in_flight=len(in_flight),
        on_track=on_track,
        on_track_pct=on_track_pct,
        this_week_publish_count=this_week,
        top_piece_title=top_piece_title,
        top_piece_conversions=top_piece_conversions,
        x_conversion_rate_pct=x_conversion_rate_pct,
        channel_standins=channel_standins,
        library_count=library_count,
        testimonial_stub_count=testimonial_stub_count,
    )


def _pct(value: int, total: int) -> int:
    """Integer percent of ``value`` over ``total``, clamped to [0, 100] (0 for total<=0)."""
    if total <= 0:
        return 0
    return max(0, min(100, round(100 * value / total)))

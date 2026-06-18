"""Later-lifecycle nurture policy — pure, params-driven (INV-11).

The cockpit owns the nurture POLICY (cadence, school-year re-engagement windows);
HubSpot owns nurture EXECUTION (the drip sends). This module is the deterministic
core for the policy half: it computes, for a given day, how hard a parked family's
re-engagement should ramp because a school-year window is approaching.

Pure: imports only the typed params model + stdlib (the core-purity test guards
this). No I/O, no LLM, no clock — ``today`` is passed in so it is fully testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.params import NurtureAnchor, PresumedLost
from app.observability.log_store import NO_RESPONSE_DISPOSITIONS, ContactOutcomeRecord


@dataclass(frozen=True)
class AnchorPressure:
    """The strongest school-year re-engagement signal for a given day.

    ``pressure`` ∈ [0,1] rises from 0 (≥ ``ramp_days`` before the nearest anchor) to
    1.0 ON the anchor date; ``anchor`` names which window drove it (None at zero).
    """

    pressure: float
    anchor: str | None


def _days_until_next(today: date, month: int, day: int) -> int | None:
    """Whole days from ``today`` to the next yearly occurrence of (month, day).

    0 when today IS the anchor date; rolls to next year once this year's date has
    passed. Returns None for an impossible calendar date (e.g. Feb 30) so a bad
    anchor is skipped rather than crashing the deriver.
    """
    for year in (today.year, today.year + 1):
        try:
            occ = date(year, month, day)
        except ValueError:
            return None
        if occ >= today:
            return (occ - today).days
    return None


def anchor_pressure(today: date, anchors: list[NurtureAnchor]) -> AnchorPressure:
    """The max re-engagement pressure across all anchors for ``today``.

    For each anchor, pressure is ``1 - days_until / ramp_days`` while within the
    ramp window (and ``ramp_days > 0``), else 0. The strongest wins; ties keep the
    first anchor in config order.
    """
    best = AnchorPressure(0.0, None)
    for a in anchors:
        days = _days_until_next(today, a.month, a.day)
        if days is None or a.ramp_days <= 0 or days > a.ramp_days:
            continue
        pressure = 1.0 - days / a.ramp_days
        if pressure > best.pressure:
            best = AnchorPressure(pressure, a.name)
    return best


def is_cold(*, stall_date: datetime, now: datetime, cold_after_days: int) -> bool:
    """Whether a stalled family has gone COLD — stalled longer than the threshold.

    True once ``now - stall_date`` reaches ``cold_after_days`` (inclusive boundary).
    COLD is a more-urgent STALLED (still active — an annotation, not a removal); the
    recency precedence (a contacted family is WORKING, not COLD) is the recovery
    deriver's job — this only decides the age threshold. ``stall_date`` is the API
    layer's derived stall-anchor; ``now`` is read once per request (INV-2: the pure
    core never reads a clock).
    """
    return (now - stall_date) >= timedelta(days=cold_after_days)


def count_no_response(
    outcomes: Iterable[ContactOutcomeRecord], *, now: datetime, within_days: int
) -> int:
    """How many no-response contact attempts fall within the trailing window.

    Counts only the no-response dispositions (the silence that accrues toward
    presumed-lost — :data:`NO_RESPONSE_DISPOSITIONS`); a live ``REACHED`` contact or
    a payment commitment is not silence. Window is ``[now - within_days, now]`` on
    each outcome's ``created_at``, so old attempts age out.
    """
    cutoff = now - timedelta(days=within_days)
    return sum(
        1 for o in outcomes if o.disposition in NO_RESPONSE_DISPOSITIONS and o.created_at >= cutoff
    )


def is_presumed_lost(
    outcomes: Iterable[ContactOutcomeRecord], policy: PresumedLost, *, now: datetime
) -> bool:
    """Whether a family should be SURFACED as 'presumed lost' (a human then confirms).

    True once ``policy.after_attempts`` no-response attempts have accrued within
    ``policy.within_days``. This only raises the suggestion — it never removes the
    family; ``policy.requires_human_confirm`` gates the actual LOST transition at the
    API layer (the machine never auto-drops a warm lead).
    """
    return (
        count_no_response(outcomes, now=now, within_days=policy.within_days)
        >= policy.after_attempts
    )

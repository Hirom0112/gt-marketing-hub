"""Pure Field & Events derivations (Module 8; INV-2 / INV-6 / INV-11).

The deterministic core behind the Field & Events surface: given the GT-organized
field-event rows, compute

1. the OVERVIEW rollup (upcoming count within an injected window, completed-this-month,
   RSVP/attendance totals + the rsvp→attendance rate, the consults total + the
   event→consult rate, and the top event type by attendance), with the reference date
   ``now`` INJECTED (the core reads no clock — mirrors :mod:`app.core.grassroots`'s
   ``as_of`` injection), and
2. a pure TRACKER FILTER (type / status / owner / date range).

This is the deterministic, *pure* core (mirrors :mod:`app.core.grassroots`): a function
of its inputs + the params dials alone — no repository, adapter, decision-queue, httpx,
or LLM import (the core-purity test guards this). Every threshold/window is read from
params (INV-11); nothing is a code literal except the closed status wire-set.

HONESTY (the manual-entry mandate): this module is MANUAL ENTRY — ``consults_booked``
is a hand-logged field, so the event→consult conversion is COMPUTED from manual data,
NOT auto-instrumented. :func:`overview` surfaces this with an explicit
``event_to_consult_manual`` flag in the payload so the UI never implies live tracking.
Aggregate + PII-free (INV-1/INV-6): venue is an aggregate label and no PII enters here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Protocol

# ---------------------------------------------------------------------------
# Lifecycle statuses — the closed ordered set (named wire tokens, not tunables; the
# INV-11 carve-out, like grassroots.STAGE_*). The migration's CHECK mirrors these.
# ---------------------------------------------------------------------------
STATUS_PLANNING = "planning"
STATUS_CONFIRMED = "confirmed"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

# Display/iteration order (planning → cancelled).
STATUSES: tuple[str, ...] = (
    STATUS_PLANNING,
    STATUS_CONFIRMED,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
)


class FieldEventLike(Protocol):
    """The structural shape the core reads off a field event (source-agnostic).

    The store :class:`app.data.field_events_store.FieldEvent` satisfies this
    structurally, so the API passes its rows straight in and the pure core never
    imports the store/adapter layer (the grassroots ``*View`` pattern, generalized).
    Members are read-only properties so a FROZEN dataclass (read-only fields) matches.
    """

    @property
    def event_type(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def owner(self) -> str: ...
    @property
    def event_date(self) -> date: ...
    @property
    def rsvp_count(self) -> int: ...
    @property
    def attendance_count(self) -> int: ...
    @property
    def consults_booked(self) -> int: ...


def _pct(numerator: int, denominator: int) -> int:
    """Integer percent of ``numerator`` against ``denominator``, clamped to ``[0, 100]``.

    Returns ``0`` for a non-positive denominator (the rate is undefined — never a
    div-by-0).
    """
    if denominator <= 0:
        return 0
    return max(0, min(100, round(100 * numerator / denominator)))


def overview(
    events: Iterable[FieldEventLike],
    *,
    now: date,
    upcoming_window_days: int,
) -> dict[str, object]:
    """The Field & Events overview rollup — computed, never faked (INV-2; honest rates).

    ``now`` is INJECTED (the core reads no clock); ``upcoming_window_days`` is read from
    ``params.field_events.upcoming_window_days`` at the API edge (INV-11). Keys:

    - ``upcoming_count`` — events whose ``event_date`` falls in ``[now, now + N days]``
      and whose status is NOT cancelled.
    - ``completed_this_month`` — events with status ``completed`` whose ``event_date``
      lands in the calendar month of ``now``.
    - ``total_rsvps`` / ``total_attendance`` — the summed hand-logged counters.
    - ``rsvp_to_attendance_pct`` — ``round(100 * attendance / rsvps)`` (0 when no RSVPs).
    - ``consults_booked_total`` — the summed hand-logged consults.
    - ``event_to_consult_pct`` — ``round(100 * consults / rsvps)`` (0 when no RSVPs); a
      MANUAL figure (see ``event_to_consult_manual``).
    - ``event_to_consult_manual`` — always ``True``: the conversion is computed from a
      MANUALLY-entered field, NOT auto-tracked (the honesty mandate).
    - ``top_event_type_by_attendance`` — ``{"event_type", "attendance"}`` for the type
      with the most summed attendance (first-seen order breaks a tie); ``None`` when
      there are no events.
    """
    rows = list(events)
    window_end = now + timedelta(days=upcoming_window_days)

    upcoming_count = sum(
        1 for e in rows if e.status != STATUS_CANCELLED and now <= e.event_date <= window_end
    )
    completed_this_month = sum(
        1
        for e in rows
        if e.status == STATUS_COMPLETED
        and e.event_date.year == now.year
        and e.event_date.month == now.month
    )
    total_rsvps = sum(e.rsvp_count for e in rows)
    total_attendance = sum(e.attendance_count for e in rows)
    consults_total = sum(e.consults_booked for e in rows)

    top = _top_type_by_attendance(rows)

    return {
        "upcoming_count": upcoming_count,
        "completed_this_month": completed_this_month,
        "total_rsvps": total_rsvps,
        "total_attendance": total_attendance,
        "rsvp_to_attendance_pct": _pct(total_attendance, total_rsvps),
        "consults_booked_total": consults_total,
        "event_to_consult_pct": _pct(consults_total, total_rsvps),
        # The conversion is computed from a MANUALLY-entered field — not instrumented.
        "event_to_consult_manual": True,
        "top_event_type_by_attendance": top,
    }


def _top_type_by_attendance(
    rows: Sequence[FieldEventLike],
) -> dict[str, object] | None:
    """The event type with the most summed attendance (first-seen tie-break), or None."""
    order: list[str] = []
    totals: dict[str, int] = {}
    for e in rows:
        if e.event_type not in totals:
            order.append(e.event_type)
            totals[e.event_type] = 0
        totals[e.event_type] += e.attendance_count
    if not order:
        return None
    best = max(order, key=lambda t: totals[t])
    return {"event_type": best, "attendance": totals[best]}


def tracker_filter[E: FieldEventLike](  # noqa: A002 - `type` mirrors the query-param name
    events: Iterable[E],
    *,
    type: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[E]:
    """Filter field events by any combination of type / status / owner / date range — pure.

    Each criterion is AND-ed; a ``None`` criterion is ignored (no narrowing). The date
    range is inclusive on both ends (``date_from <= event_date <= date_to``). Returns the
    SAME row objects (in input order) so the API serializes them directly.
    """
    result: list[E] = []
    for e in events:
        if type is not None and e.event_type != type:
            continue
        if status is not None and e.status != status:
            continue
        if owner is not None and e.owner != owner:
            continue
        if date_from is not None and e.event_date < date_from:
            continue
        if date_to is not None and e.event_date > date_to:
            continue
        result.append(e)
    return result

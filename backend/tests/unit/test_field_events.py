"""Pure Field & Events core tests (Module 8) — overview rollup + tracker filter.

Headline invariants:

- :func:`app.core.field_events.overview` computes the 8a rollup from the seeded demo
  events with an INJECTED ``now`` (the core reads no clock): the upcoming window, the
  completed-this-month count, the rsvp→attendance + event→consult rates, and the top
  event type by attendance — every figure computed, never faked (INV-2).
- The event→consult conversion is flagged MANUAL (``event_to_consult_manual``) — it is
  computed from a hand-logged field, NOT auto-instrumented (the honesty mandate).
- :func:`tracker_filter` filters by each criterion and any combination (pure).
"""

from __future__ import annotations

from datetime import date

from app.core import field_events as core
from app.core.program import Program
from app.data.field_events_store import InMemoryFieldEventsStore

_PROGRAM = Program.FALL_ENROLLMENT
# The seed anchors all dates to 2026-06-15; injecting it makes the windowed counts
# deterministic (the API injects the REAL now at the edge).
_NOW = date(2026, 6, 15)
_WINDOW = 30


def _seeded() -> InMemoryFieldEventsStore:
    store = InMemoryFieldEventsStore()
    store.seed_demo(_PROGRAM)
    return store


def test_overview_windowed_counts() -> None:
    """Upcoming (next 30d, not cancelled) + completed-this-month, with injected now."""
    events = _seeded().list_events(_PROGRAM)
    rollup = core.overview(events, now=_NOW, upcoming_window_days=_WINDOW)
    # 3 upcoming inside [2026-06-15, 2026-07-15] (festival/community/webinar); the
    # cancelled street-fair booth in the window is excluded.
    assert rollup["upcoming_count"] == 3
    # 2 completed in June 2026 (chess Jun-3, ama Jun-9); the shadow day is May 26.
    assert rollup["completed_this_month"] == 2


def test_overview_totals_and_rates() -> None:
    """RSVP/attendance totals + computed rates + the MANUAL event→consult figure."""
    events = _seeded().list_events(_PROGRAM)
    rollup = core.overview(events, now=_NOW, upcoming_window_days=_WINDOW)
    assert rollup["total_rsvps"] == 230
    assert rollup["total_attendance"] == 96
    assert rollup["rsvp_to_attendance_pct"] == 42  # round(100 * 96 / 230)
    assert rollup["consults_booked_total"] == 28
    assert rollup["event_to_consult_pct"] == 12  # round(100 * 28 / 230)
    # HONESTY: the conversion is computed from a manually-entered field.
    assert rollup["event_to_consult_manual"] is True


def test_overview_top_event_type_by_attendance() -> None:
    """The AMA webinar has the most attendance (41) → it is the top type."""
    events = _seeded().list_events(_PROGRAM)
    rollup = core.overview(events, now=_NOW, upcoming_window_days=_WINDOW)
    top = rollup["top_event_type_by_attendance"]
    assert top == {"event_type": "ama", "attendance": 41}


def test_overview_empty_is_zeroed() -> None:
    """No events ⇒ all counts/rates zero and top type None (no div-by-0)."""
    rollup = core.overview([], now=_NOW, upcoming_window_days=_WINDOW)
    assert rollup["total_rsvps"] == 0
    assert rollup["rsvp_to_attendance_pct"] == 0
    assert rollup["event_to_consult_pct"] == 0
    assert rollup["top_event_type_by_attendance"] is None


def test_tracker_filter_by_type() -> None:
    events = _seeded().list_events(_PROGRAM)
    festivals = core.tracker_filter(events, type="festival")
    assert {e.event_type for e in festivals} == {"festival"}
    assert len(festivals) == 2  # the robotics festival + the cancelled street fair


def test_tracker_filter_by_status() -> None:
    events = _seeded().list_events(_PROGRAM)
    completed = core.tracker_filter(events, status=core.STATUS_COMPLETED)
    assert len(completed) == 3
    assert all(e.status == core.STATUS_COMPLETED for e in completed)


def test_tracker_filter_by_owner_and_date_range() -> None:
    events = _seeded().list_events(_PROGRAM)
    # owner is 'events' for every seeded row.
    assert len(core.tracker_filter(events, owner="events")) == 7
    assert core.tracker_filter(events, owner="nobody") == []
    # On/after 2026-06-15: festival (Jun-23), community (Jun-30), webinar (Jul-7),
    # cancelled street fair (Jun-20) → 4.
    on_or_after = core.tracker_filter(events, date_from=date(2026, 6, 15))
    assert len(on_or_after) == 4


def test_tracker_filter_combined() -> None:
    events = _seeded().list_events(_PROGRAM)
    cancelled_festival = core.tracker_filter(events, type="festival", status=core.STATUS_CANCELLED)
    assert len(cancelled_festival) == 1
    assert cancelled_festival[0].event_name == "Downtown street fair booth"

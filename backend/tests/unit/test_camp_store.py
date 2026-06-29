"""Unit tests for the Summer Camp store + the Phase-1 reconcile dimensions (Module 4).

Exercises the deterministic in-memory seed (both synthetic sources + per-row signup
channel + registration recency, the four campuses, the four Aug-2026 sessions) and the
PURE, clock-free helpers in :mod:`app.core.summer_reconcile` (channel breakdown, funnel
drop-off, the recent-window count with an INJECTED now, the camp-start countdown, the
per-campus waitlist, and the campus/grade-band slice). No I/O, no live clock.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.api import deps
from app.core.params import Params
from app.core.program import Program
from app.core.summer_reconcile import (
    channel_breakdown,
    days_until,
    reconcile,
    registration_funnel,
    registrations_in_window,
    waitlist_by_campus,
)
from app.data.camp_store import (
    _RECENT_REGISTRATIONS,
    _REGISTRATION_REF,
    InMemoryCampStore,
)

_PROGRAM = Program.SUMMER_CAMP


@pytest.fixture
def params() -> Params:
    """The committed params (channels + capacities the seed reads — INV-11)."""
    return deps.get_params()


@pytest.fixture
def store(params: Params) -> InMemoryCampStore:
    """A fresh SEEDED in-memory camp store (both sources + channels + sessions)."""
    s = InMemoryCampStore(params=params)
    s.seed_demo(_PROGRAM)
    return s


def _core(store: InMemoryCampStore):
    return [r.to_core() for r in store.list_registrations(_PROGRAM)]


def _capacities(params: Params) -> dict[str, int]:
    return dict(params.summer_camp.campus_capacity)


# --------------------------------------------------------------------------- seed
def test_seed_persists_both_sources_and_reconciles_to_288_219(
    store: InMemoryCampStore, params: Params
) -> None:
    core = _core(store)
    result = reconcile(core, _capacities(params))
    assert result.unique_registrations == 288
    assert result.total_registered == 288
    assert result.total_paid == 219
    assert result.total_lead == 288 - 219
    # Both sources persisted; the raw union is larger than the deduped set (overlap).
    assert {s.source for s in result.sources} == {"summer_site", "registration_form"}
    assert result.raw_source_rows > result.unique_registrations
    assert result.duplicates_merged == result.raw_source_rows - 288
    assert result.conflicts == ()


def test_seed_is_idempotent(store: InMemoryCampStore) -> None:
    before = len(store.list_registrations(_PROGRAM))
    store.seed_demo(_PROGRAM)  # guarded no-op
    assert len(store.list_registrations(_PROGRAM)) == before


def test_seed_requires_params() -> None:
    with pytest.raises(ValueError, match="requires params"):
        InMemoryCampStore().seed_demo(_PROGRAM)


def test_seed_seeds_four_campuses_and_four_sessions(
    store: InMemoryCampStore, params: Params
) -> None:
    campuses = store.list_campuses(_PROGRAM)
    assert {c.campus for c in campuses} == set(params.summer_camp.campus_capacity)
    sessions = store.list_sessions(_PROGRAM)
    assert len(sessions) == 4
    # 3× two-week + 1× one-week (San Antonio is the one-week).
    durations = sorted(s.duration for s in sessions)
    assert durations == ["1wk", "2wk", "2wk", "2wk"]
    one_week = next(s for s in sessions if s.duration == "1wk")
    assert one_week.campus == "San Antonio"


# ----------------------------------------------------------------- channels
def test_channel_breakdown_word_of_mouth_is_top_and_counts_dedupe(
    store: InMemoryCampStore,
) -> None:
    core = _core(store)
    breakdown = channel_breakdown(core)
    # Sorted desc ⇒ the first row is the top channel.
    assert breakdown[0].channel == "word_of_mouth"
    # Counts are over the DEDUPED registrant set (288), not the raw union.
    assert sum(c.count for c in breakdown) == 288
    assert round(sum(c.pct for c in breakdown)) == 100
    # word_of_mouth ~40% (the seeded 8/20 weight) — clearly the largest.
    top = breakdown[0]
    assert top.count > 288 * 0.30
    assert all(top.count >= c.count for c in breakdown)


def test_channel_assignment_is_consistent_per_registrant(params: Params) -> None:
    """Both source rows of one registrant share the SAME channel (clean dedup)."""
    s = InMemoryCampStore(params=params)
    s.seed_demo(_PROGRAM)
    by_email: dict[str, set[str]] = {}
    for row in s.list_registrations(_PROGRAM):
        if row.synthetic_email and row.registration_channel:
            by_email.setdefault(row.synthetic_email, set()).add(row.registration_channel)
    # Every email maps to exactly one channel across its (1 or 2) source rows.
    assert all(len(channels) == 1 for channels in by_email.values())


# ----------------------------------------------------------------- funnel
def test_funnel_stages_and_drop_off(store: InMemoryCampStore, params: Params) -> None:
    result = reconcile(_core(store), _capacities(params))
    funnel = registration_funnel(result, attended=0)
    assert [f.stage for f in funnel] == ["Lead", "Registered", "Paid", "Attended"]
    counts = {f.stage: f.count for f in funnel}
    # Lead is floored at Registered (pre-registration inquiries aren't instrumented)
    # and flagged pending — the honest, monotonic funnel is Registered → Paid.
    assert counts == {"Lead": 288, "Registered": 288, "Paid": 219, "Attended": 0}
    lead_stage = next(f for f in funnel if f.stage == "Lead")
    assert lead_stage.pending is True
    assert lead_stage.drop_off_pct == 0.0
    # Registered → Paid drops 69 of 288 (the real instrumented drop-off).
    paid_stage = next(f for f in funnel if f.stage == "Paid")
    assert paid_stage.drop_off_pct == round((288 - 219) / 288 * 100, 1)
    # Attended is honestly pending (camp is in the future) — not a real drop to zero.
    attended_stage = next(f for f in funnel if f.stage == "Attended")
    assert attended_stage.pending is True
    assert attended_stage.count == 0


# ----------------------------------------------------------------- weekly window
def test_registrations_in_window_with_injected_now(store: InMemoryCampStore) -> None:
    core = _core(store)
    # The seed lands EXACTLY _RECENT_REGISTRATIONS registrants inside the last 7 days of
    # the documented reference; injecting that reference makes the count deterministic.
    count = registrations_in_window(core, now=_REGISTRATION_REF, days=7)
    assert count == _RECENT_REGISTRATIONS == 30


def test_registrations_in_window_zero_days_is_zero(store: InMemoryCampStore) -> None:
    assert registrations_in_window(_core(store), now=_REGISTRATION_REF, days=0) == 0


# ----------------------------------------------------------------- countdown
def test_days_until_camp_start_with_injected_now(store: InMemoryCampStore) -> None:
    earliest = min(s.starts_on for s in store.list_sessions(_PROGRAM))
    assert earliest == date(2026, 8, 3)
    # Injected "today" ⇒ a deterministic countdown (clock-free helper).
    assert days_until(earliest, now=date(2026, 6, 28)) == 36
    # Negative once the start has passed (honest).
    assert days_until(earliest, now=date(2026, 8, 10)) == -7


# ----------------------------------------------------------------- waitlist
def test_waitlist_is_zero_under_seeded_fill(store: InMemoryCampStore, params: Params) -> None:
    result = reconcile(_core(store), _capacities(params))
    waitlist = waitlist_by_campus(result)
    assert {w.campus for w in waitlist} == set(params.summer_camp.campus_capacity)
    assert all(w.waitlisted == 0 for w in waitlist)  # every campus under capacity


def test_waitlist_surfaces_overflow_when_oversubscribed() -> None:
    """An over-subscribed campus surfaces a real overflow (computed, not faked)."""
    from app.core.summer_reconcile import CampRegistration

    # Three distinct registrants into a 2-seat campus ⇒ 1 over.
    regs = [
        CampRegistration(
            external_id=f"x{i}",
            source="summer_site",
            campus="Austin",
            child_grade_band="K-2",
            synthetic_email=f"f{i}@example.invalid",
            synthetic_phone=None,
            paid=False,
        )
        for i in range(3)
    ]
    result = reconcile(regs, {"Austin": 2})
    waitlist = waitlist_by_campus(result)
    austin = next(w for w in waitlist if w.campus == "Austin")
    assert austin.registered == 3
    assert austin.waitlisted == 1


# ----------------------------------------------------------------- slicing
def test_campus_slice_narrows_the_rollup(store: InMemoryCampStore, params: Params) -> None:
    core = [r.to_core() for r in store.list_registrations(_PROGRAM) if r.campus == "Austin"]
    result = reconcile(core, {"Austin": params.summer_camp.campus_capacity["Austin"]})
    assert len(result.per_campus) == 1
    assert result.per_campus[0].campus == "Austin"
    assert result.per_campus[0].registered == 86  # the Austin synthetic fill target


def test_grade_band_slice_filters_rows(store: InMemoryCampStore, params: Params) -> None:
    bands = {r.child_grade_band for r in store.list_registrations(_PROGRAM)}
    assert bands == {"K-2", "3-5", "6-8"}
    core = [r.to_core() for r in store.list_registrations(_PROGRAM) if r.child_grade_band == "K-2"]
    result = reconcile(core, _capacities(params))
    # The K-2 slice is a strict, non-empty subset of the full 288.
    assert 0 < result.total_registered < 288


# ----------------------------------------------------------------- camp payments (0038)
def test_record_camp_payment_is_idempotent_and_rolls_up_collected_revenue() -> None:
    """The camp payment ledger upserts on payment_id and sums succeeded charges."""
    s = InMemoryCampStore()  # a clean store needs no params (no seed for the ledger)
    s.record_camp_payment(
        _PROGRAM,
        payment_id="pi_1",
        campus="Austin",
        amount_cents=97500,
        currency="usd",
        status="succeeded",
        stripe_event_id="evt_1",
    )
    # Re-recording the SAME PaymentIntent merges (no double-count) — at-least-once safe.
    s.record_camp_payment(
        _PROGRAM,
        payment_id="pi_1",
        campus="Austin",
        amount_cents=97500,
        currency="usd",
        status="succeeded",
        stripe_event_id="evt_1",
    )
    s.record_camp_payment(
        _PROGRAM,
        payment_id="pi_2",
        campus="Dallas",
        amount_cents=97500,
        currency="usd",
        status="succeeded",
        stripe_event_id="evt_2",
    )
    # A non-succeeded charge is excluded from collected revenue.
    s.record_camp_payment(
        _PROGRAM,
        payment_id="pi_3",
        campus="Austin",
        amount_cents=97500,
        currency="usd",
        status="requires_payment_method",
        stripe_event_id="evt_3",
    )

    assert len(s.list_camp_payments(_PROGRAM)) == 3  # pi_1 stored once, pi_2, pi_3
    collected = s.collected_revenue(_PROGRAM)
    assert collected == {
        "total_cents": 195000,
        "by_campus": {"Austin": 97500, "Dallas": 97500},
        "count": 2,
    }

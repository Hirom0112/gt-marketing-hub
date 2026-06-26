"""A2 watermark store — the durable per-program incremental-poll state seam.

RED→GREEN for :class:`app.data.watermark_store.InMemoryWatermarkStore`, the
CI-tested path (the live :class:`SupabaseWatermarkStore` is exercised only against
a real DB). The store is a dumb durable map keyed ``(program, object_type)``: it
stores and returns a watermark, and never moves it backward — that "never
backward" rule is the CALLER's job (the poller's ``advance_watermark``), so the
store just round-trips whatever it is handed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.program import Program
from app.data.watermark_store import InMemoryWatermarkStore, WatermarkStore


def test_get_watermark_is_none_before_any_set() -> None:
    """An unseen ``(program, object_type)`` reads ``None`` (a cold full backfill)."""
    store: WatermarkStore = InMemoryWatermarkStore()

    assert store.get_watermark(Program.FALL_ENROLLMENT, "deal") is None


def test_set_then_get_round_trips() -> None:
    """A set watermark reads back exactly (the durable round-trip)."""
    store = InMemoryWatermarkStore()
    value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    store.set_watermark(Program.FALL_ENROLLMENT, "deal", value)

    assert store.get_watermark(Program.FALL_ENROLLMENT, "deal") == value


def test_isolated_per_program_and_object_type() -> None:
    """The store keys on BOTH program and object_type — no cross-key bleed."""
    store = InMemoryWatermarkStore()
    fall_deal = datetime(2026, 1, 2, tzinfo=UTC)
    summer_deal = datetime(2026, 2, 3, tzinfo=UTC)
    fall_contact = datetime(2026, 3, 4, tzinfo=UTC)

    store.set_watermark(Program.FALL_ENROLLMENT, "deal", fall_deal)
    store.set_watermark(Program.SUMMER_CAMP, "deal", summer_deal)
    store.set_watermark(Program.FALL_ENROLLMENT, "contact", fall_contact)

    assert store.get_watermark(Program.FALL_ENROLLMENT, "deal") == fall_deal
    assert store.get_watermark(Program.SUMMER_CAMP, "deal") == summer_deal
    assert store.get_watermark(Program.FALL_ENROLLMENT, "contact") == fall_contact
    # A program/object_type never set stays None.
    assert store.get_watermark(Program.SUMMER_CAMP, "contact") is None


def test_set_overwrites_in_place() -> None:
    """A second set replaces the stored value (the store does not guard direction)."""
    store = InMemoryWatermarkStore()
    first = datetime(2026, 1, 2, tzinfo=UTC)
    second = datetime(2026, 5, 6, tzinfo=UTC)

    store.set_watermark(Program.FALL_ENROLLMENT, "deal", first)
    store.set_watermark(Program.FALL_ENROLLMENT, "deal", second)

    assert store.get_watermark(Program.FALL_ENROLLMENT, "deal") == second

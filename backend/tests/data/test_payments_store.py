"""A3 payments store — the Stripe dedupe + payment-ledger seam.

RED→GREEN for :class:`app.data.payments_store.InMemoryPaymentsStore`, the
CI-tested path (the live :class:`SupabasePaymentsStore` is exercised only against
a real DB). The store does two jobs over the two append-only, program-scoped
0026 tables:

* ``stripe_events`` — the inbound-event DEDUPE LEDGER (idempotency): record an
  ``event_id`` once, and answer "have I seen this id?" so a redelivered event is
  processed exactly once (RESEARCH_v2 §II.2). Recording the same id twice is a
  safe no-op (at-least-once delivery is expected).
* ``payment`` — the money LEDGER: append one row per fulfilled payment.

Both are program-scoped: an event/payment recorded under one program is NOT
visible under another.
"""

from __future__ import annotations

from uuid import UUID

from app.core.program import Program
from app.data.payments_store import InMemoryPaymentsStore, PaymentsStore


def test_is_event_seen_is_false_before_record() -> None:
    """An unrecorded ``event_id`` is not seen (the dedupe check defaults open)."""
    store: PaymentsStore = InMemoryPaymentsStore()

    assert store.is_event_seen(Program.FALL_ENROLLMENT, "evt_1") is False


def test_record_event_then_seen() -> None:
    """A recorded ``event_id`` reads back as seen (the idempotency ledger)."""
    store = InMemoryPaymentsStore()

    store.record_event(
        Program.FALL_ENROLLMENT,
        "evt_1",
        "checkout.session.completed",
        "cs_123",
    )

    assert store.is_event_seen(Program.FALL_ENROLLMENT, "evt_1") is True


def test_record_event_twice_is_idempotent() -> None:
    """Recording the same ``event_id`` twice is a safe no-op (at-least-once safe)."""
    store = InMemoryPaymentsStore()

    store.record_event(Program.FALL_ENROLLMENT, "evt_1", "checkout.session.completed", "cs_123")
    # A redelivered event records again — must not raise, still seen exactly once.
    store.record_event(Program.FALL_ENROLLMENT, "evt_1", "checkout.session.completed", "cs_123")

    assert store.is_event_seen(Program.FALL_ENROLLMENT, "evt_1") is True


def test_events_isolated_per_program() -> None:
    """An event recorded under one program is NOT seen under another (A1 tenancy)."""
    store = InMemoryPaymentsStore()

    store.record_event(Program.FALL_ENROLLMENT, "evt_1", "checkout.session.completed", None)

    assert store.is_event_seen(Program.FALL_ENROLLMENT, "evt_1") is True
    assert store.is_event_seen(Program.SUMMER_CAMP, "evt_1") is False


def test_record_payment_stores_the_row() -> None:
    """A recorded payment is appended to the program's ledger (the money ledger)."""
    store = InMemoryPaymentsStore()
    family_id = UUID("00000000-0000-0000-0000-0000000000a1")

    store.record_payment(
        Program.FALL_ENROLLMENT,
        family_id=family_id,
        event_id="evt_1",
        amount_cents=261850,
        currency="usd",
        status="succeeded",
    )

    payments = store.list_payments(Program.FALL_ENROLLMENT)
    assert len(payments) == 1
    row = payments[0]
    assert row.family_id == family_id
    assert row.event_id == "evt_1"
    assert row.amount_cents == 261850
    assert row.currency == "usd"
    assert row.status == "succeeded"


def test_payments_isolated_per_program() -> None:
    """A payment recorded under one program is invisible under another (A1 tenancy)."""
    store = InMemoryPaymentsStore()

    store.record_payment(
        Program.FALL_ENROLLMENT,
        family_id=None,
        event_id="evt_1",
        amount_cents=1000,
        currency="usd",
        status="succeeded",
    )

    assert len(store.list_payments(Program.FALL_ENROLLMENT)) == 1
    assert store.list_payments(Program.SUMMER_CAMP) == []

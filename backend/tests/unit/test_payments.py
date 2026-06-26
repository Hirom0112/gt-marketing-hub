"""Pure Stripe webhook decision-core tests (A3; RESEARCH_v2 §II.2; CLAUDE.md §4.1).

``decide_payment_event`` is the INV-2 deterministic core for one
already-signature-verified Stripe Event: given the event dict, an ``already_seen``
dedupe flag (the API layer checks the dedupe store and passes the bool), and the
configured ``fulfill_event_types`` (read from ``params.stripe`` — INV-11, never
hardcoded here), it RETURNS a decision; the API layer owns the actual writes.

- a fulfill-type event not yet seen ⇒ ``FULFILL`` (with extracted money fields);
- the SAME event already seen ⇒ ``NOOP`` (no double-fulfillment, regardless of type);
- a new but non-fulfill-type event ⇒ ``ACK`` (record for audit, do not fulfill).
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params
from app.core.payments import (
    PaymentDecision,
    PaymentDecisionKind,
    decide_payment_event,
)

# The committed example file is the authoritative source of the param values.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

FULFILL_EVENT_TYPES = load_params(EXAMPLE_PARAMS).stripe.fulfill_event_types


def _checkout_completed_event() -> dict:
    """A representative signature-verified ``checkout.session.completed`` Event."""
    return {
        "id": "evt_test_123",
        "type": "checkout.session.completed",
        "created": 1_700_000_000,
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_test_abc",
                "object": "checkout.session",
                "amount_total": 261850,
                "currency": "usd",
                "payment_status": "paid",
            }
        },
    }


def test_duplicate_event_id_is_noop() -> None:
    """The same event id processed twice fulfills once then no-ops (idempotency)."""
    event = _checkout_completed_event()
    # The fulfill type comes from loaded params, not a literal (INV-11 drift guard).
    assert event["type"] in FULFILL_EVENT_TYPES

    first = decide_payment_event(event, already_seen=False, fulfill_event_types=FULFILL_EVENT_TYPES)
    assert first.kind is PaymentDecisionKind.FULFILL
    assert first.event_id == "evt_test_123"

    # SAME event, now seen → NOOP: guards against duplicated event receipts.
    second = decide_payment_event(event, already_seen=True, fulfill_event_types=FULFILL_EVENT_TYPES)
    assert isinstance(second, PaymentDecision)
    assert second.kind is PaymentDecisionKind.NOOP
    assert second.event_id == "evt_test_123"


def test_fulfill_on_checkout_session_completed_only() -> None:
    """Only the configured primary event fulfills; others are merely acked."""
    event = _checkout_completed_event()
    fulfill = decide_payment_event(
        event, already_seen=False, fulfill_event_types=FULFILL_EVENT_TYPES
    )
    assert fulfill.kind is PaymentDecisionKind.FULFILL
    # Money fields are extracted from data.object (the Checkout session).
    assert fulfill.amount_cents == 261850
    assert fulfill.currency == "usd"
    assert fulfill.status == "paid"
    assert fulfill.object_id == "cs_test_abc"
    assert fulfill.dedupe_key == "cs_test_abc:checkout.session.completed"

    # A non-fulfill type (not seen) → ACK: recorded for audit, never fulfilled.
    other = {
        "id": "evt_test_456",
        "type": "payment_intent.created",
        "created": 1_700_000_001,
        "livemode": False,
        "data": {"object": {"id": "pi_test_xyz", "object": "payment_intent"}},
    }
    decision = decide_payment_event(
        other, already_seen=False, fulfill_event_types=FULFILL_EVENT_TYPES
    )
    assert decision.kind is PaymentDecisionKind.ACK
    assert decision.event_type == "payment_intent.created"
    assert decision.amount_cents is None


def test_malformed_event_missing_data_object_does_not_raise() -> None:
    """A malformed event (no data.object keys) decides without crashing."""
    # A fulfill-type event whose data.object is absent must still decide FULFILL
    # while tolerating the missing money fields (surface what's present).
    no_object = {"id": "evt_bad", "type": "checkout.session.completed"}
    decision = decide_payment_event(
        no_object, already_seen=False, fulfill_event_types=FULFILL_EVENT_TYPES
    )
    assert decision.kind is PaymentDecisionKind.FULFILL
    assert decision.amount_cents is None
    assert decision.currency is None
    assert decision.object_id is None

    # A completely empty event dict also decides (ACK, nothing to fulfill on).
    empty = decide_payment_event({}, already_seen=False, fulfill_event_types=FULFILL_EVENT_TYPES)
    assert empty.kind is PaymentDecisionKind.ACK
    assert empty.event_id == ""

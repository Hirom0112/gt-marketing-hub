"""Pure Stripe webhook decision core (A3; RESEARCH_v2 §II.2; CLAUDE.md §3, INV-2).

The deterministic decision for ONE already-signature-verified Stripe Event. A
Stripe Event has ``id`` (``evt_…``), ``type``, ``data.object`` (the resource),
``created`` (epoch seconds) and ``livemode`` (RESEARCH_v2 §II.2). Idempotency
guidance, verbatim: "guard against duplicated event receipts by logging the event
IDs you've processed, and then not processing already-logged events"; for the
rarer two-Event case, dedupe on ``data.object.id`` + ``event.type``. Fulfill on
ONE primary event per flow (``checkout.session.completed`` for Checkout) and
dedupe regardless, to avoid double-fulfillment.

This is the INV-2 deterministic core: it is PURE (stdlib + the passed-in params
only — no httpx / supabase / store / repository imports; the core-purity test
guards this) and it RETURNS a :class:`PaymentDecision`. The API layer owns every
write — the dedupe-store record, the payment row, the funding advancement. The
``already_seen`` flag is supplied by the caller, which checks the dedupe store;
``fulfill_event_types`` is read by the caller from ``params.stripe`` (INV-11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PaymentDecisionKind(StrEnum):
    """What the caller should do with one verified Stripe Event.

    - ``FULFILL`` — a new event of a configured fulfill type: the caller should
      record the payment and advance funding (and log the event id as processed).
    - ``NOOP`` — the event id was already processed: do nothing (no
      double-fulfillment), regardless of the event type.
    - ``ACK`` — a new event that is NOT a fulfill type: record it for audit
      (log its id) but do not fulfill.
    """

    FULFILL = "fulfill"
    NOOP = "noop"
    ACK = "ack"


@dataclass(frozen=True, slots=True)
class PaymentDecision:
    """The deterministic decision for one verified Stripe Event.

    Attributes:
        kind: The action the caller should take (see :class:`PaymentDecisionKind`).
        event_id: The Stripe ``event.id`` (``evt_…``); ``""`` if absent.
        event_type: The Stripe ``event.type``; ``""`` if absent.
        object_id: The ``data.object.id`` (e.g. the Checkout session id), or
            ``None`` if the object/key is missing.
        amount_cents: The fulfilled amount in the smallest currency unit
            (Checkout's ``amount_total``), or ``None`` — only set on ``FULFILL``
            and only when present in the event.
        currency: The ISO currency code (Checkout's ``currency``), or ``None``.
        status: The payment status (Checkout's ``payment_status``), or ``None``.
        dedupe_key: ``f"{object_id}:{event_type}"`` for the rarer two-Event dedupe
            case, or ``None`` when there is no object id; only set on ``FULFILL``.
    """

    kind: PaymentDecisionKind
    event_id: str
    event_type: str
    object_id: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    status: str | None = None
    dedupe_key: str | None = None


def _data_object(event: dict[str, Any]) -> dict[str, Any]:
    """The event's ``data.object`` resource, or ``{}`` if absent/malformed.

    Defensive: a malformed event (missing ``data``, missing ``object``, or a
    non-dict at either level) must NOT crash the decision — it yields an empty
    resource so money fields simply surface as ``None``.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return {}
    obj = data.get("object")
    return obj if isinstance(obj, dict) else {}


def decide_payment_event(
    event: dict[str, Any],
    *,
    already_seen: bool,
    fulfill_event_types: Sequence[str],
) -> PaymentDecision:
    """Decide what to do with one signature-verified Stripe Event (pure).

    Rules (RESEARCH_v2 §II.2):

    1. ``already_seen`` is ``True`` ⇒ ``NOOP`` — never re-process a logged event
       id, regardless of type (no double-fulfillment).
    2. else if ``event["type"]`` is in ``fulfill_event_types`` ⇒ ``FULFILL`` with
       the money fields extracted defensively from ``data.object``.
    3. else ⇒ ``ACK`` — a new event recorded for audit but not fulfilled.

    Args:
        event: The already-signature-verified Stripe Event dict.
        already_seen: Whether the caller's dedupe store has this ``event.id``.
        fulfill_event_types: The configured fulfill event types
            (``params.stripe.fulfill_event_types``, INV-11).

    Returns:
        The :class:`PaymentDecision`; the caller owns all writes (INV-2).
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")

    obj = _data_object(event)
    raw_object_id = obj.get("id")
    object_id = str(raw_object_id) if raw_object_id is not None else None

    if already_seen:
        return PaymentDecision(
            kind=PaymentDecisionKind.NOOP,
            event_id=event_id,
            event_type=event_type,
            object_id=object_id,
        )

    if event_type in fulfill_event_types:
        # Money fields vary by object: a Checkout Session carries ``amount_total`` +
        # ``payment_status``; a PaymentIntent carries ``amount`` + ``status``. Read the
        # Checkout fields first, falling back to the PaymentIntent fields, so BOTH
        # ``checkout.session.completed`` AND ``payment_intent.succeeded`` surface the
        # real amount/status (the camp-revenue slice fulfills on succeeded PIs).
        amount_raw = obj.get("amount_total")
        if amount_raw is None:
            amount_raw = obj.get("amount")
        currency = obj.get("currency")
        status_raw = obj.get("payment_status")
        if status_raw is None:
            status_raw = obj.get("status")
        dedupe_key = f"{object_id}:{event_type}" if object_id is not None else None
        return PaymentDecision(
            kind=PaymentDecisionKind.FULFILL,
            event_id=event_id,
            event_type=event_type,
            object_id=object_id,
            amount_cents=amount_raw if isinstance(amount_raw, int) else None,
            currency=currency if isinstance(currency, str) else None,
            status=status_raw if isinstance(status_raw, str) else None,
            dedupe_key=dedupe_key,
        )

    return PaymentDecision(
        kind=PaymentDecisionKind.ACK,
        event_id=event_id,
        event_type=event_type,
        object_id=object_id,
    )

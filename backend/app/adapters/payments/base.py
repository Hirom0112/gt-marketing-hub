"""The payments boundary — interface + stdlib HMAC webhook verify (A3, INV-8/9).

This is the abstract half of the §7-style ``PaymentsAdapter`` seam. Two impls —
:class:`~app.adapters.payments.simulated.SimulatedPaymentsAdapter` (v1 default,
records-never-sends; INV-9) and
:class:`~app.adapters.payments.live.LivePaymentsAdapter` (production Stripe behind
the INV-8 cap + the registry kill switch) — are selected at startup by config in
:mod:`app.adapters.registry`. The fulfillment core depends only on this interface.

Webhook signature verification is implemented with the **stdlib** ``hmac`` /
``hashlib`` — NO ``stripe`` SDK (the runtime dep budget is a hard ≤15;
TECH_STACK §4.1). The verifier operates on the **raw bytes** body so it can run
BEFORE any JSON parsing, exactly as Stripe documents (RESEARCH_v2 §II.2):

- The ``Stripe-Signature`` header is ``t=<unix_ts>,v1=<hex_hmac>`` and may carry
  multiple ``v1=`` entries (rotated secrets / re-deliveries).
- The signed payload is ``f"{t}.{raw_body}"``; the expected signature is
  ``hmac.new(secret, signed_payload, sha256).hexdigest()``, compared with
  ``hmac.compare_digest`` (constant-time).
- Reject if no ``v1`` matches (forgery/tamper) OR ``abs(now - t) > tolerance``
  (an expired / too-far-future replay). ``now`` is INJECTED (epoch seconds) so the
  expiry check is deterministic — the verifier never reads the wall clock itself
  (the repo's clock-injection discipline).

Adapter config (webhook secret, cap, tolerance) is **constructor-injected** by the
composition root (the registry), exactly like ``LiveHubSpotCRMAdapter.__init__``;
no adapter here reads settings/params itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict

# Stripe's default webhook signature timestamp tolerance — 5 minutes (RESEARCH_v2
# §II.2). The canonical home for the real value is ``params.stripe.tolerance_seconds``
# (INV-11); this is only the fallback default for a directly-constructed adapter and
# never overrides an injected value.
DEFAULT_TOLERANCE_SECONDS = 300


class SignatureVerificationError(RuntimeError):
    """A webhook event failed signature verification — forged, tampered, or expired.

    Raised by :func:`verify_webhook_signature` (and the adapters' ``verify_event``)
    when no ``v1`` candidate matches the expected HMAC, when the timestamp is
    outside the tolerance, or when the verified payload is not a JSON object. The
    API must treat this as a hard reject (never trust the event) — fail closed.
    """


class PaymentsBudgetExceededError(RuntimeError):
    """Guard (INV-8): the per-run outbound Stripe call budget was exhausted.

    Mirrors :class:`app.adapters.hubspot.live_adapter.HubSpotBudgetExceededError`:
    a breach fails closed here rather than silently overspending against the
    metered Stripe API. The registry kill switch is the coarser sibling (degrade
    live→simulated); this is the per-run ceiling.
    """


class PaymentIntentResult(BaseModel):
    """Outcome of a ``create_payment_intent`` outbound call (the payments seam).

    Attributes:
        simulated: ``True`` whenever the simulated impl handled it — the v1 lock
            (INV-9). The live Stripe impl returns ``False``.
        intent_id: The PaymentIntent id (a deterministic synthetic id for the
            simulated impl; the live Stripe ``pi_…`` id otherwise).
        amount_cents: The intent amount in the currency's minor unit.
        currency: The ISO currency code (e.g. ``"usd"``).
        status: The PaymentIntent status (e.g. ``"requires_payment_method"``).
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    intent_id: str
    amount_cents: int
    currency: str
    status: str


def _parse_sig_header(sig_header: str) -> tuple[str | None, list[str]]:
    """Parse a ``Stripe-Signature`` header into ``(timestamp, [v1 signatures])``.

    Tolerant of whitespace and of unknown scheme keys (e.g. ``v0=``); collects the
    ``t`` value and every ``v1`` candidate. A missing ``t`` ⇒ ``None``; no ``v1`` ⇒
    an empty list (both are rejected upstream).
    """
    timestamp: str | None = None
    signatures: list[str] = []
    for part in sig_header.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def verify_webhook_signature(
    payload: bytes,
    sig_header: str,
    *,
    secret: str,
    tolerance_seconds: int,
    now: int,
) -> dict[str, Any]:
    """Verify a Stripe webhook signature over the RAW body; return the Event dict.

    Stdlib-only HMAC verification (RESEARCH_v2 §II.2). Raises
    :class:`SignatureVerificationError` if the header is malformed, no ``v1``
    candidate matches the expected HMAC (forgery/tamper), the timestamp is outside
    ``tolerance_seconds`` of the injected ``now`` (replay), or the verified payload
    is not a JSON object. On success returns the parsed Event dict.

    Args:
        payload: The raw request body bytes (verified BEFORE JSON parsing).
        sig_header: The ``Stripe-Signature`` header value.
        secret: The webhook signing secret (constructor-injected; never read here).
        tolerance_seconds: The max allowed ``abs(now - t)`` before a replay reject.
        now: The current time in epoch seconds (INJECTED — never the wall clock).
    """
    timestamp, signatures = _parse_sig_header(sig_header)
    if timestamp is None or not signatures:
        raise SignatureVerificationError(
            "malformed Stripe-Signature header: missing timestamp or v1 signature."
        )
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureVerificationError(
            f"Stripe-Signature timestamp is not an integer: {timestamp!r}."
        ) from exc

    signed_payload = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    # Constant-time compare against EVERY v1 candidate; reject if none match (the
    # forgery/tamper case — a changed body or a wrong secret yields no match).
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureVerificationError(
            "no Stripe-Signature v1 candidate matches the expected HMAC (forged or tampered)."
        )

    # Replay window: an event whose timestamp is older (or further in the future)
    # than the tolerance is rejected even with a valid signature.
    if abs(now - ts) > tolerance_seconds:
        raise SignatureVerificationError(
            f"Stripe-Signature timestamp outside the {tolerance_seconds}s tolerance "
            f"(t={ts}, now={now}) — rejected as a replay."
        )

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SignatureVerificationError("verified webhook payload is not valid JSON.") from exc
    if not isinstance(event, dict):
        raise SignatureVerificationError("verified webhook payload is not a JSON object.")
    return event


class PaymentsAdapter(ABC):
    """The payments external boundary (A3, INV-8/9).

    Two impls — Simulated (v1 default) and Production (Stripe) — selected by config
    in :mod:`app.adapters.registry`. Core/fulfillment depend only on this interface.
    """

    @abstractmethod
    def verify_event(self, payload: bytes, sig_header: str, *, now: int) -> dict[str, Any]:
        """Verify a webhook signature over the RAW body; return the parsed Event dict.

        Both impls verify with the same stdlib HMAC logic against the injected
        webhook secret (simulated ≠ skip-verification; INV-9 means it never makes a
        live outbound call, not that it trusts unsigned events). Raises
        :class:`SignatureVerificationError` on any failure.
        """

    @abstractmethod
    def create_payment_intent(
        self, *, amount_cents: int, currency: str = "usd", metadata: dict[str, str] | None = None
    ) -> PaymentIntentResult:
        """Create a PaymentIntent (the outbound, budgeted call; INV-8).

        The simulated impl RECORDS the intent and returns a deterministic synthetic
        result (no network; INV-9). The live impl makes a budgeted Stripe API call
        and raises :class:`PaymentsBudgetExceededError` on the (cap+1)th outbound.
        """

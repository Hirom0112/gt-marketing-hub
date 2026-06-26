"""Simulated PaymentsAdapter — verifies offline, records outbound, no I/O (INV-9).

The v1 default impl of the payments boundary. It still VERIFIES webhook signatures
with the same stdlib HMAC logic as the live impl (against an injected test secret),
so the webhook path is fully testable offline — simulated ≠ skip-verification. What
it never does is make a live outbound call: ``create_payment_intent`` RECORDS the
intent in an in-memory log and returns a deterministic synthetic result. "Records,
never sends" is therefore a structural property (no network client exists on this
class), provable from the source alone. This module imports no Stripe SDK and no
``anthropic`` client and touches no ``core/`` state.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.adapters.payments.base import (
    DEFAULT_TOLERANCE_SECONDS,
    PaymentIntentResult,
    PaymentsAdapter,
    SignatureVerificationError,
    verify_webhook_signature,
)


class SimulatedPaymentsAdapter(PaymentsAdapter):
    """In-memory recorder + offline signature verifier (INV-9).

    Args:
        webhook_secret: The signing secret used to verify webhook signatures. May
            be ``None`` (absence is first-class): with no secret, ``verify_event``
            fails closed rather than trusting an unsigned event.
        tolerance_seconds: The signature timestamp tolerance (Stripe default 300).
    """

    def __init__(
        self,
        *,
        webhook_secret: str | None,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._tolerance_seconds = tolerance_seconds
        # Append-only audit log (the "recorder"). No network client.
        self.intents_log: list[PaymentIntentResult] = []

    def verify_event(self, payload: bytes, sig_header: str, *, now: int) -> dict[str, Any]:
        """Verify the webhook signature against the injected secret (offline; INV-9).

        Uses the identical HMAC path as the live impl. A ``None`` secret means no
        secret is configured ⇒ fail closed (never trust an unsigned event).
        """
        if self._webhook_secret is None:
            raise SignatureVerificationError(
                "no webhook secret configured — cannot verify the event (fail closed)."
            )
        return verify_webhook_signature(
            payload,
            sig_header,
            secret=self._webhook_secret,
            tolerance_seconds=self._tolerance_seconds,
            now=now,
        )

    def create_payment_intent(
        self, *, amount_cents: int, currency: str = "usd", metadata: dict[str, str] | None = None
    ) -> PaymentIntentResult:
        """Record a PaymentIntent and return a DETERMINISTIC synthetic result (INV-9).

        Records-never-sends: appends to ``intents_log`` and returns an id derived
        purely from the inputs — no wall clock, no ``uuid4`` — so the same intent
        always yields the same id (re-creating is idempotent in tests/demos).
        """
        digest = hashlib.blake2b(
            f"{amount_cents}:{currency}:{sorted((metadata or {}).items())}".encode(),
            digest_size=8,
        ).hexdigest()
        result = PaymentIntentResult(
            simulated=True,
            intent_id=f"sim_pi_{digest}",
            amount_cents=amount_cents,
            currency=currency,
            status="requires_payment_method",
        )
        self.intents_log.append(result)
        return result

"""Production PaymentsAdapter — live Stripe behind the INV-8 cap (A3).

The production half of the payments seam: it creates PaymentIntents over the live
Stripe API and verifies inbound webhook signatures, behind the **per-run call
budget** (guard, INV-8) that mirrors
:class:`app.adapters.hubspot.live_adapter.LiveHubSpotCRMAdapter`. The simulated
impl stays the v1 default; this one is selected only when ``STRIPE_MODE=live`` with
a webhook secret and no kill switch (see :mod:`app.adapters.registry`). The
fulfillment core changes zero lines — it depends on the
:class:`~app.adapters.payments.base.PaymentsAdapter` interface, not this class.

The HTTP client is **injected** so the adapter never opens a socket in a test
(tests pass a ``httpx.MockTransport``); all config (secret key, webhook secret,
cap, tolerance) is constructor-injected by the registry — this class reads no
settings/params itself. No ``stripe`` SDK is imported (dep budget ≤15): outbound
calls are plain form-encoded ``httpx`` requests, signature verification is stdlib
HMAC (RESEARCH_v2 §II.2).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from app.adapters._resilience import with_retry
from app.adapters.payments.base import (
    DEFAULT_TOLERANCE_SECONDS,
    PaymentIntentResult,
    PaymentsAdapter,
    PaymentsBudgetExceededError,
    SignatureVerificationError,
    verify_webhook_signature,
)
from app.core.params import Resilience

# Stripe API v1 object paths (the live API surface, not a tunable — Stripe's own
# fixed routes; INV-11 governs OUR knobs, not a third party's URLs).
_PAYMENT_INTENTS = "/v1/payment_intents"


class LivePaymentsAdapter(PaymentsAdapter):
    """Production ``PaymentsAdapter`` — live Stripe writes behind the INV-8 cap (A3).

    Args:
        client: An injected ``httpx.Client`` (tests pass one wired to a
            ``MockTransport``). Its ``base_url`` should be ``https://api.stripe.com``.
        secret_key: The Stripe secret API key (Bearer auth) for outbound calls. May
            be ``None`` when the adapter is used only to verify webhooks; an
            outbound call then fails loud.
        webhook_secret: The webhook signing secret used by :meth:`verify_event`.
        calls_per_run_cap: The per-run outbound Stripe call budget (INV-8 guard).
        tolerance_seconds: The webhook signature timestamp tolerance (default 300).
        resilience: The injected ``params.resilience`` block (A5) — the retry
            wrapper's ``max_attempts`` / ``base_delay_ms`` / ``max_delay_ms``. The
            adapter reads these from config, never a code literal (INV-11).
        sleep: The injected clock (seconds) the backoff sleeps on. Production passes
            ``time.sleep`` (the default); a test passes a spy so the retry never
            touches the wall clock (repo clock-injection discipline).
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        secret_key: str | None,
        webhook_secret: str | None,
        calls_per_run_cap: int,
        resilience: Resilience,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._cap = calls_per_run_cap
        self._resilience = resilience
        self._sleep = sleep
        self._tolerance_seconds = tolerance_seconds
        self._calls_made = 0
        if secret_key is not None:
            self._client.headers.update({"Authorization": f"Bearer {secret_key}"})

    # ------------------------------------------------------------------ I/O
    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One budgeted Stripe call — the guard (INV-8) trips on the (cap+1)th.

        The budget is checked BEFORE the call, so an exhausted budget never reaches
        the network (fail closed). The budget is charged ONCE per LOGICAL call: the
        check+increment is OUTER, and :func:`with_retry` wraps ONLY the raw send (A5),
        so a 429/5xx/transport retry self-heals without re-charging the budget — a
        429-then-200 makes 2 HTTP sends but spends 1 unit of quota. ``headers`` (e.g.
        the stable ``Idempotency-Key``) ride EVERY attempt unchanged, captured in the
        retry thunk's closure. A non-2xx response (after retries are exhausted) raises
        via ``raise_for_status``.
        """
        if self._calls_made >= self._cap:
            raise PaymentsBudgetExceededError(
                f"Stripe per-run call budget exhausted ({self._cap}); degrade to "
                f"simulated (INV-8) rather than overspend the metered Stripe API."
            )
        self._calls_made += 1
        response = with_retry(
            lambda: self._client.request(method, path, data=data, headers=headers),
            max_attempts=self._resilience.max_attempts,
            base_delay_ms=self._resilience.base_delay_ms,
            max_delay_ms=self._resilience.max_delay_ms,
            sleep=self._sleep,
        )
        response.raise_for_status()
        return response

    # --------------------------------------------------------------- interface
    def verify_event(self, payload: bytes, sig_header: str, *, now: int) -> dict[str, Any]:
        """Verify the webhook signature against the injected secret (stdlib HMAC).

        A ``None`` secret means none is configured ⇒ fail closed (never trust an
        unsigned event). On success returns the parsed Stripe Event dict.
        """
        if self._webhook_secret is None:
            raise SignatureVerificationError(
                "live payments adapter has no webhook secret configured — cannot "
                "verify the event (fail closed)."
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
        """Create a Stripe PaymentIntent (the outbound, budgeted call; INV-8).

        Sends a form-encoded ``POST /v1/payment_intents`` over the budgeted
        :meth:`_request` path (so the (cap+1)th call raises
        :class:`PaymentsBudgetExceededError`). A V4 ``Idempotency-Key`` is generated
        ONCE here — OUTSIDE the retry thunk — and sent as a header, so when
        :func:`with_retry` re-sends after a transient 429/5xx the SAME key rides the
        retry and Stripe collapses the duplicate POSTs into one charge (Stripe
        outbound idempotency, RESEARCH_v2 §II.2(a); ``uuid4`` is fine in an adapter —
        only ``core/`` forbids it). Returns the live ``pi_…`` id and status. An
        outbound call with no secret key fails loud.
        """
        if self._secret_key is None:
            raise RuntimeError(
                "live payments adapter has no Stripe secret key — cannot create a "
                "PaymentIntent. Configure STRIPE_SECRET_KEY or use STRIPE_MODE=simulate."
            )
        data: dict[str, str] = {"amount": str(amount_cents), "currency": currency}
        for key, value in (metadata or {}).items():
            data[f"metadata[{key}]"] = value
        # One stable key per LOGICAL create — reused across with_retry's resends so a
        # self-healed call never double-charges (RESEARCH_v2 §II.2(a)).
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        body = self._request("POST", _PAYMENT_INTENTS, data=data, headers=headers).json()
        return PaymentIntentResult(
            simulated=False,
            intent_id=str(body["id"]),
            amount_cents=int(body.get("amount", amount_cents)),
            currency=str(body.get("currency", currency)),
            status=str(body.get("status", "")),
        )

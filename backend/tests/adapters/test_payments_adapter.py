"""Payments adapter — stdlib HMAC webhook verify + INV-8 cap/kill-switch (A3).

Two RED-first targets (TODO_v2.md §A3):

- ``test_signature_verify_rejects_forged`` — a valid ``Stripe-Signature`` is
  accepted (returns the parsed Event dict); a tampered ``v1``/body raises
  :class:`SignatureVerificationError`; a timestamp older than the tolerance
  (injected ``now``) raises. The HMAC is computed in the test against a known
  secret so the verifier is exercised offline (INV-9 — no live Stripe call).
- ``test_cap_and_kill_switch_degrade`` — the (cap+1)th outbound call raises
  :class:`PaymentsBudgetExceededError` (INV-8, mirroring the HubSpot guard-3
  pattern); ``effective_payments_mode`` degrades live→simulate under the kill
  switch; and a live-intent-with-no-secret fails loud per the CRM precedent.

All tests run against a ``httpx.MockTransport`` — no real network, no live write.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.adapters.payments.base import (
    PaymentsBudgetExceededError,
    SignatureVerificationError,
)
from app.adapters.payments.live import LivePaymentsAdapter
from app.adapters.payments.simulated import SimulatedPaymentsAdapter
from app.adapters.registry import effective_payments_mode, get_payments_adapter
from app.core.settings import Settings

# A synthetic test webhook secret + a synthetic event body (INV-1: nothing real).
_SECRET = "whsec_test_secret_synthetic"
_BODY = b'{"id": "evt_synthetic_1", "type": "checkout.session.completed"}'
_TS = 1_700_000_000  # a fixed epoch second so the expiry test is deterministic.


def _sign(secret: str, ts: int, body: bytes) -> str:
    """Compute Stripe's ``v1`` HMAC-SHA256 over ``f"{ts}.{body}"`` (RESEARCH_v2 §II.2)."""
    signed_payload = f"{ts}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def _header(ts: int, *sigs: str) -> str:
    """Build a ``Stripe-Signature`` header (``t=<ts>,v1=<sig>[,v1=<sig>...]``)."""
    parts = [f"t={ts}"] + [f"v1={s}" for s in sigs]
    return ",".join(parts)


# --------------------------------------------------------------- signature verify
def test_signature_verify_rejects_forged() -> None:
    """Valid signature accepted; forged signature, tampered body, and expiry rejected."""
    adapter = SimulatedPaymentsAdapter(webhook_secret=_SECRET, tolerance_seconds=300)

    # PASS: a correctly-signed payload within tolerance returns the parsed Event.
    valid_sig = _sign(_SECRET, _TS, _BODY)
    event = adapter.verify_event(_BODY, _header(_TS, valid_sig), now=_TS + 5)
    assert event["id"] == "evt_synthetic_1"
    assert event["type"] == "checkout.session.completed"

    # PASS: multiple v1 candidates, one of which matches, still verifies.
    multi = adapter.verify_event(_BODY, _header(_TS, "0" * 64, valid_sig), now=_TS + 5)
    assert multi["id"] == "evt_synthetic_1"

    # BLOCK: a forged/garbage v1 signature is rejected (no valid v1 matches).
    with pytest.raises(SignatureVerificationError):
        adapter.verify_event(_BODY, _header(_TS, "deadbeef" * 8), now=_TS + 5)

    # BLOCK: a tampered body no longer matches the (valid-for-other-body) signature.
    tampered_body = b'{"id": "evt_synthetic_1", "type": "checkout.session.expired"}'
    with pytest.raises(SignatureVerificationError):
        adapter.verify_event(tampered_body, _header(_TS, valid_sig), now=_TS + 5)

    # BLOCK: a timestamp older than the tolerance is a replay (injected now).
    with pytest.raises(SignatureVerificationError):
        adapter.verify_event(_BODY, _header(_TS, valid_sig), now=_TS + 301)


# ------------------------------------------------------------- cap + kill switch
def _intent_handler(request: httpx.Request) -> httpx.Response:
    """A scripted Stripe — returns a synthetic PaymentIntent for any POST."""
    return httpx.Response(
        200,
        json={
            "id": "pi_synthetic_1",
            "amount": 1000,
            "currency": "usd",
            "status": "requires_payment_method",
        },
    )


def _live_adapter(*, cap: int) -> LivePaymentsAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(_intent_handler), base_url="https://api.stripe.com"
    )
    return LivePaymentsAdapter(
        client=client,
        secret_key="sk_test_synthetic",
        webhook_secret=_SECRET,
        calls_per_run_cap=cap,
        tolerance_seconds=300,
    )


def test_cap_and_kill_switch_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """(cap+1)th outbound raises; kill switch degrades live→simulate; no-secret fails loud."""
    # INV-8: staying under the cap succeeds; the (cap+1)th outbound call raises.
    adapter = _live_adapter(cap=2)
    adapter.create_payment_intent(amount_cents=1000)
    adapter.create_payment_intent(amount_cents=1000)
    with pytest.raises(PaymentsBudgetExceededError):
        adapter.create_payment_intent(amount_cents=1000)

    # INV-8 kill switch: a live mode with a secret but the kill switch ON degrades
    # to "simulate" (mirrors the CRM guard-3 precedence).
    killed = Settings(stripe_mode="live", stripe_webhook_secret=_SECRET, stripe_kill_switch=True)
    assert effective_payments_mode(killed) == "simulate"

    # Live with a secret + no kill switch is a genuine live intent.
    live = Settings(stripe_mode="live", stripe_webhook_secret=_SECRET, stripe_kill_switch=False)
    assert effective_payments_mode(live) == "live"

    # Default (simulate) returns the simulated adapter.
    monkeypatch.delenv("STRIPE_MODE", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert isinstance(get_payments_adapter(), SimulatedPaymentsAdapter)

    # Live INTENT with NO webhook secret ⇒ fail loud at construction (CRM precedent).
    monkeypatch.setenv("STRIPE_MODE", "live")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        get_payments_adapter()


def test_simulated_records_never_sends() -> None:
    """INV-9: the simulated adapter records the intent and never opens a socket."""
    adapter = SimulatedPaymentsAdapter(webhook_secret=_SECRET)
    result = adapter.create_payment_intent(amount_cents=2500, currency="usd")
    assert result.simulated is True
    assert result.amount_cents == 2500
    assert len(adapter.intents_log) == 1
    # Deterministic id (no wall clock / uuid4) — re-deriving is stable per inputs.
    again = adapter.create_payment_intent(amount_cents=2500, currency="usd")
    assert again.intent_id == result.intent_id


def test_event_payload_must_be_json_object() -> None:
    """A signature-valid but non-JSON-object body is rejected (fail closed)."""
    adapter = SimulatedPaymentsAdapter(webhook_secret=_SECRET, tolerance_seconds=300)
    body = b"not-json"
    sig = _sign(_SECRET, _TS, body)
    with pytest.raises(SignatureVerificationError):
        adapter.verify_event(body, _header(_TS, sig), now=_TS + 5)
    # Sanity: the helper used by the API parses the same way json.loads does.
    assert json.loads(_BODY)["id"] == "evt_synthetic_1"

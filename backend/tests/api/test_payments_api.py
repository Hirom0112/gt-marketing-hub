"""A3 Stripe webhook — the composition-root endpoint (acceptance).

Acceptance tests for ``POST /payments/webhook`` — the raw-body verify → dedupe →
deterministic decision → fulfill → fast-2xx path. The endpoint COMPOSES already-built
pieces: the stdlib HMAC verifier (the simulated adapter's ``verify_event``), the pure
``decide_payment_event`` decision, the ``PaymentsStore`` dedupe + money ledger, and the
INV-10 funding advance. Nothing here re-derives that logic — these prove the wiring.

Exercised end-to-end on synthetic data through dependency overrides: the SIMULATED
payments adapter constructed with a KNOWN test webhook secret (so the test can compute a
valid ``Stripe-Signature``), a fresh in-memory payments store, a one-family in-memory
repo seeded at ``GT_CONFIRMED`` (so the first-installment advance is the one legal step),
an in-memory audit log, and a fixed active program.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.payments.simulated import SimulatedPaymentsAdapter
from app.api import deps
from app.core.program import Program
from app.data.models import FundingState, FundingType
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset
from app.main import app
from app.observability.log_store import InMemoryObservabilityLog

client = TestClient(app)

# The known test webhook secret the simulated adapter verifies against — the test
# computes a valid Stripe-Signature with it (offline; no live Stripe).
_TEST_WEBHOOK_SECRET = "whsec_test_payments_api_secret"
_PROGRAM = Program.FALL_ENROLLMENT


def _family_in_state(funding_state: FundingState, funding_type: FundingType):
    """A seeded TEFA family copied into the requested funding state."""
    base = next(
        f
        for f in InMemoryFamilyRepository.seeded().list_families()
        if f.funding_type == funding_type
    )
    return base.model_copy(update={"funding_state": funding_state})


class _Fixtures:
    """The overridden deps for one webhook test — adapter, store, repo, log."""

    def __init__(self) -> None:
        tolerance = deps.get_params().stripe.tolerance_seconds
        self.adapter = SimulatedPaymentsAdapter(
            webhook_secret=_TEST_WEBHOOK_SECRET, tolerance_seconds=tolerance
        )
        self.store = deps.InMemoryPaymentsStore()
        self.log = InMemoryObservabilityLog()
        # A family at GT_CONFIRMED so a first-installment receipt is exactly the one
        # legal §5.4 advance (GT_CONFIRMED → FIRST_INSTALLMENT_RECEIVED).
        self.family = _family_in_state(FundingState.GT_CONFIRMED, FundingType.TEFA_STANDARD)
        self.repo = InMemoryFamilyRepository(SyntheticDataset(families=[self.family]))


@pytest.fixture
def fx() -> Iterator[_Fixtures]:
    """Install the five overrides for the webhook composition root; tear them down."""
    fixtures = _Fixtures()
    app.dependency_overrides[deps.get_payments_adapter_dep] = lambda: fixtures.adapter
    app.dependency_overrides[deps.get_payments_store] = lambda: fixtures.store
    app.dependency_overrides[deps.get_repository] = lambda: fixtures.repo
    app.dependency_overrides[deps.get_observability_log] = lambda: fixtures.log
    app.dependency_overrides[deps.get_active_program] = lambda: _PROGRAM
    yield fixtures
    app.dependency_overrides.clear()


def _checkout_event(family_id: str, *, event_id: str = "evt_test_1") -> bytes:
    """A ``checkout.session.completed`` event body (bytes) pointing at ``family_id``."""
    event: dict[str, Any] = {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_test_123",
                "amount_total": 261850,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {"gt_family_id": family_id},
            }
        },
    }
    return json.dumps(event).encode("utf-8")


def _sign(raw: bytes, *, t: int | None = None) -> str:
    """Compute a valid ``Stripe-Signature`` header for ``raw`` (t=<now>,v1=<hmac>)."""
    ts = int(time.time()) if t is None else t
    signed = f"{ts}.".encode() + raw
    sig = hmac.new(_TEST_WEBHOOK_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_verifies_raw_body_and_returns_2xx_fast(fx: _Fixtures) -> None:
    """Valid signature → 200, payment recorded, event deduped; replay is idempotent."""
    raw = _checkout_event(str(fx.family.family_id))
    header = _sign(raw)

    resp = client.post(
        "/payments/webhook",
        content=raw,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text

    # The payment landed in the money ledger, matched to the family, with the amount.
    payments = fx.store.list_payments(_PROGRAM)
    assert len(payments) == 1
    assert payments[0].family_id == fx.family.family_id
    assert payments[0].amount_cents == 261850
    assert payments[0].currency == "usd"

    # The event id is now recorded in the dedupe ledger.
    assert fx.store.is_event_seen(_PROGRAM, "evt_test_1") is True

    # REPLAY: the SAME event again → 200, but NO double-record / no double-fulfill.
    replay = client.post(
        "/payments/webhook",
        content=raw,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    assert replay.status_code == 200, replay.text
    assert len(fx.store.list_payments(_PROGRAM)) == 1  # idempotent — still one row.


def test_webhook_forged_signature_returns_400(fx: _Fixtures) -> None:
    """A forged/tampered signature is rejected with 400 (NOT a 2xx 'delivered')."""
    raw = _checkout_event(str(fx.family.family_id), event_id="evt_forged")
    forged = f"t={int(time.time())},v1=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    resp = client.post(
        "/payments/webhook",
        content=raw,
        headers={"stripe-signature": forged, "content-type": "application/json"},
    )
    assert resp.status_code == 400
    # Nothing was recorded — a forged event never reaches the ledgers.
    assert fx.store.list_payments(_PROGRAM) == []
    assert fx.store.is_event_seen(_PROGRAM, "evt_forged") is False

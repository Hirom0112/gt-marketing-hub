"""Watch the Stripe webhook edge cases propagate — a reproducible walkthrough (A3).

The brief asks us to PROVE idempotency and the payment seam against data we built to
stress them: "a late/failed/duplicate payment." This driver POSTs synthetic, correctly
*signed* Stripe events through the real ``POST /payments/webhook`` composition root
(verify → dedupe → decide → fulfill → fast-2xx) and prints what propagated, so the whole
loop is visible without a live Stripe account or a database.

It runs the SIMULATED payments adapter (INV-9) with a known webhook secret, so every
signature is genuinely verified — simulated ≠ skip-verification. Nothing here re-derives
the webhook logic; it drives the same endpoint the tests cover and the UI consumes.

Four scenarios, each a real signed event through the live endpoint:

1. NORMAL   — ``checkout.session.completed`` for a GT-confirmed family ⇒ FULFILL: the
              payment lands in the money ledger and the GT funding signal advances one
              legal §5.4 step (GT_CONFIRMED → FIRST_INSTALLMENT_RECEIVED).
2. DUPLICATE — the SAME ``event.id`` redelivered ⇒ NOOP: no second payment row, no
              double-advance. The idempotency headline (the ``stripe_events`` PK ledger).
3. FAILED   — ``payment_intent.payment_failed`` (not a fulfill type) ⇒ ACK: recorded for
              audit, never fulfilled.
4. LATE/ILLEGAL — a fulfill for a family NOT at the legal predecessor ⇒ the payment is
              recorded (the amount is a fact) but the funding state machine declines to
              advance: a fail-closed anomaly, no crash, no fabricated state.

Run (from ``backend/``):

    .venv/bin/python scripts/demo/stripe_edge_cases.py

Exits non-zero if any scenario does not behave as described (so it doubles as a smoke
check). Reset to a clean known state every run — it stands up fresh in-memory stores.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# Make ``app`` importable when run as a plain file from backend/ (scripts/demo is on
# sys.path, the backend root is not).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters.payments.simulated import SimulatedPaymentsAdapter  # noqa: E402
from app.api import deps  # noqa: E402
from app.core.program import Program  # noqa: E402
from app.data.models import FundingState, FundingType  # noqa: E402
from app.data.repository import InMemoryFamilyRepository  # noqa: E402
from app.data.synthetic import SyntheticDataset  # noqa: E402
from app.main import app  # noqa: E402
from app.observability.log_store import InMemoryObservabilityLog  # noqa: E402

# The known webhook secret the simulated adapter verifies against — the driver signs
# every event with it (offline; no live Stripe). Mirrors tests/api/test_payments_api.py.
_WEBHOOK_SECRET = "whsec_demo_stripe_edge_cases"
_PROGRAM = Program.FALL_ENROLLMENT


def _family_in_state(funding_state: FundingState, funding_type: FundingType):
    """A seeded TEFA family copied into the requested funding state."""
    base = next(
        f
        for f in InMemoryFamilyRepository.seeded().list_families()
        if f.funding_type == funding_type
    )
    return base.model_copy(update={"funding_state": funding_state})


def _sign(raw: bytes) -> str:
    """A valid ``Stripe-Signature`` header (``t=<now>,v1=<hmac>``) for ``raw``."""
    ts = int(time.time())
    signed = f"{ts}.".encode() + raw
    sig = hmac.new(_WEBHOOK_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _checkout_event(family_id: str, *, event_id: str, amount_cents: int = 261850) -> bytes:
    """A ``checkout.session.completed`` event pointing at ``family_id`` (fulfill type)."""
    event: dict[str, Any] = {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "livemode": False,
        "data": {
            "object": {
                "id": f"cs_{event_id}",
                "amount_total": amount_cents,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {"gt_family_id": family_id},
            }
        },
    }
    return json.dumps(event).encode("utf-8")


def _failed_event(family_id: str, *, event_id: str) -> bytes:
    """A ``payment_intent.payment_failed`` event (NOT a fulfill type ⇒ ACK)."""
    event: dict[str, Any] = {
        "id": event_id,
        "type": "payment_intent.payment_failed",
        "created": int(time.time()),
        "livemode": False,
        "data": {
            "object": {
                "id": f"pi_{event_id}",
                "amount": 261850,
                "currency": "usd",
                "status": "requires_payment_method",
                "metadata": {"gt_family_id": family_id},
            }
        },
    }
    return json.dumps(event).encode("utf-8")


def _post(client: TestClient, raw: bytes) -> Any:
    """POST a signed event through the real webhook endpoint and return the response."""
    return client.post(
        "/payments/webhook",
        content=raw,
        headers={"stripe-signature": _sign(raw), "content-type": "application/json"},
    )


def _line(label: str, detail: str) -> None:
    print(f"  {label:<22} {detail}")


def main() -> int:
    # Fresh in-memory stores every run ⇒ a clean known state (brief: resettable).
    adapter = SimulatedPaymentsAdapter(
        webhook_secret=_WEBHOOK_SECRET,
        tolerance_seconds=deps.get_params().stripe.tolerance_seconds,
    )
    store = deps.InMemoryPaymentsStore()
    log = InMemoryObservabilityLog()
    confirmed = _family_in_state(FundingState.GT_CONFIRMED, FundingType.TEFA_STANDARD)
    # A family at APPLIED — NOT the legal predecessor of FIRST_INSTALLMENT_RECEIVED ⇒
    # the "late/illegal" advance case. A DISTINCT family id (the seeded copies share one)
    # so it is a genuinely different household, not the confirmed one under another state.
    early = _family_in_state(FundingState.APPLIED, FundingType.TEFA_STANDARD).model_copy(
        update={"family_id": uuid4()}
    )
    repo = InMemoryFamilyRepository(SyntheticDataset(families=[confirmed, early]))

    app.dependency_overrides[deps.get_payments_adapter_dep] = lambda: adapter
    app.dependency_overrides[deps.get_payments_store] = lambda: store
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_observability_log] = lambda: log
    app.dependency_overrides[deps.get_active_program] = lambda: _PROGRAM

    failures: list[str] = []
    try:
        client = TestClient(app)

        print("\n\033[1mStripe webhook edge cases — /payments/webhook (signed)\033[0m")
        print(f"  GT-confirmed family : {confirmed.family_id}")
        print(f"  early (APPLIED) fam : {early.family_id}\n")

        # 1. NORMAL — fulfill, payment recorded, funding advance is LEGAL. The advance
        #    is observable as a CLEAN decision (no funding anomaly): GT_CONFIRMED is the
        #    one legal predecessor of FIRST_INSTALLMENT_RECEIVED, so the §5.4 gate
        #    accepts it. (The funding-state COLUMN is moved by the live voucher-timeline
        #    sink; the in-memory store has none — so we read the decision, not the field.)
        print("\033[1m1. NORMAL payment\033[0m  (checkout.session.completed)")
        r = _post(client, _checkout_event(str(confirmed.family_id), event_id="evt_normal"))
        pays = store.list_payments(_PROGRAM)
        normal_anomalies = [
            p.payload.get("funding_anomaly")
            for p in log.list_proposals()
            if p.payload.get("family_matched") and p.payload.get("funding_anomaly")
        ]
        amt = pays[-1].amount_cents if pays else "-"
        _line("HTTP", f"{r.status_code} kind={r.json().get('kind')}")
        _line("ledger", f"{len(pays)} payment row(s), amount={amt}c")
        _line(
            "funding signal",
            f"legal advance {FundingState.GT_CONFIRMED.value} -> "
            f"{FundingState.FIRST_INSTALLMENT_RECEIVED.value} accepted (no anomaly)",
        )
        if not (
            r.status_code == 200
            and r.json().get("kind") == "fulfill"
            and len(pays) == 1
            and not normal_anomalies
        ):
            failures.append("normal payment did not fulfill cleanly")

        # 2. DUPLICATE — same event id ⇒ NOOP, no second row, no double-advance.
        print("\n\033[1m2. DUPLICATE payment\033[0m  (same event.id redelivered)")
        r = _post(client, _checkout_event(str(confirmed.family_id), event_id="evt_normal"))
        pays_after = store.list_payments(_PROGRAM)
        _line("HTTP", f"{r.status_code} kind={r.json().get('kind')}")
        _line("ledger", f"{len(pays_after)} payment row(s) — unchanged (idempotent)")
        if not (r.status_code == 200 and r.json().get("kind") == "noop" and len(pays_after) == 1):
            failures.append("duplicate event was not a NOOP")

        # 3. FAILED — payment_intent.payment_failed ⇒ ACK, recorded not fulfilled.
        print("\n\033[1m3. FAILED payment\033[0m  (payment_intent.payment_failed)")
        r = _post(client, _failed_event(str(confirmed.family_id), event_id="evt_failed"))
        pays_after_fail = store.list_payments(_PROGRAM)
        _line("HTTP", f"{r.status_code} kind={r.json().get('kind')}")
        _line("ledger", f"{len(pays_after_fail)} payment row(s) — no new fulfillment")
        ack_ok = r.status_code == 200 and r.json().get("kind") == "ack"
        if not (ack_ok and len(pays_after_fail) == 1):
            failures.append("failed event was not an ACK")

        # 4. LATE / ILLEGAL — fulfill for a family not at the legal predecessor: the
        #    payment is recorded but the funding state machine declines to advance.
        print("\n\033[1m4. LATE / ILLEGAL advance\033[0m  (fulfill, family at APPLIED)")
        r = _post(client, _checkout_event(str(early.family_id), event_id="evt_late"))
        early_state = repo.get_family(early.family_id).family.funding_state
        pays_late = store.list_payments(_PROGRAM)
        anomalies = [
            p.payload.get("funding_anomaly")
            for p in log.list_proposals()
            if p.payload.get("funding_anomaly")
        ]
        _line("HTTP", f"{r.status_code} kind={r.json().get('kind')}")
        _line("ledger", f"{len(pays_late)} payment row(s) — amount still recorded (a fact)")
        _line("funding signal", f"stays {early_state.value} (illegal step refused, no crash)")
        _line("audit anomaly", anomalies[-1] if anomalies else "(none)")
        if not (
            r.status_code == 200
            and early_state == FundingState.APPLIED
            and len(pays_late) == 2
            and anomalies
        ):
            failures.append("late/illegal advance did not fail closed as expected")

        # Audit spine — every processed event left a proposal+decision pair (NFR-6).
        print(f"\n  audit log: {len(log.list_proposals())} proposal(s) recorded (NFR-6)")
    finally:
        app.dependency_overrides.clear()

    if failures:
        print("\n\033[31m✗ FAILED:\033[0m")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n\033[32m✓ all four edge cases propagated as expected\033[0m\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

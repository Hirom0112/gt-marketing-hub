"""Stripe webhook endpoint — raw-body verify → dedupe → fulfill → fast 2xx (A3).

The composition root that wires the A3 Stripe webhook behind REST. It COMPOSES
already-built, separately-tested pieces and owns none of their logic:

- the stdlib HMAC verifier (``PaymentsAdapter.verify_event`` over the RAW body —
  RESEARCH_v2 §II.2: verify BEFORE any JSON parse; simulated v1, live under
  ``STRIPE_MODE=live``, INV-9);
- the PURE deterministic decision (``app.core.payments.decide_payment_event``) — the
  FULFILL / NOOP / ACK call from the verified event + the dedupe flag (INV-2);
- the dedupe + money ledger (``app.data.payments_store.PaymentsStore``) — record each
  ``event.id`` once (idempotency) and append one ``payment`` row per fulfillment;
- the INV-10 GT funding signal: a first-installment RECEIPT advances the funding state
  one legal step via ``advance_funding_state`` + appends a ``voucher_event`` — exactly
  the ``app.api.funding`` pattern (Stripe's receipt IS the GT-controlled signal).

  ``POST /payments/webhook``
    Read the RAW request body FIRST (signature integrity); verify the
    ``Stripe-Signature`` (a forged/expired event ⇒ 400, NOT a 2xx "delivered");
    dedupe on ``event.id``; run the deterministic decision; on FULFILL record the
    payment + advance the GT funding signal; LOG the processed event (NFR-6); return a
    fast 2xx in every processed case (Stripe needs a quick ack).

This module is the composition root (CLAUDE.md §3): it MAY read the clock and make
UUIDs (the pure core may not), so ``now`` and the proposal id are computed HERE, exactly
as ``app.api.crm_sync`` / ``app.api.funding`` do. It may import ``app.core`` /
``app.data`` / ``app.observability``; ``app/core/`` stays pure.

INV-10 GUARD: the funding STATE is NEVER written from the Stripe payload directly — it
only advances through ``advance_funding_state`` (the legal §5.4 state machine). The
payment AMOUNT is recorded; the funding STATE advances only via the gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.adapters.payments.base import PaymentsAdapter, SignatureVerificationError
from app.api.deps import (
    get_active_program,
    get_observability_log,
    get_params,
    get_payments_adapter_dep,
    get_payments_store,
    get_repository,
)
from app.core.funding_gate import advance_funding_state
from app.core.params import Params
from app.core.payments import PaymentDecision, PaymentDecisionKind, decide_payment_event
from app.core.program import Program
from app.data.models import FundingState
from app.data.payments_store import PaymentsStore
from app.data.repository import FamilyRepository
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["payments"])

# Dependency aliases (Annotated keeps the Depends call in the type — ruff B008, the
# idiomatic FastAPI style matching app/api/crm_sync.py).
AdapterDep = Annotated[PaymentsAdapter, Depends(get_payments_adapter_dep)]
StoreDep = Annotated[PaymentsStore, Depends(get_payments_store)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]

# The audited §10 flow tag + schema version for one processed webhook event (the audit
# head). The webhook is automated, so the decision actor is the webhook, not a human
# operator — the deterministic core owns the write (INV-2); the LLM is nowhere near it.
WEBHOOK_FLOW = "stripe_webhook"
WEBHOOK_SCHEMA_VERSION = "1"
WEBHOOK_ACTOR = "stripe-webhook"

# The metadata/object keys (in precedence order) that carry the GT family id on a
# Checkout session. ``gt_family_id`` is the explicit metadata GT stamps; Stripe's own
# ``client_reference_id`` is the standard Checkout cross-reference (top-level on the
# session, sometimes mirrored into metadata). Absence is tolerated ⇒ no family match.
_FAMILY_ID_METADATA_KEYS = ("gt_family_id", "client_reference_id")

# The GT-controlled signal name the Stripe first-installment receipt asserts (mirrors
# app.api.funding's _SIGNAL_TO_EVENT first-installment field), and the default voucher
# program a family maps to (the synthetic cohort is Texas — app.api.funding's default).
_FIRST_INSTALLMENT_SIGNAL = "first_installment_received"
_DEFAULT_VOUCHER_PROGRAM = "tx_tefa"


class WebhookAck(BaseModel):
    """The fast acknowledgement Stripe receives (A3).

    Stripe only needs a 2xx; the body is for observability. ``received`` is always
    ``True`` on a processed event; ``kind`` echoes the deterministic decision
    (``fulfill`` / ``noop`` / ``ack``).
    """

    received: bool
    kind: str


def _resolve_family(repository: FamilyRepository, decision_object: dict[str, Any]) -> UUID | None:
    """Resolve the GT family id from the verified Checkout object, or ``None`` (A3).

    Prefers an explicit metadata key (``gt_family_id`` / ``client_reference_id``), then
    Stripe's top-level ``client_reference_id``. A malformed/absent id, or one that
    matches no local family, yields ``None`` — a payment may legitimately land before
    the family is matched (the 0026 nullable FK shape), and matching nothing is never
    fabricated into a fake family.
    """
    metadata = decision_object.get("metadata")
    candidates: list[Any] = []
    if isinstance(metadata, dict):
        candidates.extend(metadata.get(key) for key in _FAMILY_ID_METADATA_KEYS)
    candidates.append(decision_object.get("client_reference_id"))

    for raw in candidates:
        if not raw:
            continue
        try:
            family_id = UUID(str(raw))
        except ValueError:
            continue
        if repository.get_family(family_id) is not None:
            return family_id
    return None


def _advance_funding(
    repository: FamilyRepository,
    *,
    family_id: UUID,
) -> str | None:
    """Advance the matched family's GT funding signal one legal step (INV-10; A3).

    Stripe's first-installment RECEIPT IS the GT-controlled signal (INV-10), so we
    advance ``current → FIRST_INSTALLMENT_RECEIVED`` through the legal §5.4 state
    machine and append a ``voucher_event`` — mirroring ``app.api.funding``. The funding
    STATE is NEVER written from the payload; it only advances via the gate.

    An ILLEGAL transition (the family is not at the one legal predecessor) raises
    ``ValueError``; we catch it, write NO voucher_event (fail-closed, no re-write loop),
    and return a short anomaly note for the audit — the payment is real even if the
    state machine rejects the step, so the webhook never 500s on it.

    Returns ``None`` on a clean advance, or an anomaly note string on an illegal one.
    """
    joined = repository.get_family(family_id)
    if joined is None:  # pragma: no cover — caller only passes matched ids.
        return None
    current = joined.family.funding_state
    try:
        advanced = advance_funding_state(current, FundingState.FIRST_INSTALLMENT_RECEIVED)
    except ValueError as exc:
        # Illegal §5.4 step (the family is not at GT_CONFIRMED): the payment stands,
        # but the funding state machine declines to advance. Fail closed — no
        # voucher_event, no 500 — and surface the anomaly to the audit.
        return f"funding advance rejected: {exc}"

    # Append the transition to the append-only voucher_event timeline, if the bound
    # store supports it (the in-memory v1 store has no timeline sink ⇒ safe no-op).
    # Mirrors app.api.funding._append_voucher_event — the writer is a live-store
    # concern, so core/routers stay on the FamilyRepository interface (NFR-8).
    append = getattr(repository, "append_voucher_event", None)
    if append is not None:
        append(
            family_id=family_id,
            from_state=current,
            to_state=advanced,
            program=_DEFAULT_VOUCHER_PROGRAM,
            signal=_FIRST_INSTALLMENT_SIGNAL,
        )
    return None


def _log_event(
    log: ObservabilityLog,
    *,
    decision: PaymentDecision,
    family_id: UUID | None,
    anomaly: str | None,
) -> None:
    """Log one processed webhook event to the §10 audit spine (NFR-6).

    A proposal + an automated decision (actor ``stripe-webhook``), mirroring the A2
    poll's audit pair (``app.api.crm_sync``). The payload carries the decision kind +
    the money fields + an optional funding anomaly so the audit records exactly what was
    processed and any state-machine rejection.
    """
    proposal_id = uuid4()
    payload: dict[str, object] = {
        "kind": decision.kind.value,
        "event_id": decision.event_id,
        "event_type": decision.event_type,
        "object_id": decision.object_id,
        "amount_cents": decision.amount_cents,
        "currency": decision.currency,
        "status": decision.status,
        "family_matched": family_id is not None,
    }
    if anomaly is not None:
        payload["funding_anomaly"] = anomaly
    log.log_proposal(
        proposal_id=proposal_id,
        flow=WEBHOOK_FLOW,
        schema_version=WEBHOOK_SCHEMA_VERSION,
        payload=payload,
        family_id=family_id,
    )
    log.log_decision(
        proposal_id=proposal_id,
        human=WEBHOOK_ACTOR,
        action=DecisionAction.APPROVE,
    )


@router.post("/payments/webhook", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    adapter: AdapterDep,
    store: StoreDep,
    repository: RepositoryDep,
    log: LogDep,
    program: ProgramDep,
    params: ParamsDep,
) -> WebhookAck:
    """Verify → dedupe → decide → fulfill → fast 2xx (A3; RESEARCH_v2 §II.2; INV-10).

    1. Read the RAW body FIRST (before any JSON parse) — signature integrity.
    2. Verify the ``Stripe-Signature`` against the injected secret + the params
       tolerance; a forged/expired event ⇒ 400 (NOT a 2xx — Stripe must not treat it
       as delivered).
    3. Dedupe on ``event.id`` and run the deterministic decision.
    4. NOOP (replay) ⇒ do nothing, fast 200 (idempotent — the headline property).
       ACK (new non-fulfill) ⇒ record the event for future dedupe, 200.
       FULFILL (new fulfill type) ⇒ record the event; record the payment; advance the
       GT funding signal IF a family matched (an illegal §5.4 step is caught, the
       payment stands, no 500); LOG it; 200.
    """
    # 1. RAW body first — verified BEFORE any JSON parse (signature integrity).
    raw = await request.body()
    sig_header = request.headers.get("stripe-signature") or ""

    # 2. Verify the signature over the raw bytes (the adapter holds the secret +
    # tolerance; the simulated v1 adapter verifies offline — INV-9). A forged or
    # expired event is a hard 400, never a 2xx "delivered".
    now = int(datetime.now(UTC).timestamp())
    try:
        event = adapter.verify_event(raw, sig_header, now=now)
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 3. Dedupe on event.id, then the pure deterministic decision (INV-2). The
    # fulfill set is read from params (INV-11).
    event_id = str(event.get("id") or "")
    seen = store.is_event_seen(program, event_id)
    decision = decide_payment_event(
        event,
        already_seen=seen,
        fulfill_event_types=params.stripe.fulfill_event_types,
    )

    # 4a. NOOP — an already-seen redelivery: do nothing, fast 200 (no double-record /
    # no double-fulfill — the idempotency headline). Nothing is logged again.
    if decision.kind is PaymentDecisionKind.NOOP:
        return WebhookAck(received=True, kind=decision.kind.value)

    # Both ACK and FULFILL are new events ⇒ record the id so a later redelivery dedupes.
    store.record_event(program, decision.event_id, decision.event_type, decision.object_id)

    # 4b. ACK — a new, non-fulfill event: recorded for audit, not fulfilled.
    if decision.kind is PaymentDecisionKind.ACK:
        _log_event(log, decision=decision, family_id=None, anomaly=None)
        return WebhookAck(received=True, kind=decision.kind.value)

    # 4c. FULFILL — a new event of a configured fulfill type. Resolve the family,
    # record the payment (amount is the FACT), then advance the GT funding signal.
    decision_object = event.get("data", {})
    if isinstance(decision_object, dict):
        decision_object = decision_object.get("object", {})
    family_id = _resolve_family(
        repository, decision_object if isinstance(decision_object, dict) else {}
    )

    store.record_payment(
        program,
        family_id=family_id,
        event_id=decision.event_id,
        # Defensive fallbacks: a fulfill event SHOULD carry these, but a malformed one
        # must not crash the webhook — the amount is recorded as a fact, never fabricated.
        amount_cents=decision.amount_cents if decision.amount_cents is not None else 0,
        currency=decision.currency or "",
        status=decision.status or "",
    )

    # INV-10: advance the funding STATE only through the legal §5.4 gate, never from the
    # payload. An illegal step is caught — the payment stands, no 500.
    anomaly: str | None = None
    if family_id is not None:
        anomaly = _advance_funding(repository, family_id=family_id)

    _log_event(log, decision=decision, family_id=family_id, anomaly=anomaly)
    return WebhookAck(received=True, kind=decision.kind.value)

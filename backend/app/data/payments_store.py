"""Stripe dedupe + payment-ledger store — the A3 webhook data seam.

The Stripe webhook needs TWO durable, program-scoped facts (migration 0026):

1. an **idempotency ledger** over inbound events — record each ``event_id`` it
   has processed and refuse to reprocess a logged one (RESEARCH_v2 §II.2: "log
   the event IDs you've processed, don't reprocess logged events"). Stripe
   delivers at-least-once, so a redelivered ``event_id`` is EXPECTED — recording
   it twice must be a safe no-op, never an error.
2. a **money ledger** — append one ``payment`` row per fulfilled payment.

Both map onto the 0026 append-only, program-scoped tables (``stripe_events`` /
``payment``) and follow the NFR-8 store-seam shape of
:class:`app.data.watermark_store.WatermarkStore`:

- :class:`PaymentsStore` — the ABC the webhook depends on.
- :class:`InMemoryPaymentsStore` — the v1 / CI-tested local impl (pure, no I/O).
- :class:`SupabasePaymentsStore` — the live impl over PostgREST via the SAME
  service_role pattern as :class:`app.data.supabase_repository.SupabaseFamilyRepository`
  (exercised only against a real DB).

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only
the pure :class:`app.core.program.Program` enum and ``httpx`` (the house
transport, already a runtime dep).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a
# third party's URLs, the same carve-out the family/watermark stores make). The
# 0026 table names.
_REST = "/rest/v1"
_STRIPE_EVENTS_TABLE = f"{_REST}/stripe_events"
_PAYMENT_TABLE = f"{_REST}/payment"


@dataclass(frozen=True)
class PaymentRow:
    """One appended ``payment`` ledger row (the in-memory read accessor's shape).

    A faithful subset of the 0026 ``payment`` columns the webhook writes:
    ``amount_cents`` in the currency's minor unit, ``family_id`` nullable (a
    payment may land before the family is matched — the 0026 nullable FK shape).
    """

    family_id: UUID | None
    event_id: str
    amount_cents: int
    currency: str
    status: str


class PaymentsStore(ABC):
    """Read/write seam over the Stripe dedupe + payment ledgers (A3; migration 0026).

    The webhook depends on this interface, never a concrete store. v1 binds the
    in-memory impl; production swaps the Supabase-backed one with zero caller
    changes (the NFR-8 store-seam pattern).
    """

    @abstractmethod
    def is_event_seen(self, program: Program, event_id: str) -> bool:
        """Has ``event_id`` already been recorded for ``program``? (the dedupe check).

        The webhook calls this BEFORE processing an inbound event; ``True`` means
        a duplicate redelivery to skip (exactly-once processing).
        """
        raise NotImplementedError

    @abstractmethod
    def record_event(
        self, program: Program, event_id: str, event_type: str, object_id: str | None
    ) -> None:
        """Append an inbound event to the ``stripe_events`` idempotency ledger.

        INSERT-only (the table is append-only). Recording an already-present
        ``event_id`` is a safe, idempotent NO-OP — the PK collision is EXPECTED
        under Stripe's at-least-once delivery and is treated as "already seen",
        never an error.
        """
        raise NotImplementedError

    @abstractmethod
    def record_payment(
        self,
        program: Program,
        *,
        family_id: UUID | None,
        event_id: str,
        amount_cents: int,
        currency: str,
        status: str,
    ) -> None:
        """Append one fulfilled-payment row to the ``payment`` money ledger.

        Append-only: a payment is a fact, never updated after insert.
        ``family_id`` is nullable (a payment may land before the family is
        matched — the 0026 nullable FK shape).
        """
        raise NotImplementedError


class InMemoryPaymentsStore(PaymentsStore):
    """In-memory :class:`PaymentsStore` — per-program sets/lists; no credential, no I/O.

    The v1 local store (A3) and the CI-tested path. Events are deduped in a set
    keyed ``(program, event_id)``; payments accumulate in a per-program list. A
    production deploy swaps :class:`SupabasePaymentsStore` behind the same seam.
    """

    def __init__(self) -> None:
        self._seen_events: set[tuple[Program, str]] = set()
        self._payments: dict[Program, list[PaymentRow]] = {}

    def is_event_seen(self, program: Program, event_id: str) -> bool:
        return (program, event_id) in self._seen_events

    def record_event(
        self, program: Program, event_id: str, event_type: str, object_id: str | None
    ) -> None:
        # A set add is intrinsically idempotent: recording the same id twice is a
        # no-op (the at-least-once-delivery contract). event_type/object_id are not
        # needed for the dedupe decision, so the in-memory ledger keeps only the key.
        self._seen_events.add((program, event_id))

    def record_payment(
        self,
        program: Program,
        *,
        family_id: UUID | None,
        event_id: str,
        amount_cents: int,
        currency: str,
        status: str,
    ) -> None:
        self._payments.setdefault(program, []).append(
            PaymentRow(
                family_id=family_id,
                event_id=event_id,
                amount_cents=amount_cents,
                currency=currency,
                status=status,
            )
        )

    def list_payments(self, program: Program) -> list[PaymentRow]:
        """The payment ledger for ``program`` (the in-memory read accessor for tests)."""
        return list(self._payments.get(program, []))


class SupabasePaymentsStore(PaymentsStore):
    """Live :class:`PaymentsStore` over Supabase PostgREST (service_role; 0026).

    Query-per-request (the stateless-runtime posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call
    issues a fresh PostgREST request over the injected (or per-call) ``httpx``
    client. Both tables are program-scoped — ``program_id`` is the 0026 tenancy
    tag — so every read filters and every write stamps it. The ``service_role``
    key BYPASSES RLS (server-only — INV-5 / D-RLS-4) and never leaves the backend.

    Args:
        base_url: The Supabase project URL (``https://<ref>.supabase.co``).
        service_role_key: The server-only service_role JWT (BYPASSRLS).
        client: An optional injected ``httpx.Client`` (tests pass one wired to a
            ``MockTransport``); when omitted each request opens a short-lived client.
        timeout: Per-request timeout seconds (a fixed transport setting).
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_role_key: str,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._client = client
        self._timeout = timeout

    # ------------------------------------------------------------------ I/O
    def _headers(self) -> dict[str, str]:
        """service_role auth on every request (apikey + Bearer)."""
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """One PostgREST GET → the decoded JSON array (fail loud on non-2xx)."""
        url = f"{self._base_url}{path}"
        headers = self._headers()
        if self._client is not None:
            response = self._client.get(url, params=params, headers=headers)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST GET {path} → {response.status_code}: {response.text[:300]}"
            )
        body: Any = response.json()
        if not isinstance(body, list):
            raise SupabaseError(f"PostgREST GET {path} returned a non-array body")
        return body

    def _post(self, path: str, payload: dict[str, Any], *, prefer: str = "return=minimal") -> None:
        """One PostgREST POST (the append-only insert; fail loud on non-2xx).

        ``prefer`` carries the ``Prefer`` header — ``record_event`` adds
        ``resolution=ignore-duplicates`` so a PK conflict on a redelivered
        ``event_id`` is tolerated (treated as "already seen", per the at-least-once
        contract) rather than surfaced as a 409.
        """
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": prefer,
        }
        if self._client is not None:
            response = self._client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST POST {path} → {response.status_code}: {response.text[:300]}"
            )

    # ---------------------------------------------------------------- interface
    def is_event_seen(self, program: Program, event_id: str) -> bool:
        rows = self._get(
            _STRIPE_EVENTS_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "event_id": f"eq.{event_id}",
                "select": "event_id",
            },
        )
        return bool(rows)

    def record_event(
        self, program: Program, event_id: str, event_type: str, object_id: str | None
    ) -> None:
        # INSERT-only into the append-only dedupe ledger, program-stamped. A
        # redelivered event_id collides on the `event_id` PK; `resolution=ignore-
        # duplicates` makes PostgREST treat that as a no-op (200), so an at-least-
        # once redelivery is tolerated as "already seen" rather than a 409 error.
        self._post(
            _STRIPE_EVENTS_TABLE,
            {
                "event_id": event_id,
                "event_type": event_type,
                "object_id": object_id,
                "program_id": program.value,
            },
            prefer="return=minimal,resolution=ignore-duplicates",
        )

    def record_payment(
        self,
        program: Program,
        *,
        family_id: UUID | None,
        event_id: str,
        amount_cents: int,
        currency: str,
        status: str,
    ) -> None:
        # Append one row to the immutable payment money ledger, program-stamped.
        self._post(
            _PAYMENT_TABLE,
            {
                "family_id": str(family_id) if family_id is not None else None,
                "event_id": event_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "status": status,
                "program_id": program.value,
            },
        )


def build_supabase_payments_store() -> SupabasePaymentsStore | None:
    """Construct the Supabase payments store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.watermark_store.build_supabase_watermark_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a
    placeholder ``<...>`` sentinel — so the caller falls back to the in-memory store
    (A3). No program is threaded in: the store is constructed program-agnostic and
    bounded per call by the ``program`` argument each method takes.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabasePaymentsStore(base_url=url, service_role_key=key)

"""Decision-Queue store — the B2 cross-module human-decision data seam.

The Decision Queue lets ANY module flag an item for a human (``submit``), a leader
read the open pile (``list_open``), and a leader act on one (``record_action``,
which appends an immutable ``decision_event`` AND advances the ``decision.state``).
This module is the NFR-8 store seam for that state — the same shape as
:class:`app.data.watermark_store.WatermarkStore` and
:class:`app.data.payments_store.PaymentsStore`:

- :class:`DecisionsStore` — the ABC every decision route depends on.
- :class:`InMemoryDecisionsStore` — the v1 / CI-tested local impl (pure, no I/O).
- :class:`SupabaseDecisionsStore` — the live impl over the 0028 ``decision`` /
  ``decision_event`` tables, via the SAME PostgREST/service_role pattern as the
  family/watermark/payments stores (exercised only against a real DB).

The store is deliberately dumb: it stores and returns what it is handed. The state
machine (``app.core.decision_queue.apply_action``) is the CALLER's — the route
computes ``new_state`` and hands it here; the store does not re-derive transitions.

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only
the pure :class:`app.core.program.Program` enum, the
:class:`app.core.decision_queue` action/state enums, and ``httpx`` (the house
transport, already a runtime dep).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.decision_queue import DecisionAction, DecisionState
from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the family/watermark/payments stores make). The
# 0028 table names.
_REST = "/rest/v1"
_DECISION_TABLE = f"{_REST}/decision"
_DECISION_EVENT_TABLE = f"{_REST}/decision_event"


@dataclass(frozen=True)
class Decision:
    """One row of the Decision Queue (the in-memory/read accessor's shape).

    A faithful subset of the 0028 ``decision`` columns the queue needs: ``source``
    (which module flagged it), ``payload`` (the PII-free jsonb context, INV-1),
    ``state`` (open / decided / in_flight), and the create stamp. Frozen — a state
    change replaces the row, never mutates it (the append-only/audit posture).
    """

    id: UUID
    source: str
    payload: dict[str, Any]
    state: DecisionState
    created_at: datetime


class DecisionsStore(ABC):
    """Read/write seam over the B2 Decision Queue (migration 0028).

    Every decision route depends on this interface, never a concrete store. v1
    binds the in-memory impl; production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped
    (the 0028 tenancy tag) so one program's queue never bleeds into another's.
    """

    @abstractmethod
    def submit(self, program: Program, *, source: str, payload: dict[str, Any]) -> Decision:
        """Insert an OPEN decision (the "anyone/any-module submits" path).

        Returns the created :class:`Decision` (a fresh id + create stamp, state
        ``open``). Open to any authenticated principal at the route layer — this
        store method does not authorize.
        """
        raise NotImplementedError

    @abstractmethod
    def list_open(self, program: Program) -> list[Decision]:
        """The OPEN decisions for ``program``, in submit order (the queue)."""
        raise NotImplementedError

    @abstractmethod
    def list_all(self, program: Program) -> list[Decision]:
        """Every decision for ``program`` (open + decided + in_flight), in submit order."""
        raise NotImplementedError

    @abstractmethod
    def get(self, program: Program, decision_id: UUID) -> Decision | None:
        """The decision ``decision_id`` for ``program``, or ``None`` if absent (a clean miss)."""
        raise NotImplementedError

    @abstractmethod
    def record_action(
        self,
        program: Program,
        decision_id: UUID,
        *,
        action: DecisionAction,
        comment: str | None,
        actor: str,
        new_state: DecisionState,
    ) -> None:
        """Append an immutable ``decision_event`` AND advance the decision's ``state``.

        One human action = one appended event (``action`` + optional ``comment`` +
        the verified ``actor``) plus the resulting ``new_state`` written onto the
        decision row. ``new_state`` is computed by the caller via
        :func:`app.core.decision_queue.apply_action` — the store does not re-derive
        the transition.
        """
        raise NotImplementedError


class InMemoryDecisionsStore(DecisionsStore):
    """In-memory :class:`DecisionsStore` — per-program dicts; no credential, no I/O.

    The v1 local store (A-3) and the CI-tested path. Decisions live in a per-program
    insertion-ordered dict keyed by id; events accumulate in a per-program list. A
    production deploy swaps :class:`SupabaseDecisionsStore` behind the same seam.
    """

    @dataclass(frozen=True)
    class _Event:
        decision_id: UUID
        action: DecisionAction
        comment: str | None
        actor: str
        created_at: datetime

    def __init__(self) -> None:
        # Insertion-ordered per-program decision index (dict preserves order).
        self._decisions: dict[Program, dict[UUID, Decision]] = {}
        # Append-only per-program event audit (mirrors the 0028 decision_event table).
        self._events: dict[Program, list[InMemoryDecisionsStore._Event]] = {}

    def submit(self, program: Program, *, source: str, payload: dict[str, Any]) -> Decision:
        decision = Decision(
            id=uuid4(),
            source=source,
            payload=dict(payload),
            state=DecisionState.OPEN,
            created_at=datetime.now(UTC),
        )
        self._decisions.setdefault(program, {})[decision.id] = decision
        return decision

    def list_open(self, program: Program) -> list[Decision]:
        return [
            d for d in self._decisions.get(program, {}).values() if d.state is DecisionState.OPEN
        ]

    def list_all(self, program: Program) -> list[Decision]:
        return list(self._decisions.get(program, {}).values())

    def get(self, program: Program, decision_id: UUID) -> Decision | None:
        return self._decisions.get(program, {}).get(decision_id)

    def record_action(
        self,
        program: Program,
        decision_id: UUID,
        *,
        action: DecisionAction,
        comment: str | None,
        actor: str,
        new_state: DecisionState,
    ) -> None:
        decisions = self._decisions.get(program, {})
        current = decisions.get(decision_id)
        if current is None:
            # The caller loaded-then-acted; a missing row here is a programming error.
            raise KeyError(f"unknown decision_id (never submitted): {decision_id}")
        self._events.setdefault(program, []).append(
            InMemoryDecisionsStore._Event(
                decision_id=decision_id,
                action=action,
                comment=comment,
                actor=actor,
                created_at=datetime.now(UTC),
            )
        )
        # Frozen dataclass ⇒ a state change replaces the row (never mutates it).
        decisions[decision_id] = Decision(
            id=current.id,
            source=current.source,
            payload=current.payload,
            state=new_state,
            created_at=current.created_at,
        )

    def list_events(self, program: Program, decision_id: UUID) -> list[_Event]:
        """This decision's appended events, in append order (the in-memory read accessor)."""
        return [e for e in self._events.get(program, []) if e.decision_id == decision_id]


class SupabaseDecisionsStore(DecisionsStore):
    """Live :class:`DecisionsStore` over Supabase PostgREST (service_role; 0028).

    Query-per-request (the stateless-runtime posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call issues
    a fresh PostgREST request over the injected (or per-call) ``httpx`` client. Both
    tables are program-scoped — ``program_id`` is the 0028 tenancy tag — so every
    read filters and every write stamps it. The ``service_role`` key BYPASSES RLS
    (server-only — INV-5 / D-RLS-4) and never leaves the backend.

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

    def _post(
        self, path: str, payload: dict[str, Any], *, prefer: str = "return=minimal"
    ) -> list[dict[str, Any]]:
        """One PostgREST POST → the decoded body (``[]`` for return=minimal); fail loud."""
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
        if "return=representation" not in prefer:
            return []
        body: Any = response.json()
        if not isinstance(body, list):
            raise SupabaseError(f"PostgREST POST {path} returned a non-array body")
        return body

    def _patch(self, path: str, params: dict[str, str], payload: dict[str, Any]) -> None:
        """One PostgREST PATCH (the state-advance write; fail loud on non-2xx)."""
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        if self._client is not None:
            response = self._client.patch(url, params=params, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.patch(url, params=params, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST PATCH {path} → {response.status_code}: {response.text[:300]}"
            )

    # ---------------------------------------------------------------- interface
    def submit(self, program: Program, *, source: str, payload: dict[str, Any]) -> Decision:
        rows = self._post(
            _DECISION_TABLE,
            {
                "source": source,
                "payload": payload,
                "state": DecisionState.OPEN.value,
                "program_id": program.value,
            },
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /decision returned no representation row")
        return _row_to_decision(rows[0])

    def list_open(self, program: Program) -> list[Decision]:
        rows = self._get(
            _DECISION_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "state": f"eq.{DecisionState.OPEN.value}",
                "select": "id,source,payload,state,created_at",
                "order": "created_at.asc",
            },
        )
        return [_row_to_decision(row) for row in rows]

    def list_all(self, program: Program) -> list[Decision]:
        rows = self._get(
            _DECISION_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": "id,source,payload,state,created_at",
                "order": "created_at.asc",
            },
        )
        return [_row_to_decision(row) for row in rows]

    def get(self, program: Program, decision_id: UUID) -> Decision | None:
        rows = self._get(
            _DECISION_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "id": f"eq.{decision_id}",
                "select": "id,source,payload,state,created_at",
            },
        )
        if not rows:
            return None
        return _row_to_decision(rows[0])

    def record_action(
        self,
        program: Program,
        decision_id: UUID,
        *,
        action: DecisionAction,
        comment: str | None,
        actor: str,
        new_state: DecisionState,
    ) -> None:
        # Append the immutable audit event first (append-only, like the 0010/0015
        # posture), THEN advance the decision's state — both program-stamped.
        self._post(
            _DECISION_EVENT_TABLE,
            {
                "decision_id": str(decision_id),
                "action": action.value,
                "comment": comment,
                "actor": actor,
                "program_id": program.value,
            },
        )
        self._patch(
            _DECISION_TABLE,
            {"program_id": f"eq.{program.value}", "id": f"eq.{decision_id}"},
            {"state": new_state.value},
        )


def _row_to_decision(row: dict[str, Any]) -> Decision:
    """Map a PostgREST ``decision`` row to the :class:`Decision` accessor shape."""
    payload = row.get("payload")
    return Decision(
        id=UUID(str(row["id"])),
        source=str(row["source"]),
        payload=payload if isinstance(payload, dict) else {},
        state=DecisionState(str(row["state"])),
        created_at=_parse_timestamp(row.get("created_at")) or datetime.now(UTC),
    )


def _parse_timestamp(raw: object) -> datetime | None:
    """Parse a PostgREST ``timestamptz`` to a datetime (tolerant of ``Z``)."""
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_supabase_decisions_store() -> SupabaseDecisionsStore | None:
    """Construct the Supabase decisions store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.payments_store.build_supabase_payments_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a
    placeholder ``<...>`` sentinel — so the caller falls back to the in-memory store
    (A-3). No program is threaded in: the store is constructed program-agnostic and
    bounded per call by the ``program`` argument each method takes.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseDecisionsStore(base_url=url, service_role_key=key)

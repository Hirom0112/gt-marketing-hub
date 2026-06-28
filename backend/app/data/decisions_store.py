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
from datetime import UTC, date, datetime
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

# The PostgREST select list for a decision row — the 0028 columns + the 0034
# first-class spec-fields. One home so every read pulls the same shape.
_DECISION_SELECT = (
    "id,source,payload,state,created_at,question,raised_by,workstream,"
    "recommendation,budget_ask,due_date,priority,resolution_date"
)

# --------------------------------------------------------------------------- #
# Module 11 raise vocabulary — the ONE canonical home (INV-11) for the valid
# workstream + priority sets a structured raise is validated against. Named, not
# bare literals: the route imports these to reject an unknown workstream / priority
# (a clean 422), and the migration's CHECK mirrors PRIORITIES in the DB backstop.
# --------------------------------------------------------------------------- #

# The workstreams a decision may belong to (the spec's Module-11 lanes).
WORKSTREAMS: tuple[str, ...] = (
    "content",
    "grassroots",
    "field_events",
    "budget",
    "admissions",
    "nurture",
)

# The two priorities. ``normal`` is the default (matches the 0034 column default).
PRIORITY_URGENT = "urgent"
PRIORITY_NORMAL = "normal"
PRIORITIES: tuple[str, ...] = (PRIORITY_URGENT, PRIORITY_NORMAL)


@dataclass(frozen=True)
class Decision:
    """One row of the Decision Queue (the in-memory/read accessor's shape).

    The 0028 ``decision`` columns the queue needs — ``source`` (which module flagged
    it), ``payload`` (the PII-free jsonb context, INV-1), ``state`` (open / decided /
    in_flight), the create stamp — PLUS the 0034 first-class spec-fields a structured
    raise carries: ``question`` (the decision's name), ``raised_by`` (the VERIFIED
    principal's uid/role token, never a client name; INV-1), ``workstream``,
    ``recommendation``, an optional ``budget_ask`` and ``due_date``, the ``priority``,
    and ``resolution_date`` (set when the decision first leaves OPEN). Frozen — a
    state change replaces the row, never mutates it (the append-only/audit posture).

    Auto-flag sources (budget variance, open-data enrichment) leave the structured
    fields at their defaults and carry context in ``payload`` instead; the display
    helpers (:meth:`display_question` / :meth:`display_workstream`) derive a sensible
    label from ``payload`` for those rows so the UI never shows a blank.
    """

    id: UUID
    source: str
    payload: dict[str, Any]
    state: DecisionState
    created_at: datetime
    # 0034 first-class spec-fields (safe defaults ⇒ existing/auto-flag rows valid).
    question: str = ""
    raised_by: str = ""
    workstream: str = ""
    recommendation: str = ""
    budget_ask: float | None = None
    due_date: date | None = None
    priority: str = PRIORITY_NORMAL
    resolution_date: datetime | None = None

    def display_question(self) -> str:
        """The question to show — the structured field, else derived from ``payload``.

        A manual raise sets ``question`` directly. An auto-flag row leaves it blank,
        so we fall back to a ``payload['question']`` if present (graceful — never a
        blank label for the leader).
        """
        if self.question:
            return self.question
        payload_question = self.payload.get("question")
        return str(payload_question) if payload_question else ""

    def display_workstream(self) -> str:
        """The workstream to show — the structured field, else from ``payload``.

        Budget-variance auto-flags carry their workstream in ``payload['workstream']``
        (the existing shape), so a payload-only row still renders a workstream.
        """
        if self.workstream:
            return self.workstream
        payload_workstream = self.payload.get("workstream")
        return str(payload_workstream) if payload_workstream else ""


class DecisionsStore(ABC):
    """Read/write seam over the B2 Decision Queue (migration 0028).

    Every decision route depends on this interface, never a concrete store. v1
    binds the in-memory impl; production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped
    (the 0028 tenancy tag) so one program's queue never bleeds into another's.
    """

    @abstractmethod
    def submit(
        self,
        program: Program,
        *,
        source: str,
        payload: dict[str, Any],
        question: str = "",
        raised_by: str = "",
        workstream: str = "",
        recommendation: str = "",
        budget_ask: float | None = None,
        due_date: date | None = None,
        priority: str = PRIORITY_NORMAL,
    ) -> Decision:
        """Insert an OPEN decision (the "anyone/any-module submits" path).

        Returns the created :class:`Decision` (a fresh id + create stamp, state
        ``open``). Open to any authenticated principal at the route layer — this
        store method does not authorize. The 0034 spec-fields are KEYWORD-OPTIONAL so
        an auto-flag feeder (``flag_decision``) submits with only ``source`` +
        ``payload`` unchanged, while a manual raise threads the structured fields.
        ``raised_by`` is the VERIFIED principal's token — the route stamps it; the
        store never derives or trusts a client name (INV-1).
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
        the transition. When ``new_state`` is no longer OPEN and ``resolution_date``
        is still unset, the store stamps it with the deciding instant.
        """
        raise NotImplementedError

    @abstractmethod
    def latest_comment(self, program: Program, decision_id: UUID) -> str | None:
        """The most recent action ``comment`` for ``decision_id``, or ``None``.

        Powers the operator-visible ``GET /decisions/mine`` outcome ("what did the
        leader say"). Returns the comment on the latest appended ``decision_event``
        (which may itself be ``None`` for a no-comment approve/reject), or ``None``
        when the decision has no recorded action yet.
        """
        raise NotImplementedError

    @abstractmethod
    def latest_action(self, program: Program, decision_id: UUID) -> DecisionAction | None:
        """The most recent action VERDICT for ``decision_id``, or ``None``.

        Returns the ``action`` (``approve``/``reject``/``need_info``) on the latest
        appended ``decision_event``, or ``None`` when no action is recorded yet. Powers
        the history outcome filter and the approved-vs-rejected resolution feedback (so
        a decided row reads "approved"/"rejected", not a flat "resolved").
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

    def submit(
        self,
        program: Program,
        *,
        source: str,
        payload: dict[str, Any],
        question: str = "",
        raised_by: str = "",
        workstream: str = "",
        recommendation: str = "",
        budget_ask: float | None = None,
        due_date: date | None = None,
        priority: str = PRIORITY_NORMAL,
    ) -> Decision:
        decision = Decision(
            id=uuid4(),
            source=source,
            payload=dict(payload),
            state=DecisionState.OPEN,
            created_at=datetime.now(UTC),
            question=question,
            raised_by=raised_by,
            workstream=workstream,
            recommendation=recommendation,
            budget_ask=budget_ask,
            due_date=due_date,
            priority=priority,
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
        # When the decision first LEAVES open, stamp the resolution instant (the
        # deciding moment); a later DECIDED→IN_FLIGHT keeps the original stamp.
        resolution_date = current.resolution_date
        if new_state is not DecisionState.OPEN and resolution_date is None:
            resolution_date = datetime.now(UTC)
        # Frozen dataclass ⇒ a state change replaces the row (never mutates it).
        decisions[decision_id] = Decision(
            id=current.id,
            source=current.source,
            payload=current.payload,
            state=new_state,
            created_at=current.created_at,
            question=current.question,
            raised_by=current.raised_by,
            workstream=current.workstream,
            recommendation=current.recommendation,
            budget_ask=current.budget_ask,
            due_date=current.due_date,
            priority=current.priority,
            resolution_date=resolution_date,
        )

    def list_events(self, program: Program, decision_id: UUID) -> list[_Event]:
        """This decision's appended events, in append order (the in-memory read accessor)."""
        return [e for e in self._events.get(program, []) if e.decision_id == decision_id]

    def latest_comment(self, program: Program, decision_id: UUID) -> str | None:
        events = self.list_events(program, decision_id)
        return events[-1].comment if events else None

    def latest_action(self, program: Program, decision_id: UUID) -> DecisionAction | None:
        events = self.list_events(program, decision_id)
        return events[-1].action if events else None


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
    def submit(
        self,
        program: Program,
        *,
        source: str,
        payload: dict[str, Any],
        question: str = "",
        raised_by: str = "",
        workstream: str = "",
        recommendation: str = "",
        budget_ask: float | None = None,
        due_date: date | None = None,
        priority: str = PRIORITY_NORMAL,
    ) -> Decision:
        rows = self._post(
            _DECISION_TABLE,
            {
                "source": source,
                "payload": payload,
                "state": DecisionState.OPEN.value,
                "program_id": program.value,
                "question": question,
                "raised_by": raised_by,
                "workstream": workstream,
                "recommendation": recommendation,
                "budget_ask": budget_ask,
                "due_date": due_date.isoformat() if due_date is not None else None,
                "priority": priority,
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
                "select": _DECISION_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_decision(row) for row in rows]

    def list_all(self, program: Program) -> list[Decision]:
        rows = self._get(
            _DECISION_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": _DECISION_SELECT,
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
                "select": _DECISION_SELECT,
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
        # Stamp the resolution instant the FIRST time the decision leaves open. The
        # extra ``resolution_date=is.null`` filter makes this idempotent: a later
        # DECIDED→IN_FLIGHT (resolution already set) matches no row and is a no-op,
        # so the original deciding instant is preserved.
        if new_state is not DecisionState.OPEN:
            self._patch(
                _DECISION_TABLE,
                {
                    "program_id": f"eq.{program.value}",
                    "id": f"eq.{decision_id}",
                    "resolution_date": "is.null",
                },
                {"resolution_date": datetime.now(UTC).isoformat()},
            )

    def latest_comment(self, program: Program, decision_id: UUID) -> str | None:
        rows = self._get(
            _DECISION_EVENT_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "decision_id": f"eq.{decision_id}",
                "select": "comment,created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        comment = rows[0].get("comment")
        return str(comment) if comment is not None else None

    def latest_action(self, program: Program, decision_id: UUID) -> DecisionAction | None:
        rows = self._get(
            _DECISION_EVENT_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "decision_id": f"eq.{decision_id}",
                "select": "action,created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        action = rows[0].get("action")
        return DecisionAction(str(action)) if action is not None else None


def _row_to_decision(row: dict[str, Any]) -> Decision:
    """Map a PostgREST ``decision`` row to the :class:`Decision` accessor shape."""
    payload = row.get("payload")
    budget_ask = row.get("budget_ask")
    return Decision(
        id=UUID(str(row["id"])),
        source=str(row["source"]),
        payload=payload if isinstance(payload, dict) else {},
        state=DecisionState(str(row["state"])),
        created_at=_parse_timestamp(row.get("created_at")) or datetime.now(UTC),
        question=str(row.get("question") or ""),
        raised_by=str(row.get("raised_by") or ""),
        workstream=str(row.get("workstream") or ""),
        recommendation=str(row.get("recommendation") or ""),
        budget_ask=float(budget_ask) if budget_ask is not None else None,
        due_date=_parse_date(row.get("due_date")),
        priority=str(row.get("priority") or PRIORITY_NORMAL),
        resolution_date=_parse_timestamp(row.get("resolution_date")),
    )


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST ``date`` (``YYYY-MM-DD``) to a date, or ``None`` if absent/bad."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


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

"""KPI-goals store — the leadership-editable scorecard targets + change-log data seam.

The weekly scorecard's per-KPI ``target`` was a provisional set of API-layer named
constants (``app.api.scorecard._TARGETS``), documented "promote to a Supabase goals
store once the KPI owner sets real goals." This module IS that store: the
leadership-editable per-KPI targets behind the same NFR-8 store seam as the
budget/decisions/layouts stores, with an APPEND-ONLY change log (mirroring
``budget_store``'s spend ledger / ``decisions_store``'s decision_event audit):

- :class:`GoalsStore` — the ABC the scorecard route depends on.
- :class:`InMemoryGoalsStore` — the v1 / CI-tested local impl (SEEDED from the spec
  defaults; pure, no I/O).
- :class:`SupabaseGoalsStore` — the live impl over the 0033 ``dashboard_goal`` /
  ``dashboard_goal_event`` tables, via the SAME PostgREST/service_role pattern as the
  budget/decisions/layouts stores (exercised only against a real DB).

The spec-default targets' single canonical home is :data:`DEFAULT_GOALS` (INV-11): the
named constants moved here OUT of ``scorecard.py`` so there is exactly one source for
the provisional goals. An unedited program reads the seed verbatim, so the scorecard's
behavior is unchanged until a leader sets a real goal.

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.program.Program` enum and ``httpx`` (the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the budget/decisions/layouts stores make). The 0033
# table names.
_REST = "/rest/v1"
_GOAL_TABLE = f"{_REST}/dashboard_goal"
_GOAL_EVENT_TABLE = f"{_REST}/dashboard_goal_event"

# Per-KPI spec-default targets — the ONE canonical home (INV-11) for the provisional
# goals, MOVED here out of the API-layer ``_TARGETS`` constants. Named, NOT bare
# literals. Spec values: deposits 180, SLA 90%, conversion 40%, engagement-tier 35%,
# ambassador 30. Applicants / objections / handoffs / event-to-consult have no spec
# goal yet ⇒ a provisional ``0.0`` (a target of 0 reads green for any non-negative
# value — the pure core's documented zero-target degrade).
_TARGET_DEPOSITS = 180.0
_TARGET_SLA = 0.90
_TARGET_CONVERSION = 0.40
_TARGET_ENGAGEMENT = 0.35
_TARGET_AMBASSADOR = 30.0
_TARGET_PROVISIONAL = 0.0

# The nine KPI keys the scorecard renders, each with its spec-default target. This is
# both the seed and the VALID-KEY set: a target for any other key is rejected.
DEFAULT_GOALS: dict[str, float] = {
    "applicants": _TARGET_PROVISIONAL,
    "deposits": _TARGET_DEPOSITS,
    "conversion_top_channel": _TARGET_CONVERSION,
    "engagement_clicked": _TARGET_ENGAGEMENT,
    "followup_sla": _TARGET_SLA,
    "objections": _TARGET_PROVISIONAL,
    "ambassador_enrollments": _TARGET_AMBASSADOR,
    "handoffs": _TARGET_PROVISIONAL,
    "event_to_consult": _TARGET_PROVISIONAL,
}

# The valid goal keys (exactly the nine scorecard KPIs) — an unknown key is rejected.
GOAL_KEYS: tuple[str, ...] = tuple(DEFAULT_GOALS)


@dataclass(frozen=True)
class GoalRow:
    """One leadership-set KPI target (the 0033 ``dashboard_goal`` shape).

    Attributes:
        key: The KPI key (one of :data:`GOAL_KEYS`).
        target: The leadership-set numeric target for this KPI.
        updated_by: WHO last set it (a verified-principal reference — a uid/role
            token, never a name; INV-1).
        updated_at: When it was last set.
    """

    key: str
    target: float
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class GoalChangeEvent:
    """One append-only change-log entry (the 0033 ``dashboard_goal_event`` shape).

    Attributes:
        key: The KPI key whose target changed.
        old_target: The target BEFORE the change (the seed default on a first edit).
        new_target: The target AFTER the change.
        changed_by: WHO made the change (a verified-principal reference, never a name).
        changed_at: When the change was made.
        note: An optional free-form note (PII-free, synthetic — INV-1).
    """

    key: str
    old_target: float
    new_target: float
    changed_by: str
    changed_at: datetime
    note: str | None = None


class GoalsStore(ABC):
    """Read/write seam over the leadership-editable KPI goals (migration 0033).

    The scorecard route depends on this interface, never a concrete store. v1 binds
    the SEEDED in-memory impl; production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped
    (the 0033 tenancy tag) so one program's goals never bleed into another's.
    """

    @abstractmethod
    def get_goals(self, program: Program) -> dict[str, float]:
        """The current per-KPI targets for ``program`` — the seed merged with any edits.

        Always returns ALL nine keys: an unedited KPI reads its :data:`DEFAULT_GOALS`
        seed; an edited one reads the leadership-set target. The scorecard reads this
        in place of the old hardcoded ``_TARGETS`` lookup.
        """
        raise NotImplementedError

    @abstractmethod
    def set_goal(
        self,
        program: Program,
        key: str,
        target: float,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> GoalRow:
        """Set one KPI's target AND append a change event; return the updated row.

        Rejects an unknown ``key`` (not one of :data:`GOAL_KEYS`) with ``KeyError`` so
        the route maps it to a clean 422. The change log records the old→new transition,
        the verified actor, and the optional note — the audit the brief asks for.
        """
        raise NotImplementedError

    @abstractmethod
    def list_events(self, program: Program) -> list[GoalChangeEvent]:
        """The change log for ``program``, in append order (the audit accessor)."""
        raise NotImplementedError


class InMemoryGoalsStore(GoalsStore):
    """In-memory :class:`GoalsStore` — SEEDED from :data:`DEFAULT_GOALS`; no I/O.

    The v1 local store (A-3) and the CI-tested path. Targets live in a per-program dict
    of overrides ON TOP of the seed (so an unedited program reads the seed verbatim);
    the change log accumulates in a per-program append-only list. A production deploy
    swaps :class:`SupabaseGoalsStore` behind the same seam.
    """

    def __init__(self) -> None:
        # Per-program overrides keyed by KPI key; absent ⇒ the DEFAULT_GOALS seed.
        self._overrides: dict[Program, dict[str, GoalRow]] = {}
        # Append-only per-program change log (mirrors the 0033 dashboard_goal_event).
        self._events: dict[Program, list[GoalChangeEvent]] = {}

    def get_goals(self, program: Program) -> dict[str, float]:
        overrides = self._overrides.get(program, {})
        # Seed merged with edits — always all nine keys, in the canonical seed order.
        return {
            key: (overrides[key].target if key in overrides else DEFAULT_GOALS[key])
            for key in DEFAULT_GOALS
        }

    def set_goal(
        self,
        program: Program,
        key: str,
        target: float,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> GoalRow:
        if key not in DEFAULT_GOALS:
            raise KeyError(f"unknown KPI goal key (not a scorecard KPI): {key!r}")
        overrides = self._overrides.setdefault(program, {})
        existing = overrides.get(key)
        old_target = existing.target if existing is not None else DEFAULT_GOALS[key]
        now = datetime.now(UTC)
        row = GoalRow(key=key, target=target, updated_by=changed_by, updated_at=now)
        overrides[key] = row
        self._events.setdefault(program, []).append(
            GoalChangeEvent(
                key=key,
                old_target=old_target,
                new_target=target,
                changed_by=changed_by,
                changed_at=now,
                note=note,
            )
        )
        return row

    def list_events(self, program: Program) -> list[GoalChangeEvent]:
        return list(self._events.get(program, []))


class SupabaseGoalsStore(GoalsStore):
    """Live :class:`GoalsStore` over Supabase PostgREST (service_role; 0033).

    Query-per-request (the stateless-runtime posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call issues a
    fresh PostgREST request over the injected (or per-call) ``httpx`` client. Both tables
    are program-scoped — ``program_id`` is the 0033 tenancy tag — so every read filters
    and every write stamps it. The ``service_role`` key BYPASSES RLS (server-only —
    INV-5 / D-RLS-4) and never leaves the backend.

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
        self,
        path: str,
        payload: dict[str, Any],
        *,
        prefer: str,
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        """One PostgREST POST → the decoded body (``[]`` for return=minimal); fail loud.

        ``prefer`` carries the upsert resolution for ``dashboard_goal``
        (``resolution=merge-duplicates``) and the append for ``dashboard_goal_event``.
        ``on_conflict`` names the conflict-target columns PostgREST upserts on — REQUIRED
        for a merge-duplicates upsert against a non-PK unique key (here ``program_id,key``);
        without it PostgREST resolves on the random ``id`` PK, never conflicts, and a repeat
        set of the same KPI hits the unique constraint (a 409).
        """
        query = f"?on_conflict={on_conflict}" if on_conflict else ""
        url = f"{self._base_url}{path}{query}"
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

    # ---------------------------------------------------------------- interface
    def get_goals(self, program: Program) -> dict[str, float]:
        rows = self._get(
            _GOAL_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": "key,target",
            },
        )
        # Start from the seed (INV-11) and overlay only the keys leadership has edited.
        goals = dict(DEFAULT_GOALS)
        for row in rows:
            key = str(row["key"])
            if key in goals:
                goals[key] = float(row["target"])
        return goals

    def set_goal(
        self,
        program: Program,
        key: str,
        target: float,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> GoalRow:
        if key not in DEFAULT_GOALS:
            raise KeyError(f"unknown KPI goal key (not a scorecard KPI): {key!r}")
        # Read the prior target so the change log records a faithful old→new transition.
        old_target = self.get_goals(program)[key]
        now = datetime.now(UTC)
        # Upsert the goal row (merge-duplicates on the (program_id, key) unique key).
        rows = self._post(
            _GOAL_TABLE,
            {
                "key": key,
                "target": target,
                "updated_by": changed_by,
                "updated_at": now.isoformat(),
                "program_id": program.value,
            },
            prefer="resolution=merge-duplicates,return=representation",
            on_conflict="program_id,key",
        )
        # Append the immutable change event (append-only, like the 0010/0028 posture).
        self._post(
            _GOAL_EVENT_TABLE,
            {
                "key": key,
                "old_target": old_target,
                "new_target": target,
                "changed_by": changed_by,
                "note": note,
                "program_id": program.value,
            },
            prefer="return=minimal",
        )
        if rows:
            return _row_to_goal(rows[0])
        return GoalRow(key=key, target=target, updated_by=changed_by, updated_at=now)

    def list_events(self, program: Program) -> list[GoalChangeEvent]:
        rows = self._get(
            _GOAL_EVENT_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": "key,old_target,new_target,changed_by,note,created_at",
                "order": "created_at.asc",
            },
        )
        return [_row_to_event(row) for row in rows]


def _row_to_goal(row: dict[str, Any]) -> GoalRow:
    """Map a PostgREST ``dashboard_goal`` row to the :class:`GoalRow` accessor shape."""
    return GoalRow(
        key=str(row["key"]),
        target=float(row["target"]),
        updated_by=str(row.get("updated_by") or ""),
        updated_at=_parse_timestamp(row.get("updated_at")) or datetime.now(UTC),
    )


def _row_to_event(row: dict[str, Any]) -> GoalChangeEvent:
    """Map a PostgREST ``dashboard_goal_event`` row to :class:`GoalChangeEvent`."""
    note = row.get("note")
    return GoalChangeEvent(
        key=str(row["key"]),
        old_target=float(row["old_target"]),
        new_target=float(row["new_target"]),
        changed_by=str(row.get("changed_by") or ""),
        changed_at=_parse_timestamp(row.get("created_at")) or datetime.now(UTC),
        note=str(note) if note is not None else None,
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


def build_supabase_goals_store() -> SupabaseGoalsStore | None:
    """Construct the Supabase goals store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.budget_store.build_supabase_budget_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a placeholder
    ``<...>`` sentinel — so the caller falls back to the in-memory store (A-3). No
    program is threaded in: the store is constructed program-agnostic and bounded per
    call by the ``program`` argument each method takes.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseGoalsStore(base_url=url, service_role_key=key)

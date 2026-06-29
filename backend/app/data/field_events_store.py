"""Field & Events store (Module 8) — the GT-organized field-event seam.

The Field & Events module owns ONE piece of program-scoped state behind the same
NFR-8 store seam as the grassroots/camp stores: the GT-organized `field_event` rows
(shadow days, chess tournaments, AMAs, community events, festivals, webinars). This is
MANUAL ENTRY — no external API feeds it; the Field & Events Owner logs each row and its
attendance/consults by hand. All synthetic/aggregate data only (INV-1/INV-6 — NO real
PII; aggregate venue labels, never a precise address).

This store is DISTINCT from :mod:`app.data.grassroots_store`'s ``ambassador_event``
(the parent-led grassroots events the Field & Events module reads READ-ONLY): Module 8
OWNS + WRITES ``field_event`` and only READS ``ambassador_event`` (via the grassroots
store) to blend a single calendar.

- :class:`FieldEventsStore` — the ABC every field-events route depends on.
- :class:`InMemoryFieldEventsStore` — the v1 / CI-tested local impl (pure, no I/O),
  with a deterministic :meth:`InMemoryFieldEventsStore.seed_demo` (no clock/random).
- :class:`SupabaseFieldEventsStore` — the live impl over the 0039 ``field_event`` table,
  via the SAME PostgREST/service_role pattern as the grassroots store. Upserts pass
  ``on_conflict`` in the PostgREST URL (the bit that bit us before).

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.program.Program` enum and ``httpx`` (the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the grassroots store makes). The 0039 name.
_REST = "/rest/v1"
_FIELD_EVENT_TABLE = f"{_REST}/field_event"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 8). PII-free (INV-1) + clock/random-free: ids are
# UUID(int=...), dates derive from a FIXED epoch (no clock). 7 GT-organized events
# spanning the six event types with a MIX of statuses + dates (some completed in the
# prior ~30 days with attendance+consults; some upcoming in the next ~30 days with
# RSVPs only) so the overview rollup reads sensibly but NOT maxed. See seed_demo.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = date(2026, 6, 15)

# (event_name, event_type, date_offset_days, venue, rsvp, attendance, consults, status,
# budget_usd, notes, materials) — offsets relative to _SEED_EPOCH (negative ⇒ past).
# Calibrated totals (with now=_SEED_EPOCH): upcoming(next 30d, not cancelled)=3,
# completed-this-month(June 2026)=2, total_rsvps=230, total_attendance=96,
# rsvp→attendance≈42%, consults=28, event→consult≈12%, top type by attendance=ama(41).
_SEED_EVENTS: tuple[tuple[str, str, int, str, int, int, int, str, int, str, str], ...] = (
    # --- completed in the prior ~30 days (attendance + consults logged) ---
    (
        "Shadow Day at Mueller campus",
        "shadow_day",
        -20,
        "Austin metro",
        28,
        22,
        9,
        "completed",
        1500,
        "Strong turnout; 9 consults booked on-site.",
        "Tour decks, lanyards",
    ),
    (
        "Fall Open Chess Tournament",
        "chess_tournament",
        -12,
        "Plano",
        40,
        33,
        7,
        "completed",
        2200,
        "Co-hosted with the regional chess league.",
        "Boards, trophies, banner",
    ),
    (
        "Founder AMA (live webinar)",
        "ama",
        -6,
        "Online",
        55,
        41,
        12,
        "completed",
        300,
        "Highest consult yield of the quarter.",
        "Slides, recording",
    ),
    # --- upcoming in the next ~30 days (RSVPs only, no attendance yet) ---
    (
        "Robotics Festival booth",
        "festival",
        8,
        "Round Rock",
        35,
        0,
        0,
        "confirmed",
        1800,
        "Booth confirmed; volunteers assigned.",
        "Booth kit, flyers",
    ),
    (
        "Community open house",
        "community_event",
        15,
        "Frisco",
        24,
        0,
        0,
        "confirmed",
        900,
        "Evening session for working parents.",
        "Welcome packets",
    ),
    (
        "Admissions info webinar",
        "webinar",
        22,
        "Online",
        48,
        0,
        0,
        "planning",
        250,
        "Draft agenda; date holds.",
        "Slides (draft)",
    ),
    # --- cancelled (the fourth status; excluded from upcoming) ---
    (
        "Downtown street fair booth",
        "festival",
        5,
        "Houston",
        0,
        0,
        0,
        "cancelled",
        0,
        "Cancelled — vendor permit fell through.",
        "",
    ),
)


@dataclass(frozen=True)
class FieldEvent:
    """One GT-organized field-marketing event row (synthetic/aggregate data; INV-1/INV-6).

    Attributes:
        event_id: The row PK.
        event_name: The event's display name.
        event_type: One of the six params event-type labels (DB CHECK is the backstop).
        venue: An AGGREGATE venue label (a city/area/campus), never a precise address.
        event_date: The event date (date-only).
        rsvp_count: Hand-logged RSVPs (MANUAL ENTRY).
        attendance_count: Hand-logged attendance (MANUAL ENTRY).
        consults_booked: Hand-logged consults booked — the MANUAL conversion figure.
        status: The lifecycle status (planning/confirmed/completed/cancelled).
        owner: The owning workstream/operator routing token (not PII); default 'events'.
        notes: Free-text operator notes (synthetic/aggregate only; INV-1).
        materials: Free-text materials checklist label.
        budget_usd: The event's budget allocation in whole USD.
    """

    event_id: UUID
    event_name: str
    event_type: str
    venue: str
    event_date: date
    rsvp_count: int
    attendance_count: int
    consults_booked: int
    status: str
    owner: str
    notes: str
    materials: str
    budget_usd: int


# The mutable business columns an `update_event` partial change may target (the
# identity column `event_id` and the scoping `program_id` are never updatable here).
_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "event_name",
        "event_type",
        "venue",
        "event_date",
        "rsvp_count",
        "attendance_count",
        "consults_booked",
        "status",
        "owner",
        "notes",
        "materials",
        "budget_usd",
    }
)


class FieldEventsStore(ABC):
    """Read/write seam over the Module-8 field-events state (migration 0039).

    Every field-events route depends on this interface, never a concrete store. v1 binds
    the in-memory impl (seed-driven); production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped (the
    0039 tenancy tag) so one program's events never bleed into another's.
    """

    @abstractmethod
    def list_events(self, program: Program) -> list[FieldEvent]:
        """The GT-organized field events for ``program`` (insertion/created order)."""
        raise NotImplementedError

    @abstractmethod
    def create_event(
        self,
        program: Program,
        *,
        event_id: UUID | None = None,
        event_name: str,
        event_type: str = "community_event",
        venue: str = "",
        event_date: date,
        rsvp_count: int = 0,
        attendance_count: int = 0,
        consults_booked: int = 0,
        status: str = "planning",
        owner: str = "events",
        notes: str = "",
        materials: str = "",
        budget_usd: int = 0,
    ) -> FieldEvent:
        """Create one field event (gen a uuid when ``event_id`` is None); return it."""
        raise NotImplementedError

    @abstractmethod
    def update_event(self, program: Program, event_id: UUID, **changes: Any) -> FieldEvent:
        """Partially update one field event by ``event_id``; return the updated row.

        Only the business columns in :data:`_UPDATABLE_FIELDS` may change. Raises
        ``KeyError`` on an unknown ``event_id`` (the route maps it to a 404).
        """
        raise NotImplementedError


class InMemoryFieldEventsStore(FieldEventsStore):
    """In-memory :class:`FieldEventsStore` — per-program list; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseFieldEventsStore` behind the same seam. :meth:`seed_demo` lays down
    the deterministic demo events (idempotent).
    """

    def __init__(self) -> None:
        self._events: dict[Program, list[FieldEvent]] = {}
        self._seeded: set[Program] = set()

    def list_events(self, program: Program) -> list[FieldEvent]:
        return list(self._events.get(program, []))

    def create_event(
        self,
        program: Program,
        *,
        event_id: UUID | None = None,
        event_name: str,
        event_type: str = "community_event",
        venue: str = "",
        event_date: date,
        rsvp_count: int = 0,
        attendance_count: int = 0,
        consults_booked: int = 0,
        status: str = "planning",
        owner: str = "events",
        notes: str = "",
        materials: str = "",
        budget_usd: int = 0,
    ) -> FieldEvent:
        event = FieldEvent(
            event_id=event_id if event_id is not None else uuid4(),
            event_name=event_name,
            event_type=event_type,
            venue=venue,
            event_date=event_date,
            rsvp_count=rsvp_count,
            attendance_count=attendance_count,
            consults_booked=consults_booked,
            status=status,
            owner=owner,
            notes=notes,
            materials=materials,
            budget_usd=budget_usd,
        )
        self._events.setdefault(program, []).append(event)
        return event

    def update_event(self, program: Program, event_id: UUID, **changes: Any) -> FieldEvent:
        applied = {k: v for k, v in changes.items() if k in _UPDATABLE_FIELDS and v is not None}
        events = self._events.setdefault(program, [])
        for i, existing in enumerate(events):
            if existing.event_id == event_id:
                updated = replace(existing, **applied)
                events[i] = updated
                return updated
        raise KeyError(f"unknown field event: {event_id!r}")

    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo field events (INV-1; idempotent).

        Clock/random-free: all dates derive from :data:`_SEED_EPOCH`; ids are derived
        deterministically (``UUID(int=...)``) so a re-seed is a no-op in shape. The
        seven events span the six event types with a MIX of statuses (3 completed / 2
        confirmed / 1 planning / 1 cancelled) and dates (3 completed in the prior ~30
        days with attendance+consults; 3 upcoming in the next ~30 days with RSVPs only;
        1 cancelled) so the overview rollup reads sensibly but NOT maxed. Re-seeding the
        same program is a guarded no-op.
        """
        if program in self._seeded:
            return
        for i, row in enumerate(_SEED_EVENTS):
            (
                name,
                ev_type,
                date_off,
                venue,
                rsvp,
                attendance,
                consults,
                status,
                budget,
                notes,
                materials,
            ) = row
            self.create_event(
                program,
                event_id=UUID(int=(0xF1E1_0000 + i)),  # deterministic, demo-only
                event_name=name,
                event_type=ev_type,
                venue=venue,
                event_date=_SEED_EPOCH + timedelta(days=date_off),
                rsvp_count=rsvp,
                attendance_count=attendance,
                consults_booked=consults,
                status=status,
                owner="events",
                notes=notes,
                materials=materials,
                budget_usd=budget,
            )
        self._seeded.add(program)


class SupabaseFieldEventsStore(FieldEventsStore):
    """Live :class:`FieldEventsStore` over Supabase PostgREST (service_role; 0039).

    Query-per-request (the stateless-runtime posture of the grassroots/camp stores):
    each call issues a fresh PostgREST request over the injected (or per-call) ``httpx``
    client. The table is program-scoped (``program_id`` is the 0039 tenancy tag) so
    every read filters and every write stamps it. Upserts pass ``on_conflict`` in the
    PostgREST URL (the bit that bit us before). The ``service_role`` key BYPASSES RLS
    (server-only — INV-5 / D-RLS-4) and never leaves the backend.
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

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: Any = None,
        prefer: str | None = None,
    ) -> list[dict[str, Any]]:
        """One PostgREST request → the decoded JSON array (fail loud on non-2xx)."""
        url = f"{self._base_url}{path}"
        headers = self._headers()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer is not None:
            headers["Prefer"] = prefer

        def _send(client: httpx.Client) -> httpx.Response:
            return client.request(method, url, params=params, headers=headers, json=payload)

        if self._client is not None:
            response = _send(self._client)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = _send(client)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST {method} {path} → {response.status_code}: {response.text[:300]}"
            )
        body: Any = response.json() if response.content else []
        if not isinstance(body, list):
            raise SupabaseError(f"PostgREST {method} {path} returned a non-array body")
        return body

    _SELECT = (
        "event_id,event_name,event_type,venue,event_date,rsvp_count,attendance_count,"
        "consults_booked,status,owner,notes,materials,budget_usd"
    )

    def list_events(self, program: Program) -> list[FieldEvent]:
        rows = self._request(
            "GET",
            _FIELD_EVENT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._SELECT,
                "order": "event_date.asc",
            },
        )
        return [_row_to_event(r) for r in rows]

    def create_event(
        self,
        program: Program,
        *,
        event_id: UUID | None = None,
        event_name: str,
        event_type: str = "community_event",
        venue: str = "",
        event_date: date,
        rsvp_count: int = 0,
        attendance_count: int = 0,
        consults_booked: int = 0,
        status: str = "planning",
        owner: str = "events",
        notes: str = "",
        materials: str = "",
        budget_usd: int = 0,
    ) -> FieldEvent:
        payload: dict[str, Any] = {
            "event_name": event_name,
            "event_type": event_type,
            "venue": venue,
            "event_date": event_date.isoformat(),
            "rsvp_count": rsvp_count,
            "attendance_count": attendance_count,
            "consults_booked": consults_booked,
            "status": status,
            "owner": owner,
            "notes": notes,
            "materials": materials,
            "budget_usd": budget_usd,
            "program_id": program.value,
        }
        if event_id is not None:
            payload["event_id"] = str(event_id)
        # on_conflict in the URL (the upsert key is the PK) — the bit that bit us before.
        rows = self._request(
            "POST",
            _FIELD_EVENT_TABLE,
            params={"on_conflict": "event_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /field_event returned no representation row")
        return _row_to_event(rows[0])

    def update_event(self, program: Program, event_id: UUID, **changes: Any) -> FieldEvent:
        payload: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in _UPDATABLE_FIELDS or value is None:
                continue
            payload[key] = value.isoformat() if isinstance(value, date) else value
        payload["updated_at"] = "now()"
        patched = self._request(
            "PATCH",
            _FIELD_EVENT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "event_id": f"eq.{event_id}",
            },
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown field event: {event_id!r}")
        return _row_to_event(patched[0])


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST ``date`` to a :class:`datetime.date`, or ``None`` when absent."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _row_to_event(row: dict[str, Any]) -> FieldEvent:
    """Map a PostgREST ``field_event`` row to the :class:`FieldEvent` accessor shape."""
    return FieldEvent(
        event_id=UUID(str(row["event_id"])),
        event_name=str(row["event_name"]),
        event_type=str(row.get("event_type") or "community_event"),
        venue=str(row.get("venue") or ""),
        event_date=_parse_date(row["event_date"]) or date.min,
        rsvp_count=int(row.get("rsvp_count") or 0),
        attendance_count=int(row.get("attendance_count") or 0),
        consults_booked=int(row.get("consults_booked") or 0),
        status=str(row.get("status") or "planning"),
        owner=str(row.get("owner") or "events"),
        notes=str(row.get("notes") or ""),
        materials=str(row.get("materials") or ""),
        budget_usd=int(row.get("budget_usd") or 0),
    )


def build_supabase_field_events_store() -> SupabaseFieldEventsStore | None:
    """Construct the Supabase field-events store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.grassroots_store.build_supabase_grassroots_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` from the environment at the
    composition root, returning ``None`` when either is absent or a placeholder
    ``<...>`` sentinel — so the caller falls back to the in-memory store (A-3).
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseFieldEventsStore(base_url=url, service_role_key=key)

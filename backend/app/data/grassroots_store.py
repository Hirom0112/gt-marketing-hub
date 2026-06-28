"""Grassroots-Engine store (Module 2) — the ambassador/sprint/market-map/events seam.

The Grassroots Engine owns four pieces of program-scoped state behind the same NFR-8
store seam as the budget/decisions stores: the ambassador ROSTER, the referral
SPRINTS, the community MARKET-MAP nodes, and the parent-led EVENTS (the latter is the
SOURCE OF TRUTH the Field & Events module reads READ-ONLY). All synthetic/aggregate
adult data only (INV-1/INV-6 — NO real PII).

- :class:`GrassrootsStore` — the ABC every grassroots route depends on.
- :class:`InMemoryGrassrootsStore` — the v1 / CI-tested local impl (pure, no I/O),
  with a deterministic :meth:`InMemoryGrassrootsStore.seed_demo` (no clock/random).
- :class:`SupabaseGrassrootsStore` — the live impl over the 0035 ``ambassador`` /
  ``referral_sprint`` / ``market_node`` / ``ambassador_event`` tables, via the SAME
  PostgREST/service_role pattern as the budget/decisions stores. Upserts pass
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
# party's URLs, the same carve-out the budget/decisions stores make). The 0035 names.
_REST = "/rest/v1"
_AMBASSADOR_TABLE = f"{_REST}/ambassador"
_SPRINT_TABLE = f"{_REST}/referral_sprint"
_MARKET_NODE_TABLE = f"{_REST}/market_node"
_EVENT_TABLE = f"{_REST}/ambassador_event"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 2). PII-free (INV-1) + clock/random-free: dates
# derive from the fixed synthetic demo "now" (2026-06-15, the same _SEED_EPOCH the
# budget burn ledger anchors to) so the roster + sprints + events render coherently.
# Calibrated so the four goal bars read SENSIBLY but NOT maxed (realistic gaps):
#   active ambassadors 18 / target 25 (72%); warm intros 150 / 200 (75%);
#   p2p calls 38 / 50 (76%); influenced enrollments 22 / 30 (73%).
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = date(2026, 6, 15)

# (stage, intros, p2p_calls) per seeded ambassador, hand-calibrated to the totals
# above. 30 ambassadors: 5 champion + 13 active + 4 onboarded + 4 outreached + 4
# prospect (active+champion = 18). intros sum = 150, p2p sum = 38.
_SEED_AMBASSADORS: tuple[tuple[str, int, int], ...] = (
    # champions (5): intros 75, p2p 24
    ("champion", 18, 6),
    ("champion", 16, 5),
    ("champion", 15, 5),
    ("champion", 14, 4),
    ("champion", 12, 4),
    # active (13): intros 65, p2p 12
    ("active", 7, 2),
    ("active", 7, 2),
    ("active", 6, 2),
    ("active", 6, 1),
    ("active", 6, 1),
    ("active", 5, 1),
    ("active", 5, 1),
    ("active", 5, 1),
    ("active", 4, 1),
    ("active", 4, 0),
    ("active", 4, 0),
    ("active", 3, 0),
    ("active", 3, 0),
    # onboarded (4): intros 10, p2p 2
    ("onboarded", 3, 1),
    ("onboarded", 3, 1),
    ("onboarded", 2, 0),
    ("onboarded", 2, 0),
    # outreached (4): intros 0, p2p 0
    ("outreached", 0, 0),
    ("outreached", 0, 0),
    ("outreached", 0, 0),
    ("outreached", 0, 0),
    # prospect (4): intros 0, p2p 0
    ("prospect", 0, 0),
    ("prospect", 0, 0),
    ("prospect", 0, 0),
    ("prospect", 0, 0),
)

# Aggregate segment + region label pools (INV-6 — labels only). Cycled across the
# seeded roster deterministically. Mirrors the synthetic_ambassadors fixture labels.
_SEED_SEGMENTS: tuple[str, ...] = (
    "Robotics parents",
    "Homeschool co-op",
    "Chess club",
    "Math circle",
    "Parent group",
    "STEM meetup",
    "Debate league",
)
_SEED_REGIONS: tuple[str, ...] = (
    "Austin metro",
    "Plano",
    "Round Rock",
    "Frisco",
    "Houston",
    "San Antonio",
    "DFW",
)

# A handful of seeded ambassadors REUSE the dual-source reconcile fixture's emails
# (app.data.synthetic_ambassadors) so the roster's reconcile-provenance badges are
# demonstrable; the rest carry generated @example.invalid emails. All synthetic
# (INV-1). The first N seeded ambassadors take these in order.
_RECONCILE_FIXTURE_EMAILS: tuple[str, ...] = (
    "fields.214@example.invalid",
    "bell.731@example.invalid",
    "nair.498@example.invalid",
    "carter.305@example.invalid",
    "rahman.872@example.invalid",
    "whitaker.640@example.invalid",
    "ortiz.157@example.invalid",
    "nakamura.926@example.invalid",
    "park.583@example.invalid",
    "liu.419@example.invalid",
    "haddad.268@example.invalid",
)

# (name, window_start_offset_days, window_end_offset_days, ambassadors_enlisted,
# families_identified, conversions, status) — offsets relative to _SEED_EPOCH (a
# negative offset is before the demo now). Sprint A is on-pace; Sprint B is behind;
# conversions sum to 22 (the influenced-enrollment stand-in). Calibrated against
# behind_pace_frac=0.8: A elapsed 0.5 → expected 10, conv 12 ≥ 8 → on_pace; B elapsed
# 0.75 → expected 13.5, conv 10 < 10.8 → behind.
_SEED_SPRINTS: tuple[tuple[str, int, int, int, int, int, str], ...] = (
    ("Back-to-school referral push", -14, 14, 8, 20, 12, "active"),
    ("Robotics-season referral sprint", -21, 7, 6, 18, 10, "active"),
)

# (category, contact_label, status, leads_generated, last_activity_offset_days) — a
# market map across several categories with mixed statuses. Labels are aggregate
# (INV-1/INV-6).
_SEED_MARKET_NODES: tuple[tuple[str, str, str, int, int], ...] = (
    ("Parent groups", "Austin parent group list", "active", 9, -2),
    ("Homeschool co-ops", "Hill Country homeschool co-op", "in_conversation", 5, -4),
    ("Chess clubs", "Round Rock chess club", "outreach", 2, -6),
    ("Robotics teams", "Plano robotics parents", "active", 7, -1),
    ("Debate leagues", "DFW debate league", "cold", 0, -20),
    ("Math circles", "Frisco math circle", "in_conversation", 4, -3),
    ("STEM meetups", "Houston STEM meetup", "cold", 0, -15),
)

# (event_name, event_type, date_offset_days, location_label, rsvp_count,
# attendance_count, conversions_influenced, host_index) — mix of PAST events (with
# attendance) and UPCOMING events (with RSVPs, no attendance yet). host_index points
# into the seeded ambassador list (or -1 for no host).
_SEED_EVENTS: tuple[tuple[str, str, int, str, int, int, int, int], ...] = (
    ("Coffee chat with prospective parents", "coffee_chat", -10, "Austin metro", 14, 11, 3, 0),
    ("Robotics open house Q&A", "qa", -5, "Plano", 22, 18, 4, 5),
    ("Campus visit morning", "school_visit", 6, "Round Rock", 16, 0, 0, 1),
    ("Virtual info session", "virtual", 12, "Online", 30, 0, 0, 2),
)


@dataclass(frozen=True)
class Ambassador:
    """One ambassador roster row (synthetic/aggregate adult data; INV-1/INV-6).

    Attributes:
        ambassador_id: The row PK.
        synthetic_name: Synthetic display name (an adult, never a child; INV-1/INV-6).
        synthetic_email: Synthetic contact email (the @example.invalid sink; INV-1).
        segment: Aggregate community segment label (INV-6).
        region: Aggregate region label (INV-6).
        status: The pipeline stage (prospect/outreached/onboarded/active/champion).
        intros: Warm intros credited to this ambassador.
        p2p_calls: Peer-to-peer calls logged for this ambassador.
        last_touch: Last-touch date (date-only); ``None`` if never touched.
        owner: The owning workstream/operator routing token (not PII).
    """

    ambassador_id: UUID
    synthetic_name: str
    synthetic_email: str
    segment: str
    region: str
    status: str
    intros: int
    p2p_calls: int
    last_touch: date | None
    owner: str


@dataclass(frozen=True)
class ReferralSprint:
    """One referral-sprint row (a time-boxed referral push)."""

    sprint_id: UUID
    name: str
    window_start: date
    window_end: date
    ambassadors_enlisted: int
    families_identified: int
    conversions: int
    status: str


@dataclass(frozen=True)
class MarketNode:
    """One community market-map node (an aggregate category + its outreach state)."""

    node_id: UUID
    category: str
    contact_label: str
    status: str
    leads_generated: int
    last_activity: date | None
    owner: str


@dataclass(frozen=True)
class AmbassadorEvent:
    """One parent-led event row (the SOURCE OF TRUTH Field & Events reads READ-ONLY)."""

    event_id: UUID
    event_name: str
    host_ambassador_id: UUID | None
    event_type: str
    date: date
    location_label: str
    rsvp_count: int
    attendance_count: int
    conversions_influenced: int


class GrassrootsStore(ABC):
    """Read/write seam over the Module-2 Grassroots Engine (migration 0035).

    Every grassroots route depends on this interface, never a concrete store. v1 binds
    the in-memory impl (params/seed-driven); production swaps the Supabase-backed one
    with zero caller changes (the NFR-8 store-seam pattern). Every method is
    program-scoped (the 0035 tenancy tag) so one program's roster never bleeds into
    another's.
    """

    # ----------------------------------------------------------------- ambassadors
    @abstractmethod
    def list_ambassadors(self, program: Program) -> list[Ambassador]:
        """The ambassador roster for ``program`` (insertion/created order)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_ambassador(
        self,
        program: Program,
        *,
        ambassador_id: UUID | None = None,
        synthetic_name: str,
        synthetic_email: str,
        segment: str = "",
        region: str = "",
        status: str = "prospect",
        intros: int = 0,
        p2p_calls: int = 0,
        last_touch: date | None = None,
        owner: str = "grassroots",
    ) -> Ambassador:
        """Insert or update one ambassador (keyed by ``ambassador_id``); return it."""
        raise NotImplementedError

    @abstractmethod
    def log_p2p_call(self, program: Program, ambassador_id: UUID) -> Ambassador:
        """Increment one ambassador's ``p2p_calls`` by one; return the updated row.

        Raises ``KeyError`` on an unknown ambassador (the route maps it to a 404).
        """
        raise NotImplementedError

    # --------------------------------------------------------------------- sprints
    @abstractmethod
    def list_sprints(self, program: Program) -> list[ReferralSprint]:
        """The referral sprints for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def create_sprint(
        self,
        program: Program,
        *,
        name: str,
        window_start: date,
        window_end: date,
        ambassadors_enlisted: int = 0,
        families_identified: int = 0,
        conversions: int = 0,
        status: str = "active",
    ) -> ReferralSprint:
        """Create one referral sprint and return it."""
        raise NotImplementedError

    # ---------------------------------------------------------------- market nodes
    @abstractmethod
    def list_market_nodes(self, program: Program) -> list[MarketNode]:
        """The market-map nodes for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_market_node(
        self,
        program: Program,
        *,
        node_id: UUID | None = None,
        category: str,
        contact_label: str = "",
        status: str = "cold",
        leads_generated: int = 0,
        last_activity: date | None = None,
        owner: str = "grassroots",
    ) -> MarketNode:
        """Insert or update one market-map node (keyed by ``node_id``); return it."""
        raise NotImplementedError

    # ---------------------------------------------------------------------- events
    @abstractmethod
    def list_events(self, program: Program) -> list[AmbassadorEvent]:
        """The parent-led events for ``program`` (the Field & Events read source)."""
        raise NotImplementedError

    @abstractmethod
    def create_event(
        self,
        program: Program,
        *,
        event_name: str,
        host_ambassador_id: UUID | None = None,
        event_type: str = "coffee_chat",
        date: date,
        location_label: str = "",
        rsvp_count: int = 0,
        attendance_count: int = 0,
        conversions_influenced: int = 0,
    ) -> AmbassadorEvent:
        """Create one parent-led event and return it."""
        raise NotImplementedError


class InMemoryGrassrootsStore(GrassrootsStore):
    """In-memory :class:`GrassrootsStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseGrassrootsStore` behind the same seam. :meth:`seed_demo` lays down
    the deterministic demo roster/sprints/nodes/events (idempotent).
    """

    def __init__(self) -> None:
        self._ambassadors: dict[Program, list[Ambassador]] = {}
        self._sprints: dict[Program, list[ReferralSprint]] = {}
        self._nodes: dict[Program, list[MarketNode]] = {}
        self._events: dict[Program, list[AmbassadorEvent]] = {}
        self._seeded: set[Program] = set()

    # ----------------------------------------------------------------- ambassadors
    def list_ambassadors(self, program: Program) -> list[Ambassador]:
        return list(self._ambassadors.get(program, []))

    def upsert_ambassador(
        self,
        program: Program,
        *,
        ambassador_id: UUID | None = None,
        synthetic_name: str,
        synthetic_email: str,
        segment: str = "",
        region: str = "",
        status: str = "prospect",
        intros: int = 0,
        p2p_calls: int = 0,
        last_touch: date | None = None,
        owner: str = "grassroots",
    ) -> Ambassador:
        row = Ambassador(
            ambassador_id=ambassador_id if ambassador_id is not None else uuid4(),
            synthetic_name=synthetic_name,
            synthetic_email=synthetic_email,
            segment=segment,
            region=region,
            status=status,
            intros=intros,
            p2p_calls=p2p_calls,
            last_touch=last_touch,
            owner=owner,
        )
        roster = self._ambassadors.setdefault(program, [])
        for i, existing in enumerate(roster):
            if existing.ambassador_id == row.ambassador_id:
                roster[i] = row
                return row
        roster.append(row)
        return row

    def log_p2p_call(self, program: Program, ambassador_id: UUID) -> Ambassador:
        roster = self._ambassadors.setdefault(program, [])
        for i, existing in enumerate(roster):
            if existing.ambassador_id == ambassador_id:
                updated = replace(existing, p2p_calls=existing.p2p_calls + 1)
                roster[i] = updated
                return updated
        raise KeyError(f"unknown ambassador: {ambassador_id!r}")

    # --------------------------------------------------------------------- sprints
    def list_sprints(self, program: Program) -> list[ReferralSprint]:
        return list(self._sprints.get(program, []))

    def create_sprint(
        self,
        program: Program,
        *,
        name: str,
        window_start: date,
        window_end: date,
        ambassadors_enlisted: int = 0,
        families_identified: int = 0,
        conversions: int = 0,
        status: str = "active",
    ) -> ReferralSprint:
        sprint = ReferralSprint(
            sprint_id=uuid4(),
            name=name,
            window_start=window_start,
            window_end=window_end,
            ambassadors_enlisted=ambassadors_enlisted,
            families_identified=families_identified,
            conversions=conversions,
            status=status,
        )
        self._sprints.setdefault(program, []).append(sprint)
        return sprint

    # ---------------------------------------------------------------- market nodes
    def list_market_nodes(self, program: Program) -> list[MarketNode]:
        return list(self._nodes.get(program, []))

    def upsert_market_node(
        self,
        program: Program,
        *,
        node_id: UUID | None = None,
        category: str,
        contact_label: str = "",
        status: str = "cold",
        leads_generated: int = 0,
        last_activity: date | None = None,
        owner: str = "grassroots",
    ) -> MarketNode:
        node = MarketNode(
            node_id=node_id if node_id is not None else uuid4(),
            category=category,
            contact_label=contact_label,
            status=status,
            leads_generated=leads_generated,
            last_activity=last_activity,
            owner=owner,
        )
        nodes = self._nodes.setdefault(program, [])
        for i, existing in enumerate(nodes):
            if existing.node_id == node.node_id:
                nodes[i] = node
                return node
        nodes.append(node)
        return node

    # ---------------------------------------------------------------------- events
    def list_events(self, program: Program) -> list[AmbassadorEvent]:
        return list(self._events.get(program, []))

    def create_event(
        self,
        program: Program,
        *,
        event_name: str,
        host_ambassador_id: UUID | None = None,
        event_type: str = "coffee_chat",
        date: date,
        location_label: str = "",
        rsvp_count: int = 0,
        attendance_count: int = 0,
        conversions_influenced: int = 0,
    ) -> AmbassadorEvent:
        event = AmbassadorEvent(
            event_id=uuid4(),
            event_name=event_name,
            host_ambassador_id=host_ambassador_id,
            event_type=event_type,
            date=date,
            location_label=location_label,
            rsvp_count=rsvp_count,
            attendance_count=attendance_count,
            conversions_influenced=conversions_influenced,
        )
        self._events.setdefault(program, []).append(event)
        return event

    # ------------------------------------------------------------------ demo seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo roster/sprints/nodes/events (INV-1; idempotent).

        Clock/random-free: all dates derive from :data:`_SEED_EPOCH`; ids are derived
        deterministically (``UUID(int=...)``) so a re-seed is a no-op in shape. The
        roster/sprints are calibrated so the four goal bars read sensibly but NOT maxed
        (see the module header). Re-seeding the same program is a guarded no-op.
        """
        if program in self._seeded:
            return

        seeded_ids: list[UUID] = []
        for i, (status, intros, p2p) in enumerate(_SEED_AMBASSADORS):
            amb_id = UUID(int=(0x6A55_0000 + i))  # deterministic, demo-only
            seeded_ids.append(amb_id)
            email = (
                _RECONCILE_FIXTURE_EMAILS[i]
                if i < len(_RECONCILE_FIXTURE_EMAILS)
                else f"ambassador.{i:02d}@example.invalid"
            )
            # Touched recency decays with pipeline position (active rows touched more
            # recently); a prospect with no touch reads None.
            last_touch = None if status == "prospect" else _SEED_EPOCH - timedelta(days=(i % 9) + 1)
            self.upsert_ambassador(
                program,
                ambassador_id=amb_id,
                synthetic_name=f"GR Ambassador {i:02d}",
                synthetic_email=email,
                segment=_SEED_SEGMENTS[i % len(_SEED_SEGMENTS)],
                region=_SEED_REGIONS[i % len(_SEED_REGIONS)],
                status=status,
                intros=intros,
                p2p_calls=p2p,
                last_touch=last_touch,
                owner="grassroots",
            )

        for name, start_off, end_off, enlisted, identified, conv, status in _SEED_SPRINTS:
            self.create_sprint(
                program,
                name=name,
                window_start=_SEED_EPOCH + timedelta(days=start_off),
                window_end=_SEED_EPOCH + timedelta(days=end_off),
                ambassadors_enlisted=enlisted,
                families_identified=identified,
                conversions=conv,
                status=status,
            )

        for category, label, status, leads, act_off in _SEED_MARKET_NODES:
            self.upsert_market_node(
                program,
                category=category,
                contact_label=label,
                status=status,
                leads_generated=leads,
                last_activity=_SEED_EPOCH + timedelta(days=act_off),
                owner="grassroots",
            )

        for name, ev_type, date_off, loc, rsvp, attendance, conv, host_idx in _SEED_EVENTS:
            host = seeded_ids[host_idx] if 0 <= host_idx < len(seeded_ids) else None
            self.create_event(
                program,
                event_name=name,
                host_ambassador_id=host,
                event_type=ev_type,
                date=_SEED_EPOCH + timedelta(days=date_off),
                location_label=loc,
                rsvp_count=rsvp,
                attendance_count=attendance,
                conversions_influenced=conv,
            )

        self._seeded.add(program)


class SupabaseGrassrootsStore(GrassrootsStore):
    """Live :class:`GrassrootsStore` over Supabase PostgREST (service_role; 0035).

    Query-per-request (the stateless-runtime posture of the family/budget stores): each
    call issues a fresh PostgREST request over the injected (or per-call) ``httpx``
    client. Every table is program-scoped (``program_id`` is the 0035 tenancy tag) so
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

    # ------------------------------------------------------------------ I/O
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

    # ----------------------------------------------------------------- ambassadors
    def list_ambassadors(self, program: Program) -> list[Ambassador]:
        rows = self._request(
            "GET",
            _AMBASSADOR_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "ambassador_id,synthetic_name,synthetic_email,segment,region,"
                    "status,intros,p2p_calls,last_touch,owner"
                ),
                "order": "created_at.asc",
            },
        )
        return [_row_to_ambassador(r) for r in rows]

    def upsert_ambassador(
        self,
        program: Program,
        *,
        ambassador_id: UUID | None = None,
        synthetic_name: str,
        synthetic_email: str,
        segment: str = "",
        region: str = "",
        status: str = "prospect",
        intros: int = 0,
        p2p_calls: int = 0,
        last_touch: date | None = None,
        owner: str = "grassroots",
    ) -> Ambassador:
        payload: dict[str, Any] = {
            "synthetic_name": synthetic_name,
            "synthetic_email": synthetic_email,
            "segment": segment,
            "region": region,
            "status": status,
            "intros": intros,
            "p2p_calls": p2p_calls,
            "last_touch": last_touch.isoformat() if last_touch is not None else None,
            "owner": owner,
            "program_id": program.value,
        }
        if ambassador_id is not None:
            payload["ambassador_id"] = str(ambassador_id)
        # on_conflict in the URL (the upsert key is the PK) — the bit that bit us before.
        rows = self._request(
            "POST",
            _AMBASSADOR_TABLE,
            params={"on_conflict": "ambassador_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /ambassador returned no representation row")
        return _row_to_ambassador(rows[0])

    def log_p2p_call(self, program: Program, ambassador_id: UUID) -> Ambassador:
        # Read-then-patch (PostgREST has no atomic increment without an RPC). Fail loud
        # on an unknown ambassador so the route maps it to a 404.
        rows = self._request(
            "GET",
            _AMBASSADOR_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "ambassador_id": f"eq.{ambassador_id}",
                "select": "p2p_calls",
            },
        )
        if not rows:
            raise KeyError(f"unknown ambassador: {ambassador_id!r}")
        current = int(rows[0]["p2p_calls"])
        patched = self._request(
            "PATCH",
            _AMBASSADOR_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "ambassador_id": f"eq.{ambassador_id}",
            },
            payload={"p2p_calls": current + 1, "updated_at": "now()"},
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown ambassador: {ambassador_id!r}")
        return _row_to_ambassador(patched[0])

    # --------------------------------------------------------------------- sprints
    def list_sprints(self, program: Program) -> list[ReferralSprint]:
        rows = self._request(
            "GET",
            _SPRINT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "sprint_id,name,window_start,window_end,ambassadors_enlisted,"
                    "families_identified,conversions,status"
                ),
                "order": "created_at.asc",
            },
        )
        return [_row_to_sprint(r) for r in rows]

    def create_sprint(
        self,
        program: Program,
        *,
        name: str,
        window_start: date,
        window_end: date,
        ambassadors_enlisted: int = 0,
        families_identified: int = 0,
        conversions: int = 0,
        status: str = "active",
    ) -> ReferralSprint:
        rows = self._request(
            "POST",
            _SPRINT_TABLE,
            payload={
                "name": name,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "ambassadors_enlisted": ambassadors_enlisted,
                "families_identified": families_identified,
                "conversions": conversions,
                "status": status,
                "program_id": program.value,
            },
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /referral_sprint returned no representation row")
        return _row_to_sprint(rows[0])

    # ---------------------------------------------------------------- market nodes
    def list_market_nodes(self, program: Program) -> list[MarketNode]:
        rows = self._request(
            "GET",
            _MARKET_NODE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "node_id,category,contact_label,status,leads_generated,last_activity,owner"
                ),
                "order": "created_at.asc",
            },
        )
        return [_row_to_node(r) for r in rows]

    def upsert_market_node(
        self,
        program: Program,
        *,
        node_id: UUID | None = None,
        category: str,
        contact_label: str = "",
        status: str = "cold",
        leads_generated: int = 0,
        last_activity: date | None = None,
        owner: str = "grassroots",
    ) -> MarketNode:
        payload: dict[str, Any] = {
            "category": category,
            "contact_label": contact_label,
            "status": status,
            "leads_generated": leads_generated,
            "last_activity": last_activity.isoformat() if last_activity is not None else None,
            "owner": owner,
            "program_id": program.value,
        }
        if node_id is not None:
            payload["node_id"] = str(node_id)
        rows = self._request(
            "POST",
            _MARKET_NODE_TABLE,
            params={"on_conflict": "node_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /market_node returned no representation row")
        return _row_to_node(rows[0])

    # ---------------------------------------------------------------------- events
    def list_events(self, program: Program) -> list[AmbassadorEvent]:
        rows = self._request(
            "GET",
            _EVENT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "event_id,event_name,host_ambassador_id,event_type,date,"
                    "location_label,rsvp_count,attendance_count,conversions_influenced"
                ),
                "order": "date.asc",
            },
        )
        return [_row_to_event(r) for r in rows]

    def create_event(
        self,
        program: Program,
        *,
        event_name: str,
        host_ambassador_id: UUID | None = None,
        event_type: str = "coffee_chat",
        date: date,
        location_label: str = "",
        rsvp_count: int = 0,
        attendance_count: int = 0,
        conversions_influenced: int = 0,
    ) -> AmbassadorEvent:
        rows = self._request(
            "POST",
            _EVENT_TABLE,
            payload={
                "event_name": event_name,
                "host_ambassador_id": str(host_ambassador_id)
                if host_ambassador_id is not None
                else None,
                "event_type": event_type,
                "date": date.isoformat(),
                "location_label": location_label,
                "rsvp_count": rsvp_count,
                "attendance_count": attendance_count,
                "conversions_influenced": conversions_influenced,
                "program_id": program.value,
            },
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /ambassador_event returned no representation row")
        return _row_to_event(rows[0])


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST ``date`` to a :class:`datetime.date`, or ``None`` when absent."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _parse_uuid(raw: object) -> UUID | None:
    """Parse a PostgREST uuid, or ``None`` when absent/malformed."""
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _row_to_ambassador(row: dict[str, Any]) -> Ambassador:
    """Map a PostgREST ``ambassador`` row to the :class:`Ambassador` accessor shape."""
    return Ambassador(
        ambassador_id=UUID(str(row["ambassador_id"])),
        synthetic_name=str(row["synthetic_name"]),
        synthetic_email=str(row["synthetic_email"]),
        segment=str(row.get("segment") or ""),
        region=str(row.get("region") or ""),
        status=str(row["status"]),
        intros=int(row.get("intros") or 0),
        p2p_calls=int(row.get("p2p_calls") or 0),
        last_touch=_parse_date(row.get("last_touch")),
        owner=str(row.get("owner") or "grassroots"),
    )


def _row_to_sprint(row: dict[str, Any]) -> ReferralSprint:
    """Map a PostgREST ``referral_sprint`` row to :class:`ReferralSprint`."""
    return ReferralSprint(
        sprint_id=UUID(str(row["sprint_id"])),
        name=str(row["name"]),
        window_start=_parse_date(row["window_start"]) or date.min,
        window_end=_parse_date(row["window_end"]) or date.min,
        ambassadors_enlisted=int(row.get("ambassadors_enlisted") or 0),
        families_identified=int(row.get("families_identified") or 0),
        conversions=int(row.get("conversions") or 0),
        status=str(row.get("status") or "active"),
    )


def _row_to_node(row: dict[str, Any]) -> MarketNode:
    """Map a PostgREST ``market_node`` row to :class:`MarketNode`."""
    return MarketNode(
        node_id=UUID(str(row["node_id"])),
        category=str(row["category"]),
        contact_label=str(row.get("contact_label") or ""),
        status=str(row.get("status") or "cold"),
        leads_generated=int(row.get("leads_generated") or 0),
        last_activity=_parse_date(row.get("last_activity")),
        owner=str(row.get("owner") or "grassroots"),
    )


def _row_to_event(row: dict[str, Any]) -> AmbassadorEvent:
    """Map a PostgREST ``ambassador_event`` row to :class:`AmbassadorEvent`."""
    return AmbassadorEvent(
        event_id=UUID(str(row["event_id"])),
        event_name=str(row["event_name"]),
        host_ambassador_id=_parse_uuid(row.get("host_ambassador_id")),
        event_type=str(row.get("event_type") or "coffee_chat"),
        date=_parse_date(row["date"]) or date.min,
        location_label=str(row.get("location_label") or ""),
        rsvp_count=int(row.get("rsvp_count") or 0),
        attendance_count=int(row.get("attendance_count") or 0),
        conversions_influenced=int(row.get("conversions_influenced") or 0),
    )


def build_supabase_grassroots_store() -> SupabaseGrassrootsStore | None:
    """Construct the Supabase grassroots store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.budget_store.build_supabase_budget_store`: reads
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
    return SupabaseGrassrootsStore(base_url=url, service_role_key=key)

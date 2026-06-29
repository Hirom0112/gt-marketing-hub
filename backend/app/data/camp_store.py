"""Summer-camp store (Module 4) — the registration / campus / session seam.

The Summer Camp module owns three pieces of program-scoped state behind the same
NFR-8 store seam as the grassroots/content stores: the camp REGISTRATIONS (one row
per source appearance — the dual-source reconcile INPUT, deduped downstream by
:mod:`app.core.summer_reconcile`), the CAMPUS capacity reference, and the camp
SESSIONS (the weekly cohorts + the countdown source). All synthetic/aggregate data
only — a household contact + an AGGREGATE grade band, never child PII (INV-1/INV-6).

- :class:`CampStore` — the ABC every camp route depends on.
- :class:`InMemoryCampStore` — the v1 / CI-tested local impl (pure, no I/O), with a
  deterministic :meth:`InMemoryCampStore.seed_demo` (no clock/random) that persists
  BOTH synthetic sources + a per-row signup channel + a registration-recency spread,
  plus the four campuses and the four Aug-2026 sessions.
- :class:`SupabaseCampStore` — the live impl over the 0032 ``camp_registration`` /
  ``campus`` + the 0037 ``camp_session`` tables (+ the 0037 registration columns), via
  the SAME PostgREST/service_role pattern as the grassroots store. Upserts pass
  ``on_conflict`` in the PostgREST URL (the bit that bit us before).

The reconcile core stays the single source of dedup truth — this store only SUPPLIES
rows (both sources, un-deduped); :func:`app.core.summer_reconcile.reconcile` collapses
them. Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules,
only the pure :class:`app.core.program.Program` enum, the reconcile core's identity
key + synthetic generator, :class:`app.core.params.Params`, and ``httpx``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.core.summer_reconcile import CampRegistration, _dedup_key
from app.data.supabase_repository import SupabaseError

if TYPE_CHECKING:
    from app.core.params import Params

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the grassroots store makes). The 0032/0037 names.
_REST = "/rest/v1"
_REGISTRATION_TABLE = f"{_REST}/camp_registration"
_CAMPUS_TABLE = f"{_REST}/campus"
_SESSION_TABLE = f"{_REST}/camp_session"
_PAYMENT_TABLE = f"{_REST}/camp_payment"

# The Stripe PI status a CHARGE that actually collected revenue carries. Only succeeded
# payments roll into collected revenue (a fixed Stripe wire token, INV-11 carve-out).
_SUCCEEDED_STATUS = "succeeded"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 4). PII-free (INV-1) + clock/random-free: ids are
# UUID(int=...), dates derive from a FIXED reference (no clock). The synthetic source
# rows come from app.data.synthetic_summer (288 unique / 219 paid across 4 campuses).
# ----------------------------------------------------------------------------- #

# The reference "now" the registration recency is anchored to (the build/demo date,
# 2026-06-28). "Registrations this week" = the last 7 days ENDING here; the seed lands
# exactly :data:`_RECENT_REGISTRATIONS` rows inside that window (days_ago 0..6) and
# spreads the rest back over weeks 2-8. The API injects the REAL now at the edge — at
# demo time (late Jun 2026) the two coincide; the test injects THIS reference for a
# deterministic count.
_REGISTRATION_REF: datetime = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

# How many DEDUPED registrants land inside the most-recent 7-day window (a real recent
# count, not faked) — spread across days 0..6 so the week reads naturally.
_RECENT_REGISTRATIONS: int = 30
# The older registrants spread over weeks 2-8 (days 7..55 = 49 days).
_OLDER_SPREAD_DAYS: int = 49
_RECENT_WINDOW_DAYS: int = 7

# Per-channel relative weights, aligned to params.summer_camp.registration_channels
# ORDER (word_of_mouth, social, email, website). word_of_mouth is the clear top
# (~40%): 8/20 of the repeating pattern. These are documented seed constants (the
# distribution shape), NOT business tunables — the LABELS live in params (INV-11). A
# channel beyond this list gets weight 1 (robust to a params edit).
_CHANNEL_WEIGHTS: tuple[int, ...] = (8, 5, 4, 3)  # → 40% / 25% / 20% / 15%

# Per-campus city label + duration (matches 0032's campus seed; aggregate, no precise
# geo — INV-6). Capacities come from params.summer_camp.campus_capacity (INV-11).
_CAMPUS_META: dict[str, tuple[str, str]] = {
    "Austin": ("Mueller campus", "2wk"),
    "Dallas": ("Knox-Henderson campus", "2wk"),
    "Houston": ("Heights campus", "2wk"),
    "San Antonio": ("Pearl campus", "1wk"),
}

# The four Aug-2026 camp sessions (3× two-week + 1× one-week; San Antonio is the
# one-week). (campus, starts_on, ends_on, duration) — the earliest start drives the
# countdown. Capacity per session = the campus capacity (params, INV-11).
_SESSIONS: tuple[tuple[str, date, date, str], ...] = (
    ("Austin", date(2026, 8, 3), date(2026, 8, 14), "2wk"),
    ("Dallas", date(2026, 8, 3), date(2026, 8, 14), "2wk"),
    ("Houston", date(2026, 8, 10), date(2026, 8, 21), "2wk"),
    ("San Antonio", date(2026, 8, 17), date(2026, 8, 21), "1wk"),
)

# Deterministic id bases (demo-only, like the grassroots seed's 0x6A55_0000). Distinct
# high bits per kind so registration / session ids never collide.
_REG_ID_BASE = 0xCA37_0000  # "CAmp ReG"
_SESSION_ID_BASE = 0xCA37_5E50  # "CAmp SESsion"

_DEFAULT_SESSION_STATUS = "scheduled"


@dataclass(frozen=True)
class CampRegistrationRow:
    """One camp registration AS SEEN IN ONE SOURCE (the 0032/0037 storage grain).

    The two overlapping sources are deduped on the household identity key by the
    reconcile core; this row is the per-source storage grain. INV-1/INV-6: a synthetic
    household contact + an AGGREGATE grade band only — NEVER a child name / DOB / geo.

    Attributes:
        registration_id: The row PK.
        source: ``"summer_site"`` | ``"registration_form"`` (the dedup provenance).
        external_id: The source's own opaque id (never child PII).
        campus: The campus the registrant signed up for.
        child_grade_band: An AGGREGATE band (``"K-2"`` …) — never a child key (INV-6).
        synthetic_email: Household contact email (synthetic; INV-1) — primary dedup key.
        synthetic_phone: Household contact phone (synthetic; INV-1) — fallback key.
        paid: Whether the registration is paid (vs a registered-but-unpaid lead).
        registration_channel: How the family signed up (a params channel label).
        attended: Whether the child attended (camp is FUTURE ⇒ honestly False here).
        registered_at: When the registration arrived (the recent-window source).
    """

    registration_id: UUID
    source: str
    external_id: str
    campus: str
    child_grade_band: str
    synthetic_email: str | None
    synthetic_phone: str | None
    paid: bool
    registration_channel: str | None
    attended: bool
    registered_at: datetime | None

    def to_core(self) -> CampRegistration:
        """Project onto the pure-core :class:`CampRegistration` (the reconcile input)."""
        return CampRegistration(
            external_id=self.external_id,
            source=self.source,
            campus=self.campus,
            child_grade_band=self.child_grade_band,
            synthetic_email=self.synthetic_email,
            synthetic_phone=self.synthetic_phone,
            paid=self.paid,
            registration_channel=self.registration_channel,
            attended=self.attended,
            registered_at=self.registered_at,
        )


@dataclass(frozen=True)
class Campus:
    """One campus capacity-reference row (the seat universe; aggregate — INV-6)."""

    campus: str
    city: str
    capacity: int
    duration: str


@dataclass(frozen=True)
class CampSession:
    """One weekly camp cohort (the session calendar + the countdown source)."""

    session_id: UUID
    campus: str
    starts_on: date
    ends_on: date
    duration: str
    capacity: int
    status: str


@dataclass(frozen=True)
class CampPayment:
    """One fulfilled camp PaymentIntent — the collected-revenue grain (0038).

    The Stripe webhook appends one of these per camp PaymentIntent it fulfills
    (``metadata.program == 'summer_camp'``). IDEMPOTENT on ``payment_id`` (the Stripe
    PI id): the same PI recorded twice (Stripe's at-least-once redelivery) merges, never
    double-counts. INV-1/INV-6: NO PII — the PI id, an aggregate campus label, the amount
    (minor units), currency, status, and the source Stripe event id only.

    Attributes:
        payment_id: The Stripe PaymentIntent id (``pi_…``) — the idempotency key.
        campus: The aggregate campus label (from the PI's ``metadata.campus``).
        amount_cents: The charge amount in the currency's minor unit.
        currency: The ISO currency code (e.g. ``"usd"``).
        status: The PI status (``"succeeded"`` for a collected charge).
        stripe_event_id: The source Stripe ``event.id`` (``evt_…``) for the audit trail.
    """

    payment_id: str
    campus: str
    amount_cents: int
    currency: str
    status: str
    stripe_event_id: str


class CampStore(ABC):
    """Read/write seam over the Module-4 Summer Camp state (0032 + 0037).

    Every camp route depends on this interface, never a concrete store. v1 binds the
    in-memory impl (seed-driven); production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped (the
    0032 tenancy tag) so one program's registrations never bleed into another's.
    """

    # ----------------------------------------------------------------- registrations
    @abstractmethod
    def list_registrations(self, program: Program) -> list[CampRegistrationRow]:
        """ALL registration rows for ``program`` (BOTH sources — the reconcile input)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_registration(
        self,
        program: Program,
        *,
        registration_id: UUID | None = None,
        source: str,
        external_id: str,
        campus: str,
        child_grade_band: str,
        synthetic_email: str | None,
        synthetic_phone: str | None,
        paid: bool = False,
        registration_channel: str | None = None,
        attended: bool = False,
        registered_at: datetime | None = None,
    ) -> CampRegistrationRow:
        """Insert or update one registration (keyed by ``registration_id``); return it."""
        raise NotImplementedError

    # ---------------------------------------------------------------------- campuses
    @abstractmethod
    def list_campuses(self, program: Program) -> list[Campus]:
        """The campus capacity reference for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_campus(
        self,
        program: Program,
        *,
        campus: str,
        city: str,
        capacity: int,
        duration: str,
    ) -> Campus:
        """Insert or update one campus (keyed by ``campus``); return it."""
        raise NotImplementedError

    # ---------------------------------------------------------------------- sessions
    @abstractmethod
    def list_sessions(self, program: Program) -> list[CampSession]:
        """The camp sessions for ``program`` (the cohort calendar + countdown source)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_session(
        self,
        program: Program,
        *,
        session_id: UUID | None = None,
        campus: str,
        starts_on: date,
        ends_on: date,
        duration: str,
        capacity: int,
        status: str = _DEFAULT_SESSION_STATUS,
    ) -> CampSession:
        """Insert or update one camp session (keyed by ``session_id``); return it."""
        raise NotImplementedError

    # ----------------------------------------------------------------- payments
    @abstractmethod
    def record_camp_payment(
        self,
        program: Program,
        *,
        payment_id: str,
        campus: str,
        amount_cents: int,
        currency: str,
        status: str,
        stripe_event_id: str,
    ) -> CampPayment:
        """IDEMPOTENT upsert of one fulfilled camp PaymentIntent (keyed by ``payment_id``).

        Recording the SAME PaymentIntent twice (Stripe's at-least-once redelivery) must
        NOT double-count — the upsert merges on ``payment_id``. Returns the stored row.
        """
        raise NotImplementedError

    @abstractmethod
    def list_camp_payments(self, program: Program) -> list[CampPayment]:
        """ALL camp payment rows for ``program`` (the collected-revenue ledger)."""
        raise NotImplementedError

    # ------------------------------------------------------- collected revenue
    def collected_revenue(self, program: Program) -> dict[str, Any]:
        """Sum the SUCCEEDED camp payments into ``{total_cents, by_campus, count}``.

        A pure read over :meth:`list_camp_payments` (works for every concrete store):
        only ``status == 'succeeded'`` rows count toward collected revenue. ``by_campus``
        maps each campus to its succeeded total (minor units); ``count`` is the number of
        succeeded payments. An empty ledger yields zeros — the caller then falls back to
        the synthetic paid × price estimate.
        """
        total_cents = 0
        by_campus: dict[str, int] = {}
        count = 0
        for payment in self.list_camp_payments(program):
            if payment.status != _SUCCEEDED_STATUS:
                continue
            total_cents += payment.amount_cents
            by_campus[payment.campus] = by_campus.get(payment.campus, 0) + payment.amount_cents
            count += 1
        return {"total_cents": total_cents, "by_campus": by_campus, "count": count}


class InMemoryCampStore(CampStore):
    """In-memory :class:`CampStore` — per-program lists; pure, no I/O.

    The v1 local store and the CI-tested path. A production deploy swaps
    :class:`SupabaseCampStore` behind the same seam. :meth:`seed_demo` lays down the
    deterministic demo registrations/campuses/sessions (idempotent). The seed reads the
    channel LABELS + the recent-window size from ``params`` (INV-11), so a clean store
    for tests is built with no params (no seed needed).
    """

    def __init__(self, *, params: Params | None = None) -> None:
        self._params = params
        self._registrations: dict[Program, list[CampRegistrationRow]] = {}
        self._campuses: dict[Program, list[Campus]] = {}
        self._sessions: dict[Program, list[CampSession]] = {}
        # Camp payments keyed by payment_id per program so an upsert is intrinsically
        # idempotent (a redelivered PaymentIntent replaces, never appends).
        self._payments: dict[Program, dict[str, CampPayment]] = {}
        self._seeded: set[Program] = set()

    # ----------------------------------------------------------------- registrations
    def list_registrations(self, program: Program) -> list[CampRegistrationRow]:
        return list(self._registrations.get(program, []))

    def upsert_registration(
        self,
        program: Program,
        *,
        registration_id: UUID | None = None,
        source: str,
        external_id: str,
        campus: str,
        child_grade_band: str,
        synthetic_email: str | None,
        synthetic_phone: str | None,
        paid: bool = False,
        registration_channel: str | None = None,
        attended: bool = False,
        registered_at: datetime | None = None,
    ) -> CampRegistrationRow:
        row = CampRegistrationRow(
            registration_id=registration_id if registration_id is not None else uuid4(),
            source=source,
            external_id=external_id,
            campus=campus,
            child_grade_band=child_grade_band,
            synthetic_email=synthetic_email,
            synthetic_phone=synthetic_phone,
            paid=paid,
            registration_channel=registration_channel,
            attended=attended,
            registered_at=registered_at,
        )
        rows = self._registrations.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.registration_id == row.registration_id:
                rows[i] = row
                return row
        rows.append(row)
        return row

    # ---------------------------------------------------------------------- campuses
    def list_campuses(self, program: Program) -> list[Campus]:
        return list(self._campuses.get(program, []))

    def upsert_campus(
        self,
        program: Program,
        *,
        campus: str,
        city: str,
        capacity: int,
        duration: str,
    ) -> Campus:
        row = Campus(campus=campus, city=city, capacity=capacity, duration=duration)
        rows = self._campuses.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.campus == row.campus:
                rows[i] = row
                return row
        rows.append(row)
        return row

    # ---------------------------------------------------------------------- sessions
    def list_sessions(self, program: Program) -> list[CampSession]:
        return list(self._sessions.get(program, []))

    def upsert_session(
        self,
        program: Program,
        *,
        session_id: UUID | None = None,
        campus: str,
        starts_on: date,
        ends_on: date,
        duration: str,
        capacity: int,
        status: str = _DEFAULT_SESSION_STATUS,
    ) -> CampSession:
        row = CampSession(
            session_id=session_id if session_id is not None else uuid4(),
            campus=campus,
            starts_on=starts_on,
            ends_on=ends_on,
            duration=duration,
            capacity=capacity,
            status=status,
        )
        rows = self._sessions.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.session_id == row.session_id:
                rows[i] = row
                return row
        rows.append(row)
        return row

    # ----------------------------------------------------------------- payments
    def record_camp_payment(
        self,
        program: Program,
        *,
        payment_id: str,
        campus: str,
        amount_cents: int,
        currency: str,
        status: str,
        stripe_event_id: str,
    ) -> CampPayment:
        payment = CampPayment(
            payment_id=payment_id,
            campus=campus,
            amount_cents=amount_cents,
            currency=currency,
            status=status,
            stripe_event_id=stripe_event_id,
        )
        # Keyed by payment_id ⇒ the same PaymentIntent recorded twice merges (no
        # double-count), exactly the at-least-once-delivery contract.
        self._payments.setdefault(program, {})[payment_id] = payment
        return payment

    def list_camp_payments(self, program: Program) -> list[CampPayment]:
        return list(self._payments.get(program, {}).values())

    # ------------------------------------------------------------------ demo seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo camp state (INV-1; idempotent).

        Persists BOTH synthetic sources (``generate_summer_sources`` — 288 unique /
        219 paid across the four campuses) WITH a deterministic signup channel per
        registrant (a weighted spread making ``word_of_mouth`` the clear top, ~40%) and
        a deterministic ``registered_at`` (exactly :data:`_RECENT_REGISTRATIONS` inside
        the last 7 days of :data:`_REGISTRATION_REF`, the rest back over weeks 2-8).
        ``attended`` is False for every row — camp is in the FUTURE (honest). Also seeds
        the four campuses (capacity from params) + the four Aug-2026 sessions.

        Channel + recency are keyed off the DEDUPED registrant (both source rows of one
        registrant share them), so the breakdown/recency dedup cleanly. Clock/random-
        free: ids are ``UUID(int=...)``; dates derive from the fixed reference. Re-seed
        is a guarded no-op.
        """
        if program in self._seeded:
            return
        if self._params is None:
            raise ValueError(
                "InMemoryCampStore.seed_demo requires params (summer_camp.campus_capacity "
                "+ registration_channels)"
            )

        from app.data.synthetic_summer import generate_summer_sources

        camp = self._params.summer_camp
        capacities = camp.campus_capacity

        # Campuses (capacity from params; city/duration from the documented meta).
        for campus_name, capacity in capacities.items():
            city, duration = _CAMPUS_META.get(campus_name, (campus_name, "2wk"))
            self.upsert_campus(
                program,
                campus=campus_name,
                city=city,
                capacity=capacity,
                duration=duration,
            )

        # Sessions (capacity from params per campus).
        for i, (campus_name, starts_on, ends_on, duration) in enumerate(_SESSIONS):
            self.upsert_session(
                program,
                session_id=UUID(int=_SESSION_ID_BASE + i),
                campus=campus_name,
                starts_on=starts_on,
                ends_on=ends_on,
                duration=duration,
                capacity=capacities.get(campus_name, 0),
                status=_DEFAULT_SESSION_STATUS,
            )

        # Registrations — both sources, with a per-registrant channel + recency.
        site, form = generate_summer_sources()
        union = [*site, *form]
        pattern = _channel_pattern(camp.registration_channels)

        # First-seen position per DEDUPED registrant (stable union order) — both rows of
        # one registrant share the position ⇒ share channel + registered_at.
        positions: dict[str, int] = {}
        for core_row in union:
            key = _dedup_key(core_row) or f"ext:{core_row.external_id}"
            if key not in positions:
                positions[key] = len(positions)

        for idx, core_row in enumerate(union):
            key = _dedup_key(core_row) or f"ext:{core_row.external_id}"
            p = positions[key]
            channel = pattern[p % len(pattern)]
            registered_at = _REGISTRATION_REF - timedelta(days=_days_ago_for(p))
            self.upsert_registration(
                program,
                registration_id=UUID(int=_REG_ID_BASE + idx),
                source=core_row.source,
                external_id=core_row.external_id,
                campus=core_row.campus,
                child_grade_band=core_row.child_grade_band,
                synthetic_email=core_row.synthetic_email,
                synthetic_phone=core_row.synthetic_phone,
                paid=core_row.paid,
                registration_channel=channel,
                attended=False,  # camp is in the future — honestly not attended yet
                registered_at=registered_at,
            )

        self._seeded.add(program)


def _channel_pattern(channels: list[str]) -> tuple[str, ...]:
    """Build the repeating weighted channel pattern (word_of_mouth the clear top).

    Each channel is repeated by its weight (:data:`_CHANNEL_WEIGHTS` by position, 1 for
    any channel beyond it), so assigning ``pattern[position % len(pattern)]`` yields the
    documented ~40/25/20/15 split. Deterministic; depends only on the params labels.
    """
    pattern: list[str] = []
    for i, channel in enumerate(channels):
        weight = _CHANNEL_WEIGHTS[i] if i < len(_CHANNEL_WEIGHTS) else 1
        pattern.extend([channel] * weight)
    return tuple(pattern) if pattern else ("unknown",)


def _days_ago_for(position: int) -> int:
    """Days-ago offset from the reference for the ``position``-th deduped registrant.

    The first :data:`_RECENT_REGISTRATIONS` (positions 0..29) spread across days 0..6 —
    EXACTLY that many land inside the most-recent 7-day window; the rest spread over
    days 7..55 (weeks 2-8). Deterministic, clock-free.
    """
    if position < _RECENT_REGISTRATIONS:
        return position % _RECENT_WINDOW_DAYS
    return _RECENT_WINDOW_DAYS + ((position - _RECENT_REGISTRATIONS) % _OLDER_SPREAD_DAYS)


class SupabaseCampStore(CampStore):
    """Live :class:`CampStore` over Supabase PostgREST (service_role; 0032 + 0037).

    Query-per-request (the grassroots store's stateless posture): each call issues a
    fresh PostgREST request over the injected (or per-call) ``httpx`` client. Every
    table is program-scoped (``program_id`` is the 0032 tenancy tag) so every read
    filters and every write stamps it. Upserts pass ``on_conflict`` in the PostgREST
    URL. The ``service_role`` key BYPASSES RLS (server-only — INV-5 / D-RLS-4) and
    never leaves the backend.
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

    # ----------------------------------------------------------------- registrations
    def list_registrations(self, program: Program) -> list[CampRegistrationRow]:
        rows = self._request(
            "GET",
            _REGISTRATION_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "registration_id,source,external_id,campus,child_grade_band,"
                    "synthetic_email,synthetic_phone,paid,registration_channel,"
                    "attended,registered_at"
                ),
                "order": "created_at.asc",
            },
        )
        return [_row_to_registration(r) for r in rows]

    def upsert_registration(
        self,
        program: Program,
        *,
        registration_id: UUID | None = None,
        source: str,
        external_id: str,
        campus: str,
        child_grade_band: str,
        synthetic_email: str | None,
        synthetic_phone: str | None,
        paid: bool = False,
        registration_channel: str | None = None,
        attended: bool = False,
        registered_at: datetime | None = None,
    ) -> CampRegistrationRow:
        payload: dict[str, Any] = {
            "source": source,
            "external_id": external_id,
            "campus": campus,
            "child_grade_band": child_grade_band,
            "synthetic_email": synthetic_email,
            "synthetic_phone": synthetic_phone,
            "paid": paid,
            "registration_channel": registration_channel,
            "attended": attended,
            "registered_at": registered_at.isoformat() if registered_at is not None else None,
            "program_id": program.value,
        }
        # camp_registration's PK has no DB default — mint one when the caller omits it.
        rid = registration_id if registration_id is not None else uuid4()
        payload["registration_id"] = str(rid)
        rows = self._request(
            "POST",
            _REGISTRATION_TABLE,
            params={"on_conflict": "registration_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /camp_registration returned no representation row")
        return _row_to_registration(rows[0])

    # ---------------------------------------------------------------------- campuses
    def list_campuses(self, program: Program) -> list[Campus]:
        rows = self._request(
            "GET",
            _CAMPUS_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "campus,city,capacity,duration",
                "order": "campus.asc",
            },
        )
        return [_row_to_campus(r) for r in rows]

    def upsert_campus(
        self,
        program: Program,
        *,
        campus: str,
        city: str,
        capacity: int,
        duration: str,
    ) -> Campus:
        rows = self._request(
            "POST",
            _CAMPUS_TABLE,
            params={"on_conflict": "campus"},
            payload={
                "campus": campus,
                "city": city,
                "capacity": capacity,
                "duration": duration,
                "program_id": program.value,
            },
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /campus returned no representation row")
        return _row_to_campus(rows[0])

    # ---------------------------------------------------------------------- sessions
    def list_sessions(self, program: Program) -> list[CampSession]:
        rows = self._request(
            "GET",
            _SESSION_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "session_id,campus,starts_on,ends_on,duration,capacity,status",
                "order": "starts_on.asc",
            },
        )
        return [_row_to_session(r) for r in rows]

    def upsert_session(
        self,
        program: Program,
        *,
        session_id: UUID | None = None,
        campus: str,
        starts_on: date,
        ends_on: date,
        duration: str,
        capacity: int,
        status: str = _DEFAULT_SESSION_STATUS,
    ) -> CampSession:
        payload: dict[str, Any] = {
            "campus": campus,
            "starts_on": starts_on.isoformat(),
            "ends_on": ends_on.isoformat(),
            "duration": duration,
            "capacity": capacity,
            "status": status,
            "program_id": program.value,
        }
        payload["session_id"] = str(session_id if session_id is not None else uuid4())
        rows = self._request(
            "POST",
            _SESSION_TABLE,
            params={"on_conflict": "session_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /camp_session returned no representation row")
        return _row_to_session(rows[0])

    # ----------------------------------------------------------------- payments
    def list_camp_payments(self, program: Program) -> list[CampPayment]:
        rows = self._request(
            "GET",
            _PAYMENT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "payment_id,campus,amount_cents,currency,status,stripe_event_id",
                "order": "created_at.asc",
            },
        )
        return [_row_to_payment(r) for r in rows]

    def record_camp_payment(
        self,
        program: Program,
        *,
        payment_id: str,
        campus: str,
        amount_cents: int,
        currency: str,
        status: str,
        stripe_event_id: str,
    ) -> CampPayment:
        payload: dict[str, Any] = {
            "payment_id": payment_id,
            "campus": campus,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": status,
            "stripe_event_id": stripe_event_id,
            "program_id": program.value,
        }
        # IDEMPOTENT: on_conflict=payment_id merges a redelivered PaymentIntent onto the
        # existing row (no double-count), the same on_conflict-in-URL pattern as the
        # registration/campus/session upserts above.
        rows = self._request(
            "POST",
            _PAYMENT_TABLE,
            params={"on_conflict": "payment_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /camp_payment returned no representation row")
        return _row_to_payment(rows[0])


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST ``date`` to a :class:`datetime.date`, or ``None`` when absent."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _parse_datetime(raw: object) -> datetime | None:
    """Parse a PostgREST ``timestamptz`` to an aware datetime, or ``None`` when absent."""
    if not raw:
        return None
    text = str(raw)
    # PostgREST renders UTC as a trailing 'Z'; fromisoformat wants '+00:00'.
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _row_to_registration(row: dict[str, Any]) -> CampRegistrationRow:
    """Map a PostgREST ``camp_registration`` row to :class:`CampRegistrationRow`."""
    return CampRegistrationRow(
        registration_id=UUID(str(row["registration_id"])),
        source=str(row["source"]),
        external_id=str(row["external_id"]),
        campus=str(row["campus"]),
        child_grade_band=str(row["child_grade_band"]),
        synthetic_email=(str(row["synthetic_email"]) if row.get("synthetic_email") else None),
        synthetic_phone=(str(row["synthetic_phone"]) if row.get("synthetic_phone") else None),
        paid=bool(row.get("paid")),
        registration_channel=(
            str(row["registration_channel"]) if row.get("registration_channel") else None
        ),
        attended=bool(row.get("attended")),
        registered_at=_parse_datetime(row.get("registered_at")),
    )


def _row_to_campus(row: dict[str, Any]) -> Campus:
    """Map a PostgREST ``campus`` row to :class:`Campus`."""
    return Campus(
        campus=str(row["campus"]),
        city=str(row.get("city") or ""),
        capacity=int(row.get("capacity") or 0),
        duration=str(row.get("duration") or ""),
    )


def _row_to_session(row: dict[str, Any]) -> CampSession:
    """Map a PostgREST ``camp_session`` row to :class:`CampSession`."""
    return CampSession(
        session_id=UUID(str(row["session_id"])),
        campus=str(row["campus"]),
        starts_on=_parse_date(row["starts_on"]) or date.min,
        ends_on=_parse_date(row["ends_on"]) or date.min,
        duration=str(row.get("duration") or ""),
        capacity=int(row.get("capacity") or 0),
        status=str(row.get("status") or _DEFAULT_SESSION_STATUS),
    )


def _row_to_payment(row: dict[str, Any]) -> CampPayment:
    """Map a PostgREST ``camp_payment`` row to :class:`CampPayment`."""
    return CampPayment(
        payment_id=str(row["payment_id"]),
        campus=str(row.get("campus") or ""),
        amount_cents=int(row.get("amount_cents") or 0),
        currency=str(row.get("currency") or ""),
        status=str(row.get("status") or ""),
        stripe_event_id=str(row.get("stripe_event_id") or ""),
    )


def build_supabase_camp_store() -> SupabaseCampStore | None:
    """Construct the Supabase camp store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.grassroots_store.build_supabase_grassroots_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` from the environment at the
    composition root, returning ``None`` when either is absent or a placeholder
    ``<...>`` sentinel — so the caller falls back to the in-memory store.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseCampStore(base_url=url, service_role_key=key)

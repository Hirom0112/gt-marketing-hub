"""Nurture & Lifecycle store (Module 5) — the program-scoped synthetic-mirror seam.

The Nurture surface owns four pieces of program-scoped state behind the same NFR-8 store
seam as the field-events/content stores (migration 0040):

- ``nurture_segment``  — saved audience SEGMENTS (a T1/T2/T3 tier + sub-bucket + sized,
  reachability-tagged audience). Aggregate attribute filters only (INV-1/INV-6).
- ``nurture_sequence`` — the READ-ONLY synthetic MIRROR of a HubSpot Sales-Hub sequence
  (the Sequences API is NOT available in this portal, so per-step open/click/conversion
  rates are synthetic, clearly labeled ``source="synthetic_mirror"`` in the API).
- ``sms_thread``       — SMS inbox threads. ``contact_label`` is a SYNTHETIC token
  (e.g. "Family #A12"), NEVER a real name/phone (INV-1/INV-6).
- ``sla_contact``      — first-contact SLA timer rows (entered_at → contacted_at). The
  ``applicant_label`` is a SYNTHETIC token, never PII.

- :class:`NurtureStore` — the ABC every nurture route depends on.
- :class:`InMemoryNurtureStore` — the v1 / CI-tested local impl (pure, no I/O), with a
  deterministic :meth:`InMemoryNurtureStore.seed_demo` (no clock/random).
- :class:`SupabaseNurtureStore` — the live impl over the 0040 tables, via the SAME
  PostgREST/service_role pattern as the field-events store. Upserts pass ``on_conflict``
  in the PostgREST URL (the bit that bit us before).

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.program.Program` enum and ``httpx`` (the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the field-events store makes). The 0040 names.
_REST = "/rest/v1"
_SEGMENT_TABLE = f"{_REST}/nurture_segment"
_SEQUENCE_TABLE = f"{_REST}/nurture_sequence"
_SMS_TABLE = f"{_REST}/sms_thread"
_SLA_TABLE = f"{_REST}/sla_contact"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 5). PII-free (INV-1) + clock/random-free: ids are
# UUID(int=...), datetimes derive from a FIXED epoch (no clock). Calibrated so the six
# sub-views render sensibly but NOT maxed.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

# Owner/rep routing tokens for the SLA log (synthetic, not PII).
_SEED_OWNERS: tuple[str, ...] = ("rep_morgan", "rep_sasha", "rep_lee")

# (tier, sub_bucket, label, attribute_filters, size, reachability_pct, notes) — ~6
# segments across T1/T2/T3 (aggregate bucket labels only; INV-6).
_SEED_SEGMENTS: tuple[tuple[str, str, str, dict[str, Any], int, float, str], ...] = (
    (
        "T1",
        "ready_high_income",
        "T1 · Ready, >$160K",
        {"engagement_tier": "clicked", "income": "gt_160k"},
        40,
        92.0,
        "Hot, high-intent, fully reachable — call first.",
    ),
    (
        "T1",
        "ready_voucher",
        "T1 · Ready, voucher-track",
        {"engagement_tier": "clicked", "income": "65k_160k"},
        85,
        88.0,
        "High intent, TEFA-eligible — fast-track funding.",
    ),
    (
        "T2",
        "warm_mid_funnel",
        "T2 · Warm mid-funnel",
        {"engagement_tier": "opened", "income": "65k_160k"},
        1600,
        61.0,
        "Opened but not clicked — needs a nudge sequence.",
    ),
    (
        "T2",
        "warm_southwest",
        "T2 · Warm, Southwest region",
        {"engagement_tier": "opened", "region": "TX"},
        1500,
        58.0,
        "Regional warm pool — pair with field events.",
    ),
    (
        "T3",
        "cold_longhorizon",
        "T3 · Cold, long-horizon",
        {"engagement_tier": "cold", "grade": "incoming_k"},
        700,
        18.0,
        "Future-grade families — long drip, not lost.",
    ),
    (
        "T3",
        "cold_reengage",
        "T3 · Cold, re-engage",
        {"engagement_tier": "cold"},
        424,
        15.0,
        "Gone quiet — re-engagement sequence candidates.",
    ),
)

# (name, seq_type, audience_size, steps) — ~5 sequences across the types. Each step is
# (step, open_pct, click_pct, conversion_pct). One welcome seq is the deliberate
# laggard (low open/click ⇒ health_flag true).
_SEED_SEQUENCES: tuple[tuple[str, str, int, tuple[tuple[int, float, float, float], ...]], ...] = (
    (
        "Welcome — new applicant",
        "welcome",
        420,
        ((1, 68.0, 22.0, 9.0), (2, 54.0, 14.0, 6.0), (3, 41.0, 9.0, 4.0)),
    ),
    (
        "Nurture — mid-funnel drip",
        "nurture",
        1600,
        ((1, 47.0, 11.0, 3.0), (2, 39.0, 8.0, 2.0), (3, 33.0, 6.0, 2.0), (4, 28.0, 5.0, 1.0)),
    ),
    (
        "Re-engagement — gone cold",
        "re_engagement",
        1100,
        ((1, 24.0, 4.0, 1.0), (2, 18.0, 3.0, 1.0)),  # the laggard ⇒ unhealthy
    ),
    (
        "Event — shadow-day invite",
        "event",
        300,
        ((1, 72.0, 31.0, 14.0), (2, 58.0, 19.0, 8.0)),
    ),
    (
        "Waitlist — hold warm",
        "waitlist",
        140,
        ((1, 61.0, 17.0, 5.0), (2, 49.0, 12.0, 4.0), (3, 38.0, 9.0, 3.0)),
    ),
)

# (contact_label, last_message, theme_tags, status, replied, inbound_offset_hours) — ~14
# SMS threads across all statuses + themes. contact_label is a SYNTHETIC token (INV-1);
# last_message is synthetic content seeded to match the params theme keyword rules.
_SEED_SMS: tuple[tuple[str, str, list[str], str, bool, int], ...] = (
    ("Family #A12", "How much is tuition for two kids?", ["tuition"], "objection", False, -3),
    ("Family #B07", "Is this a real accredited school?", ["accreditation"], "objection", False, -6),
    ("Family #C31", "Can we reschedule the tour?", ["scheduling"], "unread", False, -2),
    ("Family #D44", "We're ready to enroll!", ["ready"], "hot_family", True, -1),
    (
        "Family #E18",
        "What's the price after the scholarship?",
        ["tuition"],
        "hot_family",
        False,
        -4,
    ),
    ("Family #F90", "stop texting me please", ["no_reply"], "no_reply", False, -9),
    ("Family #G22", "When does fall start?", ["scheduling"], "unread", False, -5),
    ("Family #H55", "Sign us up, where do we deposit?", ["ready"], "ready", True, -1),
    ("Family #J03", "Too expensive for us right now", ["tuition"], "objection", False, -7),
    ("Family #K61", "Do you give real diplomas?", ["accreditation"], "unread", False, -8),
    ("Family #L29", "busy, talk later", ["no_reply"], "no_reply", False, -12),
    (
        "Family #M74",
        "Yes we want to start in August",
        ["ready", "scheduling"],
        "hot_family",
        True,
        -2,
    ),
    ("Family #N38", "What time is the info session?", ["scheduling"], "unread", False, -3),
    ("Family #P81", "Thanks, just looking for now", [], "unread", False, -10),
)


@dataclass(frozen=True)
class NurtureSegment:
    """One saved audience segment (aggregate; INV-1/INV-6)."""

    segment_id: UUID
    tier: str
    sub_bucket: str
    label: str
    attribute_filters: dict[str, Any]
    size: int
    reachability_pct: float
    owner: str
    notes: str


@dataclass(frozen=True)
class SequenceStep:
    """One synthetic sequence step's per-step perf (the read-only mirror)."""

    step: int
    open_pct: float
    click_pct: float
    conversion_pct: float


@dataclass(frozen=True)
class NurtureSequence:
    """One synthetic-mirror sequence (read-only; the Sequences API is unavailable)."""

    sequence_id: UUID
    name: str
    seq_type: str
    audience_size: int
    step_count: int
    steps: list[SequenceStep]
    health_flag: bool
    status: str


@dataclass(frozen=True)
class SmsThread:
    """One SMS inbox thread. ``contact_label`` is a SYNTHETIC token (INV-1)."""

    thread_id: UUID
    contact_label: str
    last_message: str
    theme_tags: list[str] = field(default_factory=list)
    status: str = "unread"
    replied: bool = False
    inbound_at: datetime | None = None


@dataclass(frozen=True)
class SlaContact:
    """One first-contact SLA timer row. ``applicant_label`` is SYNTHETIC (INV-1)."""

    contact_id: UUID
    applicant_label: str
    entered_at: datetime
    contacted_at: datetime | None
    owner: str


# The mutable columns an `update_thread` partial change may target.
_THREAD_UPDATABLE: frozenset[str] = frozenset({"status", "replied", "theme_tags", "last_message"})

# SMS statuses — the closed wire set (the 0040 CHECK mirrors these; INV-11 carve-out).
SMS_STATUSES: tuple[str, ...] = ("unread", "no_reply", "objection", "hot_family", "ready")


class NurtureStore(ABC):
    """Read/write seam over the Module-5 nurture state (migration 0040).

    Every nurture route depends on this interface, never a concrete store. v1 binds the
    in-memory impl (seed-driven); production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped (the
    0040 tenancy tag) so one program's nurture state never bleeds into another's.
    """

    # ---------------------------------------------------------------- segments
    @abstractmethod
    def list_segments(self, program: Program) -> list[NurtureSegment]:
        """The saved audience segments for ``program`` (insertion/created order)."""
        raise NotImplementedError

    @abstractmethod
    def create_segment(
        self,
        program: Program,
        *,
        segment_id: UUID | None = None,
        tier: str,
        sub_bucket: str = "",
        label: str = "",
        attribute_filters: dict[str, Any] | None = None,
        size: int = 0,
        reachability_pct: float = 0.0,
        owner: str = "nurture",
        notes: str = "",
    ) -> NurtureSegment:
        """Create one segment (gen a uuid when ``segment_id`` is None); return it."""
        raise NotImplementedError

    # --------------------------------------------------------------- sequences
    @abstractmethod
    def list_sequences(self, program: Program) -> list[NurtureSequence]:
        """The synthetic-mirror sequences for ``program`` (read-only)."""
        raise NotImplementedError

    # --------------------------------------------------------------- sms inbox
    @abstractmethod
    def list_sms_threads(self, program: Program) -> list[SmsThread]:
        """The SMS inbox threads for ``program`` (insertion/created order)."""
        raise NotImplementedError

    @abstractmethod
    def get_thread(self, program: Program, thread_id: UUID) -> SmsThread | None:
        """One SMS thread by id, or ``None`` when absent."""
        raise NotImplementedError

    @abstractmethod
    def update_thread(self, program: Program, thread_id: UUID, **changes: Any) -> SmsThread:
        """Partially update one SMS thread (mark replied / change status / retag).

        Only the columns in :data:`_THREAD_UPDATABLE` may change. Raises ``KeyError`` on
        an unknown ``thread_id`` (the route maps it to a 404).
        """
        raise NotImplementedError

    # ----------------------------------------------------------------- sla log
    @abstractmethod
    def list_sla_contacts(self, program: Program) -> list[SlaContact]:
        """The first-contact SLA timer rows for ``program``."""
        raise NotImplementedError


class InMemoryNurtureStore(NurtureStore):
    """In-memory :class:`NurtureStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseNurtureStore` behind the same seam. :meth:`seed_demo` lays down the
    deterministic demo segments/sequences/threads/SLA rows (idempotent).
    """

    def __init__(self) -> None:
        self._segments: dict[Program, list[NurtureSegment]] = {}
        self._sequences: dict[Program, list[NurtureSequence]] = {}
        self._threads: dict[Program, list[SmsThread]] = {}
        self._sla: dict[Program, list[SlaContact]] = {}
        self._seeded: set[Program] = set()

    # ---------------------------------------------------------------- segments
    def list_segments(self, program: Program) -> list[NurtureSegment]:
        return list(self._segments.get(program, []))

    def create_segment(
        self,
        program: Program,
        *,
        segment_id: UUID | None = None,
        tier: str,
        sub_bucket: str = "",
        label: str = "",
        attribute_filters: dict[str, Any] | None = None,
        size: int = 0,
        reachability_pct: float = 0.0,
        owner: str = "nurture",
        notes: str = "",
    ) -> NurtureSegment:
        seg = NurtureSegment(
            segment_id=segment_id if segment_id is not None else uuid4(),
            tier=tier,
            sub_bucket=sub_bucket,
            label=label,
            attribute_filters=dict(attribute_filters or {}),
            size=size,
            reachability_pct=reachability_pct,
            owner=owner,
            notes=notes,
        )
        self._segments.setdefault(program, []).append(seg)
        return seg

    # --------------------------------------------------------------- sequences
    def list_sequences(self, program: Program) -> list[NurtureSequence]:
        return list(self._sequences.get(program, []))

    # --------------------------------------------------------------- sms inbox
    def list_sms_threads(self, program: Program) -> list[SmsThread]:
        return list(self._threads.get(program, []))

    def get_thread(self, program: Program, thread_id: UUID) -> SmsThread | None:
        for t in self._threads.get(program, []):
            if t.thread_id == thread_id:
                return t
        return None

    def update_thread(self, program: Program, thread_id: UUID, **changes: Any) -> SmsThread:
        applied = {k: v for k, v in changes.items() if k in _THREAD_UPDATABLE and v is not None}
        threads = self._threads.setdefault(program, [])
        for i, existing in enumerate(threads):
            if existing.thread_id == thread_id:
                updated = replace(existing, **applied)
                threads[i] = updated
                return updated
        raise KeyError(f"unknown sms thread: {thread_id!r}")

    # ----------------------------------------------------------------- sla log
    def list_sla_contacts(self, program: Program) -> list[SlaContact]:
        return list(self._sla.get(program, []))

    # ------------------------------------------------------------------ seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo nurture state (INV-1; idempotent).

        Clock/random-free: ids are derived (``UUID(int=...)``) and all datetimes derive
        from :data:`_SEED_EPOCH`, so a re-seed is a no-op in shape. ~6 segments across
        T1/T2/T3, ~5 sequences across the types (one deliberate laggard), ~14 SMS threads
        across all statuses/themes, and ~30 SLA rows (a third contacted in-window, a third
        contacted late, a third uncontacted) so the six sub-views read sensibly but NOT
        maxed. Re-seeding the same program is a guarded no-op.
        """
        if program in self._seeded:
            return

        for i, (tier, sub, label, filt, size, reach, notes) in enumerate(_SEED_SEGMENTS):
            self.create_segment(
                program,
                segment_id=UUID(int=(0x4E51_0000 + i)),
                tier=tier,
                sub_bucket=sub,
                label=label,
                attribute_filters=filt,
                size=size,
                reachability_pct=reach,
                owner="nurture",
                notes=notes,
            )

        seqs = self._sequences.setdefault(program, [])
        for i, (name, seq_type, audience, steps) in enumerate(_SEED_SEQUENCES):
            step_views = [SequenceStep(s, o, c, cv) for (s, o, c, cv) in steps]
            avg_open = sum(s.open_pct for s in step_views) / len(step_views)
            avg_click = sum(s.click_pct for s in step_views) / len(step_views)
            # The laggard (low avg open/click) seeds an unhealthy flag; the API re-derives
            # the flag from params, this is just a sensible seeded default.
            health_flag = avg_open < 35.0 or avg_click < 8.0
            seqs.append(
                NurtureSequence(
                    sequence_id=UUID(int=(0x4E52_0000 + i)),
                    name=name,
                    seq_type=seq_type,
                    audience_size=audience,
                    step_count=len(step_views),
                    steps=step_views,
                    health_flag=health_flag,
                    status="active",
                )
            )

        threads = self._threads.setdefault(program, [])
        for i, (label, msg, tags, status, replied, off_h) in enumerate(_SEED_SMS):
            threads.append(
                SmsThread(
                    thread_id=UUID(int=(0x4E53_0000 + i)),
                    contact_label=label,
                    last_message=msg,
                    theme_tags=list(tags),
                    status=status,
                    replied=replied,
                    inbound_at=_SEED_EPOCH + timedelta(hours=off_h),
                )
            )

        sla = self._sla.setdefault(program, [])
        for i in range(30):
            entered = _SEED_EPOCH - timedelta(hours=i * 2)
            owner = _SEED_OWNERS[i % len(_SEED_OWNERS)]
            bucket = i % 3
            if bucket == 0:  # contacted within the 24h window
                contacted: datetime | None = entered + timedelta(hours=5)
            elif bucket == 1:  # contacted LATE (> 24h)
                contacted = entered + timedelta(hours=30)
            else:  # uncontacted
                contacted = None
            sla.append(
                SlaContact(
                    contact_id=UUID(int=(0x4E54_0000 + i)),
                    applicant_label=f"Applicant #{i:02d}",
                    entered_at=entered,
                    contacted_at=contacted,
                    owner=owner,
                )
            )

        self._seeded.add(program)


class SupabaseNurtureStore(NurtureStore):
    """Live :class:`NurtureStore` over Supabase PostgREST (service_role; 0040).

    Query-per-request (the stateless-runtime posture of the field-events/content stores):
    each call issues a fresh PostgREST request over the injected (or per-call) ``httpx``
    client. Every table is program-scoped (``program_id`` is the 0040 tenancy tag) so
    every read filters and every write stamps it. The ``service_role`` key BYPASSES RLS
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

    # ---------------------------------------------------------------- segments
    _SEGMENT_SELECT = (
        "segment_id,tier,sub_bucket,label,attribute_filters,size,reachability_pct,owner,notes"
    )

    def list_segments(self, program: Program) -> list[NurtureSegment]:
        rows = self._request(
            "GET",
            _SEGMENT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._SEGMENT_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_segment(r) for r in rows]

    def create_segment(
        self,
        program: Program,
        *,
        segment_id: UUID | None = None,
        tier: str,
        sub_bucket: str = "",
        label: str = "",
        attribute_filters: dict[str, Any] | None = None,
        size: int = 0,
        reachability_pct: float = 0.0,
        owner: str = "nurture",
        notes: str = "",
    ) -> NurtureSegment:
        payload: dict[str, Any] = {
            "tier": tier,
            "sub_bucket": sub_bucket,
            "label": label,
            "attribute_filters": dict(attribute_filters or {}),
            "size": size,
            "reachability_pct": reachability_pct,
            "owner": owner,
            "notes": notes,
            "program_id": program.value,
        }
        if segment_id is not None:
            payload["segment_id"] = str(segment_id)
        rows = self._request(
            "POST",
            _SEGMENT_TABLE,
            params={"on_conflict": "segment_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /nurture_segment returned no representation row")
        return _row_to_segment(rows[0])

    # --------------------------------------------------------------- sequences
    def list_sequences(self, program: Program) -> list[NurtureSequence]:
        rows = self._request(
            "GET",
            _SEQUENCE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": (
                    "sequence_id,name,seq_type,audience_size,step_count,steps,health_flag,status"
                ),
                "order": "created_at.asc",
            },
        )
        return [_row_to_sequence(r) for r in rows]

    # --------------------------------------------------------------- sms inbox
    _SMS_SELECT = "thread_id,contact_label,last_message,theme_tags,status,replied,inbound_at"

    def list_sms_threads(self, program: Program) -> list[SmsThread]:
        rows = self._request(
            "GET",
            _SMS_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._SMS_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_thread(r) for r in rows]

    def get_thread(self, program: Program, thread_id: UUID) -> SmsThread | None:
        rows = self._request(
            "GET",
            _SMS_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "thread_id": f"eq.{thread_id}",
                "select": self._SMS_SELECT,
            },
        )
        return _row_to_thread(rows[0]) if rows else None

    def update_thread(self, program: Program, thread_id: UUID, **changes: Any) -> SmsThread:
        payload: dict[str, Any] = {
            k: v for k, v in changes.items() if k in _THREAD_UPDATABLE and v is not None
        }
        payload["updated_at"] = "now()"
        patched = self._request(
            "PATCH",
            _SMS_TABLE,
            params={"program_id": f"eq.{program.value}", "thread_id": f"eq.{thread_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown sms thread: {thread_id!r}")
        return _row_to_thread(patched[0])

    # ----------------------------------------------------------------- sla log
    def list_sla_contacts(self, program: Program) -> list[SlaContact]:
        rows = self._request(
            "GET",
            _SLA_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "contact_id,applicant_label,entered_at,contacted_at,owner",
                "order": "entered_at.asc",
            },
        )
        return [_row_to_sla(r) for r in rows]


def _parse_dt(raw: object) -> datetime | None:
    """Parse a PostgREST timestamptz to a tz-aware datetime, or ``None`` when absent."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _row_to_segment(row: dict[str, Any]) -> NurtureSegment:
    """Map a PostgREST ``nurture_segment`` row to :class:`NurtureSegment`."""
    filt = row.get("attribute_filters")
    return NurtureSegment(
        segment_id=UUID(str(row["segment_id"])),
        tier=str(row.get("tier") or "T3"),
        sub_bucket=str(row.get("sub_bucket") or ""),
        label=str(row.get("label") or ""),
        attribute_filters=dict(filt) if isinstance(filt, dict) else {},
        size=int(row.get("size") or 0),
        reachability_pct=float(row.get("reachability_pct") or 0.0),
        owner=str(row.get("owner") or "nurture"),
        notes=str(row.get("notes") or ""),
    )


def _row_to_sequence(row: dict[str, Any]) -> NurtureSequence:
    """Map a PostgREST ``nurture_sequence`` row to :class:`NurtureSequence`."""
    raw_steps = row.get("steps")
    steps: list[SequenceStep] = []
    if isinstance(raw_steps, list):
        for s in raw_steps:
            if isinstance(s, dict):
                steps.append(
                    SequenceStep(
                        step=int(s.get("step") or 0),
                        open_pct=float(s.get("open_pct") or 0.0),
                        click_pct=float(s.get("click_pct") or 0.0),
                        conversion_pct=float(s.get("conversion_pct") or 0.0),
                    )
                )
    return NurtureSequence(
        sequence_id=UUID(str(row["sequence_id"])),
        name=str(row.get("name") or ""),
        seq_type=str(row.get("seq_type") or "nurture"),
        audience_size=int(row.get("audience_size") or 0),
        step_count=int(row.get("step_count") or len(steps)),
        steps=steps,
        health_flag=bool(row.get("health_flag")),
        status=str(row.get("status") or "active"),
    )


def _row_to_thread(row: dict[str, Any]) -> SmsThread:
    """Map a PostgREST ``sms_thread`` row to :class:`SmsThread`."""
    tags = row.get("theme_tags")
    return SmsThread(
        thread_id=UUID(str(row["thread_id"])),
        contact_label=str(row.get("contact_label") or ""),
        last_message=str(row.get("last_message") or ""),
        theme_tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        status=str(row.get("status") or "unread"),
        replied=bool(row.get("replied")),
        inbound_at=_parse_dt(row.get("inbound_at")),
    )


def _row_to_sla(row: dict[str, Any]) -> SlaContact:
    """Map a PostgREST ``sla_contact`` row to :class:`SlaContact`."""
    return SlaContact(
        contact_id=UUID(str(row["contact_id"])),
        applicant_label=str(row.get("applicant_label") or ""),
        entered_at=_parse_dt(row.get("entered_at")) or _SEED_EPOCH,
        contacted_at=_parse_dt(row.get("contacted_at")),
        owner=str(row.get("owner") or "nurture"),
    )


def build_supabase_nurture_store() -> SupabaseNurtureStore | None:
    """Construct the Supabase nurture store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.field_events_store.build_supabase_field_events_store`: reads
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
    return SupabaseNurtureStore(base_url=url, service_role_key=key)

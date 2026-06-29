"""Admissions & Voice-of-Customer store (Module 9) — the program-scoped listening-post seam.

The Admissions surface owns five pieces of program-scoped state behind the same NFR-8
store seam as the nurture/content/field-events stores (migration 0042):

- ``objection_log``  — themed, frequency-counted, trended objections (the 9b log).
- ``voice_quote``    — notable SYNTHETIC family verbatims (the 9d voice feed); one row
  may be the rotating quote-of-the-week.
- ``feedback_item``  — "marketing needs to know X" items (the 9e loop), optionally linked
  to a Decision-Queue row when actionable.
- ``admission_stat`` — one week's admission funnel counters (the 9a numbers).
- ``content_bridge`` — objection→content-brief bridge rows (the 9c tracker).

- :class:`AdmissionsStore` — the ABC every admissions route depends on.
- :class:`InMemoryAdmissionsStore` — the v1 / CI-tested local impl (pure, no I/O), with a
  deterministic :meth:`InMemoryAdmissionsStore.seed_demo` (no clock/random).
- :class:`SupabaseAdmissionsStore` — the live impl over the 0042 tables, via the SAME
  PostgREST/service_role pattern as the nurture store. Upserts pass ``on_conflict`` in the
  PostgREST URL (the bit that bit us before).

All synthetic/aggregate data only (INV-1/INV-6 — NO real PII; objection example quotes +
voice quotes are SYNTHETIC text, never real families). Purity: plain data access — imports
no ``app.ai`` / ``app.adapters`` modules, only the pure :class:`app.core.program.Program`
enum and ``httpx`` (the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the nurture store makes). The 0042 names.
_REST = "/rest/v1"
_OBJECTION_TABLE = f"{_REST}/objection_log"
_VOICE_TABLE = f"{_REST}/voice_quote"
_FEEDBACK_TABLE = f"{_REST}/feedback_item"
_STAT_TABLE = f"{_REST}/admission_stat"
_BRIDGE_TABLE = f"{_REST}/content_bridge"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 9). PII-free (INV-1) + clock/random-free: ids are
# UUID(int=...), datetimes derive from a FIXED epoch (no clock). Calibrated so the five
# sub-views render sensibly but NOT maxed. Every quote/example is SYNTHETIC text.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_SEED_DAY = _SEED_EPOCH.date()

# (theme, week_count, cumulative_count, trend, source, example_quote, persona, urgency)
# — ~7 objections across the themes (a falling objection is a good thing). The quotes are
# SYNTHETIC content (INV-1), aligned to the frontend AdmissionsModule seed shapes.
_SEED_OBJECTIONS: tuple[tuple[str, int, int, str, str, str, str, str], ...] = (
    (
        "cost",
        14,
        58,
        "up",
        "bdr_call",
        "$10k a year before the ESA even clears — we cannot float that.",
        "ESA-planned, out-of-pocket-anxious",
        "high",
    ),
    (
        "accreditation",
        11,
        44,
        "up",
        "form",
        "Is this an accredited school, or does my kid end up with no real diploma?",
        "first-time, diploma-skeptic",
        "high",
    ),
    (
        "gifted_enough",
        8,
        31,
        "stable",
        "sms",
        "He is bright but not a prodigy — is this only for the genius kids?",
        "bright-but-not-prodigy parent",
        "normal",
    ),
    (
        "scheduling",
        6,
        22,
        "down",
        "event",
        "When does the day actually start? I work and cannot do a 7am drop.",
        "working-parent, logistics-first",
        "normal",
    ),
    (
        "curriculum",
        4,
        18,
        "stable",
        "bdr_call",
        "What do they actually learn if an app teaches the academics?",
        "rigor-curious parent",
        "normal",
    ),
    (
        "social",
        3,
        14,
        "down",
        "sms",
        "I worry she will be isolated staring at a screen all day.",
        "socialization-worried parent",
        "low",
    ),
    (
        "tech_requirements",
        1,
        5,
        "stable",
        "form",
        "Do we have to buy the iPad, or is the device provided?",
        "logistics-first parent",
        "low",
    ),
)

# (quote, sentiment, theme, source, is_quote_of_week, week_offset_days) — ~8 voice
# quotes, one quote-of-the-week, mixed sentiment. SYNTHETIC text (INV-1).
_SEED_VOICE: tuple[tuple[str, str, str, str, bool, int], ...] = (
    (
        "The 2-hour academic core gave my daughter her afternoons back.",
        "positive",
        "scheduling",
        "enrolled_family",
        False,
        0,
    ),
    (
        "Loved the tour but nobody followed up for nine days. I had already half-moved on.",
        "negative",
        "scheduling",
        "tour_attendee",
        False,
        0,
    ),
    (
        "Still not clear how the guides differ from teachers. Explain that and I am sold.",
        "neutral",
        "curriculum",
        "form_inquiry",
        False,
        0,
    ),
    (
        "My son went from hating school to asking to do extra. That alone is worth it.",
        "positive",
        "curriculum",
        "enrolled_family",
        False,
        0,
    ),
    (
        "The ESA paperwork felt heavier than enrolling itself. A checklist would have saved me.",
        "negative",
        "cost",
        "esa_planned",
        False,
        0,
    ),
    (
        "I came in a skeptic about an app teaching my kid. I left realizing the app is the "
        "floor and the guides build everything on top of it.",
        "positive",
        "curriculum",
        "shadow_day_visitor",
        True,  # the rotating quote-of-the-week
        0,
    ),
    (
        "What is the real difference between mastery and grade-level? I keep hearing both.",
        "neutral",
        "curriculum",
        "form_inquiry",
        False,
        0,
    ),
    (
        "Afternoons-back framing lands hard with working parents. Use it more.",
        "positive",
        "scheduling",
        "enrolled_family",
        False,
        0,
    ),
)

# (summary, category, status, actionable, created_offset_days, actioned_offset_days|None)
# — ~6 feedback items across categories/statuses; some actioned within 7d, one after, two
# still open (one of them the URGENT one). Offsets are negative days from the epoch.
_SEED_FEEDBACK: tuple[tuple[str, str, str, bool, int, int | None], ...] = (
    (
        "Families do not connect 2-hour learning to academic rigor — reads as less school.",
        "messaging_gap",
        "actioned",
        True,
        -10,
        -6,  # 4 days -> within the 7d SLA
    ),
    (
        "Gifted-enough recurs from mid-tier learners — hero copy over-indexes on prodigies.",
        "persona_mismatch",
        "open",
        False,
        -4,
        None,
    ),
    (
        "Accreditation questions up since a competitor diploma campaign.",
        "objection_pattern",
        "actioned",
        True,
        -12,
        -2,  # 10 days -> OUTSIDE the 7d SLA
    ),
    (
        "Afternoons-back framing lands hard with working parents — under-used in ads.",
        "positive_signal",
        "closed",
        False,
        -8,
        -5,  # 3 days -> within the 7d SLA
    ),
    (
        "High-intent families stalled on ESA paperwork confusion — churn risk this week.",
        "urgent",
        "open",
        True,
        -2,
        None,
    ),
    (
        "Tour-to-followup gap reported again — leads cool before the first call.",
        "messaging_gap",
        "actioned",
        True,
        -5,
        -3,  # 2 days -> within the 7d SLA
    ),
)

# (week_offset_days, applicants, shadow_days, offers, deposits) — ~5 weeks, rising.
_SEED_STATS: tuple[tuple[int, int, int, int, int], ...] = (
    (-28, 31, 12, 9, 5),
    (-21, 38, 15, 12, 7),
    (-14, 44, 18, 15, 9),
    (-7, 49, 20, 17, 11),
    (0, 47, 22, 19, 13),
)

# (objection_theme, produced, surfaced_offset_days, published_offset_days|None,
# freq_before, freq_after|None) — ~4 bridges; 2 produced+published, 2 pending.
_SEED_BRIDGES: tuple[tuple[str, bool, int, int | None, int, int | None], ...] = (
    ("cost", True, -14, -10, 18, 14),  # published, frequency dropped
    ("accreditation", True, -12, -6, 14, 11),  # published, frequency dropped
    ("gifted_enough", False, -3, None, 8, None),  # pending
    ("scheduling", False, -2, None, 9, None),  # pending
)


@dataclass(frozen=True)
class Objection:
    """One themed, frequency-counted, trended objection (synthetic verbatim; INV-1)."""

    objection_id: UUID
    theme: str
    week_count: int
    cumulative_count: int
    trend: str
    source: str
    example_quote: str
    persona: str
    urgency: str


@dataclass(frozen=True)
class VoiceQuote:
    """One notable SYNTHETIC family verbatim (the voice feed; INV-1)."""

    quote_id: UUID
    quote: str
    sentiment: str
    theme: str
    source: str
    is_quote_of_week: bool
    week_of: date | None


@dataclass(frozen=True)
class FeedbackItem:
    """One 'marketing needs to know X' loop item (optionally Decision-linked)."""

    item_id: UUID
    summary: str
    category: str
    status: str
    actionable: bool
    owner: str
    decision_id: UUID | None
    created_at: datetime
    actioned_at: datetime | None


@dataclass(frozen=True)
class AdmissionStat:
    """One week's admission funnel counters (the 9a numbers)."""

    stat_id: UUID
    week_of: date
    applicants: int
    shadow_days: int
    offers: int
    deposits: int


@dataclass(frozen=True)
class ContentBridge:
    """One objection→content-brief bridge row (the 9c tracker)."""

    bridge_id: UUID
    objection_theme: str
    brief_entry_id: UUID | None
    produced: bool
    surfaced_at: datetime
    published_at: datetime | None
    freq_before: int
    freq_after: int | None


class AdmissionsStore(ABC):
    """Read/write seam over the Module-9 admissions state (migration 0042).

    Every admissions route depends on this interface, never a concrete store. v1 binds the
    in-memory impl (seed-driven); production swaps the Supabase-backed one with zero caller
    changes (the NFR-8 store-seam pattern). Every method is program-scoped (the 0042
    tenancy tag) so one program's admissions state never bleeds into another's.
    """

    # ------------------------------------------------------------------ objections
    @abstractmethod
    def list_objections(self, program: Program) -> list[Objection]:
        """The objection-log rows for ``program`` (insertion/created order)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_objection(
        self,
        program: Program,
        *,
        objection_id: UUID | None = None,
        theme: str,
        week_count: int = 0,
        cumulative_count: int = 0,
        trend: str = "stable",
        source: str = "other",
        example_quote: str = "",
        persona: str = "",
        urgency: str = "normal",
    ) -> Objection:
        """Insert or update one objection (keyed by ``objection_id``); return it."""
        raise NotImplementedError

    # ---------------------------------------------------------------- voice quotes
    @abstractmethod
    def list_voice_quotes(self, program: Program) -> list[VoiceQuote]:
        """The voice-quote feed for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def get_quote_of_week(self, program: Program) -> VoiceQuote | None:
        """The current quote-of-the-week (first ``is_quote_of_week`` row), or ``None``."""
        raise NotImplementedError

    # ------------------------------------------------------------------- feedback
    @abstractmethod
    def list_feedback(self, program: Program) -> list[FeedbackItem]:
        """The feedback-loop items for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def create_feedback(
        self,
        program: Program,
        *,
        item_id: UUID | None = None,
        summary: str,
        category: str,
        status: str = "open",
        actionable: bool = False,
        owner: str = "admissions",
        decision_id: UUID | None = None,
        created_at: datetime | None = None,
        actioned_at: datetime | None = None,
    ) -> FeedbackItem:
        """Create one feedback item (gen a uuid when ``item_id`` is None); return it."""
        raise NotImplementedError

    @abstractmethod
    def update_feedback(self, program: Program, item_id: UUID, **changes: Any) -> FeedbackItem:
        """Partially update one feedback item (status / actioned_at / decision_id).

        Raises ``KeyError`` on an unknown ``item_id`` (the route maps it to a 404).
        """
        raise NotImplementedError

    # -------------------------------------------------------------------- stats
    @abstractmethod
    def list_admission_stats(self, program: Program) -> list[AdmissionStat]:
        """The weekly admission-stat rows for ``program`` (week order)."""
        raise NotImplementedError

    # ------------------------------------------------------------------- bridges
    @abstractmethod
    def list_content_bridges(self, program: Program) -> list[ContentBridge]:
        """The objection→content bridge rows for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_bridge(
        self,
        program: Program,
        *,
        bridge_id: UUID | None = None,
        objection_theme: str,
        brief_entry_id: UUID | None = None,
        produced: bool = False,
        surfaced_at: datetime | None = None,
        published_at: datetime | None = None,
        freq_before: int = 0,
        freq_after: int | None = None,
    ) -> ContentBridge:
        """Insert or update one content-bridge row (keyed by ``bridge_id``); return it."""
        raise NotImplementedError

    @abstractmethod
    def mark_bridge_produced(
        self, program: Program, bridge_id: UUID, *, published_at: datetime | None = None
    ) -> ContentBridge:
        """Mark one bridge produced (and optionally published); return the updated row.

        Raises ``KeyError`` on an unknown ``bridge_id`` (the route maps it to a 404).
        """
        raise NotImplementedError


# The mutable columns an `update_feedback` partial change may target.
_FEEDBACK_UPDATABLE: frozenset[str] = frozenset({"status", "actioned_at", "decision_id"})


class InMemoryAdmissionsStore(AdmissionsStore):
    """In-memory :class:`AdmissionsStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseAdmissionsStore` behind the same seam. :meth:`seed_demo` lays down the
    deterministic demo objections/quotes/feedback/stats/bridges (idempotent).
    """

    def __init__(self) -> None:
        self._objections: dict[Program, list[Objection]] = {}
        self._voice: dict[Program, list[VoiceQuote]] = {}
        self._feedback: dict[Program, list[FeedbackItem]] = {}
        self._stats: dict[Program, list[AdmissionStat]] = {}
        self._bridges: dict[Program, list[ContentBridge]] = {}
        self._seeded: set[Program] = set()

    # ------------------------------------------------------------------ objections
    def list_objections(self, program: Program) -> list[Objection]:
        return list(self._objections.get(program, []))

    def upsert_objection(
        self,
        program: Program,
        *,
        objection_id: UUID | None = None,
        theme: str,
        week_count: int = 0,
        cumulative_count: int = 0,
        trend: str = "stable",
        source: str = "other",
        example_quote: str = "",
        persona: str = "",
        urgency: str = "normal",
    ) -> Objection:
        row = Objection(
            objection_id=objection_id if objection_id is not None else uuid4(),
            theme=theme,
            week_count=week_count,
            cumulative_count=cumulative_count,
            trend=trend,
            source=source,
            example_quote=example_quote,
            persona=persona,
            urgency=urgency,
        )
        rows = self._objections.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.objection_id == row.objection_id:
                rows[i] = row
                return row
        rows.append(row)
        return row

    # ---------------------------------------------------------------- voice quotes
    def list_voice_quotes(self, program: Program) -> list[VoiceQuote]:
        return list(self._voice.get(program, []))

    def get_quote_of_week(self, program: Program) -> VoiceQuote | None:
        for q in self._voice.get(program, []):
            if q.is_quote_of_week:
                return q
        return None

    # ------------------------------------------------------------------- feedback
    def list_feedback(self, program: Program) -> list[FeedbackItem]:
        return list(self._feedback.get(program, []))

    def create_feedback(
        self,
        program: Program,
        *,
        item_id: UUID | None = None,
        summary: str,
        category: str,
        status: str = "open",
        actionable: bool = False,
        owner: str = "admissions",
        decision_id: UUID | None = None,
        created_at: datetime | None = None,
        actioned_at: datetime | None = None,
    ) -> FeedbackItem:
        row = FeedbackItem(
            item_id=item_id if item_id is not None else uuid4(),
            summary=summary,
            category=category,
            status=status,
            actionable=actionable,
            owner=owner,
            decision_id=decision_id,
            created_at=created_at if created_at is not None else datetime.now(UTC),
            actioned_at=actioned_at,
        )
        self._feedback.setdefault(program, []).append(row)
        return row

    def update_feedback(self, program: Program, item_id: UUID, **changes: Any) -> FeedbackItem:
        applied = {k: v for k, v in changes.items() if k in _FEEDBACK_UPDATABLE and v is not None}
        items = self._feedback.setdefault(program, [])
        for i, existing in enumerate(items):
            if existing.item_id == item_id:
                updated = replace(existing, **applied)
                items[i] = updated
                return updated
        raise KeyError(f"unknown feedback item: {item_id!r}")

    # -------------------------------------------------------------------- stats
    def list_admission_stats(self, program: Program) -> list[AdmissionStat]:
        return sorted(self._stats.get(program, []), key=lambda s: s.week_of)

    # ------------------------------------------------------------------- bridges
    def list_content_bridges(self, program: Program) -> list[ContentBridge]:
        return list(self._bridges.get(program, []))

    def upsert_bridge(
        self,
        program: Program,
        *,
        bridge_id: UUID | None = None,
        objection_theme: str,
        brief_entry_id: UUID | None = None,
        produced: bool = False,
        surfaced_at: datetime | None = None,
        published_at: datetime | None = None,
        freq_before: int = 0,
        freq_after: int | None = None,
    ) -> ContentBridge:
        row = ContentBridge(
            bridge_id=bridge_id if bridge_id is not None else uuid4(),
            objection_theme=objection_theme,
            brief_entry_id=brief_entry_id,
            produced=produced,
            surfaced_at=surfaced_at if surfaced_at is not None else datetime.now(UTC),
            published_at=published_at,
            freq_before=freq_before,
            freq_after=freq_after,
        )
        rows = self._bridges.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.bridge_id == row.bridge_id:
                rows[i] = row
                return row
        rows.append(row)
        return row

    def mark_bridge_produced(
        self, program: Program, bridge_id: UUID, *, published_at: datetime | None = None
    ) -> ContentBridge:
        rows = self._bridges.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.bridge_id == bridge_id:
                updated = replace(existing, produced=True, published_at=published_at)
                rows[i] = updated
                return updated
        raise KeyError(f"unknown content bridge: {bridge_id!r}")

    # ------------------------------------------------------------------ demo seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo admissions state (INV-1; idempotent).

        Clock/random-free: ids are derived (``UUID(int=...)``) and all datetimes derive
        from :data:`_SEED_EPOCH`, so a re-seed is a no-op in shape. ~7 objections across
        the themes, ~8 voice quotes (one quote-of-the-week, mixed sentiment), ~6 feedback
        items across categories/statuses (some actioned within/after the 7d SLA, two
        open), ~5 weekly admission stats, and ~4 content bridges (two produced+published,
        two pending) so the five sub-views read sensibly but NOT maxed. Every quote/example
        is SYNTHETIC. Re-seeding the same program is a guarded no-op.
        """
        if program in self._seeded:
            return

        for i, (theme, wk, cum, trend, src, quote, persona, urg) in enumerate(_SEED_OBJECTIONS):
            self.upsert_objection(
                program,
                objection_id=UUID(int=(0xAD91_0000 + i)),
                theme=theme,
                week_count=wk,
                cumulative_count=cum,
                trend=trend,
                source=src,
                example_quote=quote,
                persona=persona,
                urgency=urg,
            )

        voice = self._voice.setdefault(program, [])
        for i, (quote, sentiment, theme, src, qow, off) in enumerate(_SEED_VOICE):
            voice.append(
                VoiceQuote(
                    quote_id=UUID(int=(0xAD92_0000 + i)),
                    quote=quote,
                    sentiment=sentiment,
                    theme=theme,
                    source=src,
                    is_quote_of_week=qow,
                    week_of=(_SEED_EPOCH + timedelta(days=off)).date(),
                )
            )

        for i, (summary, cat, status, actionable, c_off, a_off) in enumerate(_SEED_FEEDBACK):
            self.create_feedback(
                program,
                item_id=UUID(int=(0xAD93_0000 + i)),
                summary=summary,
                category=cat,
                status=status,
                actionable=actionable,
                owner="admissions",
                created_at=_SEED_EPOCH + timedelta(days=c_off),
                actioned_at=(_SEED_EPOCH + timedelta(days=a_off)) if a_off is not None else None,
            )

        stats = self._stats.setdefault(program, [])
        for i, (off, app, shadow, offers, deposits) in enumerate(_SEED_STATS):
            stats.append(
                AdmissionStat(
                    stat_id=UUID(int=(0xAD94_0000 + i)),
                    week_of=(_SEED_EPOCH + timedelta(days=off)).date(),
                    applicants=app,
                    shadow_days=shadow,
                    offers=offers,
                    deposits=deposits,
                )
            )

        for i, (theme, produced, s_off, p_off, before, after) in enumerate(_SEED_BRIDGES):
            self.upsert_bridge(
                program,
                bridge_id=UUID(int=(0xAD95_0000 + i)),
                objection_theme=theme,
                brief_entry_id=UUID(int=(0xAD96_0000 + i)),
                produced=produced,
                surfaced_at=_SEED_EPOCH + timedelta(days=s_off),
                published_at=(_SEED_EPOCH + timedelta(days=p_off)) if p_off is not None else None,
                freq_before=before,
                freq_after=after,
            )

        self._seeded.add(program)


class SupabaseAdmissionsStore(AdmissionsStore):
    """Live :class:`AdmissionsStore` over Supabase PostgREST (service_role; 0042).

    Query-per-request (the stateless-runtime posture of the nurture/content stores): each
    call issues a fresh PostgREST request over the injected (or per-call) ``httpx`` client.
    Every table is program-scoped (``program_id`` is the 0042 tenancy tag) so every read
    filters and every write stamps it. Upserts pass ``on_conflict`` in the PostgREST URL.
    The ``service_role`` key BYPASSES RLS (server-only — INV-5 / D-RLS-4) and never leaves
    the backend.
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

    # ------------------------------------------------------------------ objections
    _OBJECTION_SELECT = (
        "objection_id,theme,week_count,cumulative_count,trend,source,example_quote,persona,urgency"
    )

    def list_objections(self, program: Program) -> list[Objection]:
        rows = self._request(
            "GET",
            _OBJECTION_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._OBJECTION_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_objection(r) for r in rows]

    def upsert_objection(
        self,
        program: Program,
        *,
        objection_id: UUID | None = None,
        theme: str,
        week_count: int = 0,
        cumulative_count: int = 0,
        trend: str = "stable",
        source: str = "other",
        example_quote: str = "",
        persona: str = "",
        urgency: str = "normal",
    ) -> Objection:
        payload: dict[str, Any] = {
            "theme": theme,
            "week_count": week_count,
            "cumulative_count": cumulative_count,
            "trend": trend,
            "source": source,
            "example_quote": example_quote,
            "persona": persona,
            "urgency": urgency,
            "program_id": program.value,
        }
        if objection_id is not None:
            payload["objection_id"] = str(objection_id)
        rows = self._request(
            "POST",
            _OBJECTION_TABLE,
            params={"on_conflict": "objection_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /objection_log returned no row")
        return _row_to_objection(rows[0])

    # ---------------------------------------------------------------- voice quotes
    _VOICE_SELECT = "quote_id,quote,sentiment,theme,source,is_quote_of_week,week_of"

    def list_voice_quotes(self, program: Program) -> list[VoiceQuote]:
        rows = self._request(
            "GET",
            _VOICE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._VOICE_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_voice(r) for r in rows]

    def get_quote_of_week(self, program: Program) -> VoiceQuote | None:
        rows = self._request(
            "GET",
            _VOICE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "is_quote_of_week": "eq.true",
                "select": self._VOICE_SELECT,
                "order": "week_of.desc",
                "limit": "1",
            },
        )
        return _row_to_voice(rows[0]) if rows else None

    # ------------------------------------------------------------------- feedback
    _FEEDBACK_SELECT = (
        "item_id,summary,category,status,actionable,owner,decision_id,created_at,actioned_at"
    )

    def list_feedback(self, program: Program) -> list[FeedbackItem]:
        rows = self._request(
            "GET",
            _FEEDBACK_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._FEEDBACK_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_feedback(r) for r in rows]

    def create_feedback(
        self,
        program: Program,
        *,
        item_id: UUID | None = None,
        summary: str,
        category: str,
        status: str = "open",
        actionable: bool = False,
        owner: str = "admissions",
        decision_id: UUID | None = None,
        created_at: datetime | None = None,
        actioned_at: datetime | None = None,
    ) -> FeedbackItem:
        payload: dict[str, Any] = {
            "summary": summary,
            "category": category,
            "status": status,
            "actionable": actionable,
            "owner": owner,
            "decision_id": str(decision_id) if decision_id is not None else None,
            "program_id": program.value,
        }
        if item_id is not None:
            payload["item_id"] = str(item_id)
        if created_at is not None:
            payload["created_at"] = created_at.isoformat()
        if actioned_at is not None:
            payload["actioned_at"] = actioned_at.isoformat()
        rows = self._request(
            "POST",
            _FEEDBACK_TABLE,
            params={"on_conflict": "item_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /feedback_item returned no row")
        return _row_to_feedback(rows[0])

    def update_feedback(self, program: Program, item_id: UUID, **changes: Any) -> FeedbackItem:
        payload: dict[str, Any] = {}
        for k, v in changes.items():
            if k not in _FEEDBACK_UPDATABLE or v is None:
                continue
            payload[k] = (
                str(v) if isinstance(v, UUID) else (v.isoformat() if isinstance(v, datetime) else v)
            )
        patched = self._request(
            "PATCH",
            _FEEDBACK_TABLE,
            params={"program_id": f"eq.{program.value}", "item_id": f"eq.{item_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown feedback item: {item_id!r}")
        return _row_to_feedback(patched[0])

    # -------------------------------------------------------------------- stats
    def list_admission_stats(self, program: Program) -> list[AdmissionStat]:
        rows = self._request(
            "GET",
            _STAT_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "stat_id,week_of,applicants,shadow_days,offers,deposits",
                "order": "week_of.asc",
            },
        )
        return [_row_to_stat(r) for r in rows]

    # ------------------------------------------------------------------- bridges
    _BRIDGE_SELECT = (
        "bridge_id,objection_theme,brief_entry_id,produced,surfaced_at,"
        "published_at,freq_before,freq_after"
    )

    def list_content_bridges(self, program: Program) -> list[ContentBridge]:
        rows = self._request(
            "GET",
            _BRIDGE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._BRIDGE_SELECT,
                "order": "surfaced_at.asc",
            },
        )
        return [_row_to_bridge(r) for r in rows]

    def upsert_bridge(
        self,
        program: Program,
        *,
        bridge_id: UUID | None = None,
        objection_theme: str,
        brief_entry_id: UUID | None = None,
        produced: bool = False,
        surfaced_at: datetime | None = None,
        published_at: datetime | None = None,
        freq_before: int = 0,
        freq_after: int | None = None,
    ) -> ContentBridge:
        payload: dict[str, Any] = {
            "objection_theme": objection_theme,
            "brief_entry_id": str(brief_entry_id) if brief_entry_id is not None else None,
            "produced": produced,
            "freq_before": freq_before,
            "freq_after": freq_after,
            "program_id": program.value,
        }
        if bridge_id is not None:
            payload["bridge_id"] = str(bridge_id)
        if surfaced_at is not None:
            payload["surfaced_at"] = surfaced_at.isoformat()
        if published_at is not None:
            payload["published_at"] = published_at.isoformat()
        rows = self._request(
            "POST",
            _BRIDGE_TABLE,
            params={"on_conflict": "bridge_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /content_bridge returned no row")
        return _row_to_bridge(rows[0])

    def mark_bridge_produced(
        self, program: Program, bridge_id: UUID, *, published_at: datetime | None = None
    ) -> ContentBridge:
        payload: dict[str, Any] = {"produced": True}
        if published_at is not None:
            payload["published_at"] = published_at.isoformat()
        patched = self._request(
            "PATCH",
            _BRIDGE_TABLE,
            params={"program_id": f"eq.{program.value}", "bridge_id": f"eq.{bridge_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown content bridge: {bridge_id!r}")
        return _row_to_bridge(patched[0])


def _parse_dt(raw: object) -> datetime | None:
    """Parse a PostgREST timestamptz to a tz-aware datetime, or ``None`` when absent."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST date to a :class:`datetime.date`, or ``None`` when absent."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _opt_uuid(raw: object) -> UUID | None:
    """Parse an optional uuid column, or ``None`` when absent/blank."""
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _row_to_objection(row: dict[str, Any]) -> Objection:
    """Map a PostgREST ``objection_log`` row to :class:`Objection`."""
    return Objection(
        objection_id=UUID(str(row["objection_id"])),
        theme=str(row.get("theme") or "other"),
        week_count=int(row.get("week_count") or 0),
        cumulative_count=int(row.get("cumulative_count") or 0),
        trend=str(row.get("trend") or "stable"),
        source=str(row.get("source") or "other"),
        example_quote=str(row.get("example_quote") or ""),
        persona=str(row.get("persona") or ""),
        urgency=str(row.get("urgency") or "normal"),
    )


def _row_to_voice(row: dict[str, Any]) -> VoiceQuote:
    """Map a PostgREST ``voice_quote`` row to :class:`VoiceQuote`."""
    return VoiceQuote(
        quote_id=UUID(str(row["quote_id"])),
        quote=str(row.get("quote") or ""),
        sentiment=str(row.get("sentiment") or "neutral"),
        theme=str(row.get("theme") or ""),
        source=str(row.get("source") or ""),
        is_quote_of_week=bool(row.get("is_quote_of_week")),
        week_of=_parse_date(row.get("week_of")),
    )


def _row_to_feedback(row: dict[str, Any]) -> FeedbackItem:
    """Map a PostgREST ``feedback_item`` row to :class:`FeedbackItem`."""
    return FeedbackItem(
        item_id=UUID(str(row["item_id"])),
        summary=str(row.get("summary") or ""),
        category=str(row.get("category") or "messaging_gap"),
        status=str(row.get("status") or "open"),
        actionable=bool(row.get("actionable")),
        owner=str(row.get("owner") or "admissions"),
        decision_id=_opt_uuid(row.get("decision_id")),
        created_at=_parse_dt(row.get("created_at")) or _SEED_EPOCH,
        actioned_at=_parse_dt(row.get("actioned_at")),
    )


def _row_to_stat(row: dict[str, Any]) -> AdmissionStat:
    """Map a PostgREST ``admission_stat`` row to :class:`AdmissionStat`."""
    return AdmissionStat(
        stat_id=UUID(str(row["stat_id"])),
        week_of=_parse_date(row.get("week_of")) or _SEED_DAY,
        applicants=int(row.get("applicants") or 0),
        shadow_days=int(row.get("shadow_days") or 0),
        offers=int(row.get("offers") or 0),
        deposits=int(row.get("deposits") or 0),
    )


def _row_to_bridge(row: dict[str, Any]) -> ContentBridge:
    """Map a PostgREST ``content_bridge`` row to :class:`ContentBridge`."""
    raw_after = row.get("freq_after")
    return ContentBridge(
        bridge_id=UUID(str(row["bridge_id"])),
        objection_theme=str(row.get("objection_theme") or ""),
        brief_entry_id=_opt_uuid(row.get("brief_entry_id")),
        produced=bool(row.get("produced")),
        surfaced_at=_parse_dt(row.get("surfaced_at")) or _SEED_EPOCH,
        published_at=_parse_dt(row.get("published_at")),
        freq_before=int(row.get("freq_before") or 0),
        freq_after=int(raw_after) if raw_after is not None else None,
    )


def build_supabase_admissions_store() -> SupabaseAdmissionsStore | None:
    """Construct the Supabase admissions store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.nurture_store.build_supabase_nurture_store`: reads
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
    return SupabaseAdmissionsStore(base_url=url, service_role_key=key)

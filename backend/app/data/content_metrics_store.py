"""Content-metrics store (Module 3) — the editorial-calendar + channel/piece metrics seam.

The Content analytics surface owns three pieces of program-scoped state behind the same
NFR-8 store seam as the budget/grassroots stores: the editorial CALENDAR entries (the
month grid + drag-reschedule + conflict detection), the per-channel METRICS (the
channel breakdown; ``source_kind`` drives the honesty label), and the per-piece
PERFORMANCE rows (top/bottom + content-to-conversion; ``utm_attributed`` keeps the
broken-UTM reality visible). All synthetic/aggregate data only (INV-1/INV-6 — NO real
PII).

- :class:`ContentMetricsStore` — the ABC every content-analytics route depends on.
- :class:`InMemoryContentMetricsStore` — the v1 / CI-tested local impl (pure, no I/O),
  with a deterministic :meth:`InMemoryContentMetricsStore.seed_demo` (no clock/random).
- :class:`SupabaseContentMetricsStore` — the live impl over the 0036
  ``content_calendar_entry`` / ``content_channel_metric`` / ``content_piece_perf``
  tables, via the SAME PostgREST/service_role pattern as the grassroots store. Upserts
  pass ``on_conflict`` in the PostgREST URL (the bit that bit us before).

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.program.Program` enum + :class:`app.core.params.Params` (the seed
reads the channel labels + the 42% X conversion rate from there — INV-11) and ``httpx``
(the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

if TYPE_CHECKING:
    from app.core.params import Params

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the grassroots store makes). The 0036 names.
_REST = "/rest/v1"
_CALENDAR_TABLE = f"{_REST}/content_calendar_entry"
_CHANNEL_METRIC_TABLE = f"{_REST}/content_channel_metric"
_PIECE_PERF_TABLE = f"{_REST}/content_piece_perf"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 3). PII-free (INV-1) + clock/random-free: dates
# derive from the fixed synthetic demo "now" (2026-06-15, the same _SEED_EPOCH the
# budget/grassroots seeds anchor to) so the calendar + metrics render coherently. The
# channel LABELS and the 42% X conversion rate are READ FROM PARAMS at seed time
# (INV-11) — the seed only owns the per-channel reach/clicks shape.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = date(2026, 6, 15)

# The X/Twitter channel label (the standout). The label's canonical home is
# params.content.channels; this is the token the seed matches to apply the 42% rate.
_X_CHANNEL = "x"

# Per-channel seed shape: channel label → (reach, clicks, conversions, source_kind).
# X's conversions are DERIVED from params.content.x_conversion_rate at seed time (the
# value here is a placeholder the seed overwrites), so the surfaced ~42% is computed,
# not hardcoded. Facebook is the deliberate laggard (lowest conversion rate);
# source_kind labels each honestly (no channel has a live adapter in this phase, so
# every label is a stood_in/manual stand-in — INV-9).
_SEED_CHANNEL_SHAPE: dict[str, tuple[int, int, int, str]] = {
    "substack": (8200, 540, 70, "manual"),
    "x": (15400, 500, 0, "stood_in"),  # conversions overwritten from the param rate
    "instagram": (9100, 360, 40, "stood_in"),
    "facebook": (6300, 280, 8, "stood_in"),  # the laggard
    "podcast": (3400, 120, 18, "manual"),
    "email": (5200, 410, 64, "manual"),
    "youtube": (7800, 300, 33, "stood_in"),
}
# A default shape for any params channel not in the table above (robustness; keeps the
# seed honest for a channel added to params without a seed shape).
_SEED_CHANNEL_DEFAULT: tuple[int, int, int, str] = (1000, 100, 10, "stood_in")

# (title, channel, date_offset_days, status) — a month of editorial slots relative to
# _SEED_EPOCH, with TWO deliberate same-day CONFLICT days (offset 0 and 9), each
# carrying 4 entries (>= the params conflict_threshold of 4). The rest are on distinct
# days. Titles are synthetic content themes (INV-1 — no PII).
_SEED_CALENDAR: tuple[tuple[str, str, int, str], ...] = (
    # Conflict day A (offset 0): 4 entries.
    ("The mastery model, explained", "substack", 0, "scheduled"),
    ("Why two hours a day works", "x", 0, "scheduled"),
    ("Parent Q&A: socialization", "instagram", 0, "planned"),
    ("TEFA funding walkthrough", "email", 0, "planned"),
    # Conflict day B (offset 9): 4 entries.
    ("Accreditation, in plain language", "substack", 9, "planned"),
    ("A day in a GT cohort", "youtube", 9, "planned"),
    ("Affordability myth-busting", "facebook", 9, "planned"),
    ("Mastery vs. grade-level", "x", 9, "scheduled"),
    # The rest — distinct days across the month.
    ("Founder thread: the model", "x", -4, "published"),
    ("ESA vs. voucher: what parents ask", "substack", -2, "published"),
    ("Podcast: gifted education today", "podcast", 2, "scheduled"),
    ("Reel: the 2-hour learning day", "instagram", 4, "planned"),
    ("Newsletter: enrollment is open", "email", 6, "scheduled"),
    ("YouTube: advisor walkthrough", "youtube", 11, "planned"),
    ("Facebook: campus visit recap", "facebook", 13, "planned"),
    ("Substack: the case for acceleration", "substack", 15, "planned"),
    ("Podcast: parent testimonial ep", "podcast", 17, "planned"),
    ("Thread: accreditation FAQ", "x", 18, "planned"),
)

# (piece_title, channel, reach, clicks, conversions, utm_attributed) — enough pieces for
# a sensible top/bottom ranking. THREE are utm_attributed=True (the honestly
# attributable conversions); the rest are False (the broken-UTM reality). The X founder
# piece tops conversions; the Facebook recap is the bottom.
_SEED_PIECE_PERF: tuple[tuple[str, str, int, int, int, bool], ...] = (
    ("Why two hours a day works", "x", 15400, 500, 210, True),
    ("TEFA funding walkthrough", "email", 5200, 410, 64, True),
    ("Parent Q&A: socialization", "instagram", 9100, 360, 40, True),
    ("The mastery model, explained", "substack", 8200, 540, 70, False),
    ("Founder thread: the model", "x", 12000, 420, 95, False),
    ("Podcast: gifted education today", "podcast", 3400, 120, 18, False),
    ("Reel: the 2-hour learning day", "instagram", 6000, 200, 12, False),
    ("Facebook: campus visit recap", "facebook", 6300, 280, 8, False),
)


@dataclass(frozen=True)
class CalendarEntry:
    """One editorial-calendar slot (synthetic content; INV-1).

    Attributes:
        entry_id: The row PK.
        title: A synthetic editorial title (content, never PII).
        channel: The publishing channel label (a ``content.channels`` token).
        scheduled_date: The day the piece is slotted on.
        status: The slot lifecycle (planned/scheduled/published/draft).
        piece_ref: A nullable link to a kanban card / library asset (a routing ref).
        owner: The owning workstream/operator routing token (not PII).
    """

    entry_id: UUID
    title: str
    channel: str
    scheduled_date: date
    status: str
    piece_ref: str | None
    owner: str


@dataclass(frozen=True)
class ChannelMetric:
    """One channel's period metrics (the channel-breakdown input).

    Attributes:
        metric_id: The row PK.
        channel: The channel label.
        period_start: The metric period's first day.
        reach: Reach (the subscriber/listen/impression stand-in).
        clicks: Clicks for the period.
        conversions: Conversions for the period.
        source_kind: The provenance/honesty label (``stood_in`` / ``manual`` / …).
    """

    metric_id: UUID
    channel: str
    period_start: date
    reach: int
    clicks: int
    conversions: int
    source_kind: str


@dataclass(frozen=True)
class PiecePerf:
    """One piece's performance row (top/bottom + content-to-conversion input).

    Attributes:
        perf_id: The row PK.
        piece_title: A synthetic piece title (content, never PII).
        channel: The channel the piece ran on.
        reach: Reach for the piece.
        clicks: Clicks for the piece.
        conversions: Conversions credited to the piece.
        utm_attributed: Whether the conversions are UTM-attributable (honesty flag).
    """

    perf_id: UUID
    piece_title: str
    channel: str
    reach: int
    clicks: int
    conversions: int
    utm_attributed: bool


class ContentMetricsStore(ABC):
    """Read/write seam over the Module-3 Content metrics (migration 0036).

    Every content-analytics route depends on this interface, never a concrete store. v1
    binds the in-memory impl (seed-driven); production swaps the Supabase-backed one with
    zero caller changes (the NFR-8 store-seam pattern). Every method is program-scoped
    (the 0036 tenancy tag) so one program's calendar/metrics never bleed into another's.
    """

    # ---------------------------------------------------------------------- calendar
    @abstractmethod
    def list_calendar(self, program: Program) -> list[CalendarEntry]:
        """The editorial-calendar entries for ``program`` (scheduled-date order)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_calendar_entry(
        self,
        program: Program,
        *,
        entry_id: UUID | None = None,
        title: str,
        channel: str,
        scheduled_date: date,
        status: str = "planned",
        piece_ref: str | None = None,
        owner: str = "content",
    ) -> CalendarEntry:
        """Insert or update one calendar entry (keyed by ``entry_id``); return it."""
        raise NotImplementedError

    @abstractmethod
    def reschedule_entry(self, program: Program, entry_id: UUID, new_date: date) -> CalendarEntry:
        """Move one calendar entry to ``new_date``; return the updated row.

        Raises ``KeyError`` on an unknown entry (the route maps it to a 404).
        """
        raise NotImplementedError

    # -------------------------------------------------------------- channel metrics
    @abstractmethod
    def list_channel_metrics(self, program: Program) -> list[ChannelMetric]:
        """The per-channel metrics for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_channel_metric(
        self,
        program: Program,
        *,
        metric_id: UUID | None = None,
        channel: str,
        period_start: date,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        source_kind: str = "stood_in",
    ) -> ChannelMetric:
        """Insert or update one channel metric (keyed by program+channel+period); return it."""
        raise NotImplementedError

    # ------------------------------------------------------------------ piece perf
    @abstractmethod
    def list_piece_perf(self, program: Program) -> list[PiecePerf]:
        """The per-piece performance rows for ``program``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_piece_perf(
        self,
        program: Program,
        *,
        perf_id: UUID | None = None,
        piece_title: str,
        channel: str,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        utm_attributed: bool = False,
    ) -> PiecePerf:
        """Insert or update one piece-perf row (keyed by program+title+channel); return it."""
        raise NotImplementedError


class InMemoryContentMetricsStore(ContentMetricsStore):
    """In-memory :class:`ContentMetricsStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseContentMetricsStore` behind the same seam. :meth:`seed_demo` lays
    down the deterministic demo calendar/metrics/pieces (idempotent). The seed reads the
    channel labels + the 42% X conversion rate from ``params`` (INV-11), so a clean store
    for tests is built with no params (no seed needed).
    """

    def __init__(self, *, params: Params | None = None) -> None:
        self._params = params
        self._calendar: dict[Program, list[CalendarEntry]] = {}
        self._channel_metrics: dict[Program, list[ChannelMetric]] = {}
        self._piece_perf: dict[Program, list[PiecePerf]] = {}
        self._seeded: set[Program] = set()

    # ---------------------------------------------------------------------- calendar
    def list_calendar(self, program: Program) -> list[CalendarEntry]:
        return sorted(self._calendar.get(program, []), key=lambda e: e.scheduled_date)

    def upsert_calendar_entry(
        self,
        program: Program,
        *,
        entry_id: UUID | None = None,
        title: str,
        channel: str,
        scheduled_date: date,
        status: str = "planned",
        piece_ref: str | None = None,
        owner: str = "content",
    ) -> CalendarEntry:
        row = CalendarEntry(
            entry_id=entry_id if entry_id is not None else uuid4(),
            title=title,
            channel=channel,
            scheduled_date=scheduled_date,
            status=status,
            piece_ref=piece_ref,
            owner=owner,
        )
        entries = self._calendar.setdefault(program, [])
        for i, existing in enumerate(entries):
            if existing.entry_id == row.entry_id:
                entries[i] = row
                return row
        entries.append(row)
        return row

    def reschedule_entry(self, program: Program, entry_id: UUID, new_date: date) -> CalendarEntry:
        entries = self._calendar.setdefault(program, [])
        for i, existing in enumerate(entries):
            if existing.entry_id == entry_id:
                updated = replace(existing, scheduled_date=new_date)
                entries[i] = updated
                return updated
        raise KeyError(f"unknown calendar entry: {entry_id!r}")

    # -------------------------------------------------------------- channel metrics
    def list_channel_metrics(self, program: Program) -> list[ChannelMetric]:
        return list(self._channel_metrics.get(program, []))

    def upsert_channel_metric(
        self,
        program: Program,
        *,
        metric_id: UUID | None = None,
        channel: str,
        period_start: date,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        source_kind: str = "stood_in",
    ) -> ChannelMetric:
        row = ChannelMetric(
            metric_id=metric_id if metric_id is not None else uuid4(),
            channel=channel,
            period_start=period_start,
            reach=reach,
            clicks=clicks,
            conversions=conversions,
            source_kind=source_kind,
        )
        metrics = self._channel_metrics.setdefault(program, [])
        # The natural key is (channel, period_start) — match it OR the explicit id.
        for i, existing in enumerate(metrics):
            same_id = metric_id is not None and existing.metric_id == metric_id
            same_key = existing.channel == channel and existing.period_start == period_start
            if same_id or same_key:
                row = replace(row, metric_id=existing.metric_id)
                metrics[i] = row
                return row
        metrics.append(row)
        return row

    # ------------------------------------------------------------------ piece perf
    def list_piece_perf(self, program: Program) -> list[PiecePerf]:
        return list(self._piece_perf.get(program, []))

    def upsert_piece_perf(
        self,
        program: Program,
        *,
        perf_id: UUID | None = None,
        piece_title: str,
        channel: str,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        utm_attributed: bool = False,
    ) -> PiecePerf:
        row = PiecePerf(
            perf_id=perf_id if perf_id is not None else uuid4(),
            piece_title=piece_title,
            channel=channel,
            reach=reach,
            clicks=clicks,
            conversions=conversions,
            utm_attributed=utm_attributed,
        )
        rows = self._piece_perf.setdefault(program, [])
        for i, existing in enumerate(rows):
            same_id = perf_id is not None and existing.perf_id == perf_id
            same_key = existing.piece_title == piece_title and existing.channel == channel
            if same_id or same_key:
                row = replace(row, perf_id=existing.perf_id)
                rows[i] = row
                return row
        rows.append(row)
        return row

    # ------------------------------------------------------------------ demo seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo calendar/metrics/pieces (INV-1; idempotent).

        Clock/random-free: all dates derive from :data:`_SEED_EPOCH`; ids are derived
        deterministically (``UUID(int=...)``) so a re-seed is a no-op in shape. The
        channel labels + the X conversion rate are READ FROM PARAMS (INV-11): the seed
        derives X's conversions from ``params.content.x_conversion_rate`` so the surfaced
        ~42% is a real computed rate, never a hardcoded headline. Re-seeding the same
        program is a guarded no-op.
        """
        if program in self._seeded:
            return
        if self._params is None:
            raise RuntimeError(
                "InMemoryContentMetricsStore.seed_demo requires params "
                "(content.channels + content.x_conversion_rate)"
            )

        channels = self._params.content.channels
        x_rate = self._params.content.x_conversion_rate

        # Calendar entries.
        for i, (title, channel, offset, status) in enumerate(_SEED_CALENDAR):
            self.upsert_calendar_entry(
                program,
                entry_id=UUID(int=(0xC047_0000 + i)),  # deterministic, demo-only
                title=title,
                channel=channel,
                scheduled_date=_SEED_EPOCH + timedelta(days=offset),
                status=status,
                piece_ref=None,
                owner="content",
            )

        # Channel metrics — one row per params channel; X's conversions derive from the
        # param rate (the surfaced 42% is computed over reach/clicks, not hardcoded).
        for i, channel in enumerate(channels):
            reach, clicks, conversions, source_kind = _SEED_CHANNEL_SHAPE.get(
                channel, _SEED_CHANNEL_DEFAULT
            )
            if channel == _X_CHANNEL:
                conversions = round(clicks * x_rate)
            self.upsert_channel_metric(
                program,
                metric_id=UUID(int=(0xC047_1000 + i)),
                channel=channel,
                period_start=_SEED_EPOCH,
                reach=reach,
                clicks=clicks,
                conversions=conversions,
                source_kind=source_kind,
            )

        # Per-piece performance rows.
        for i, (title, channel, reach, clicks, conversions, utm) in enumerate(_SEED_PIECE_PERF):
            self.upsert_piece_perf(
                program,
                perf_id=UUID(int=(0xC047_2000 + i)),
                piece_title=title,
                channel=channel,
                reach=reach,
                clicks=clicks,
                conversions=conversions,
                utm_attributed=utm,
            )

        self._seeded.add(program)


class SupabaseContentMetricsStore(ContentMetricsStore):
    """Live :class:`ContentMetricsStore` over Supabase PostgREST (service_role; 0036).

    Query-per-request (the stateless-runtime posture of the grassroots/budget stores):
    each call issues a fresh PostgREST request over the injected (or per-call) ``httpx``
    client. Every table is program-scoped (``program_id`` is the 0036 tenancy tag) so
    every read filters and every write stamps it. Upserts pass ``on_conflict`` in the
    PostgREST URL (the bit that bit us before) — the PK for the calendar, the natural
    keys for the metric/piece tables. The ``service_role`` key BYPASSES RLS (server-only
    — INV-5 / D-RLS-4) and never leaves the backend.
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

    # ---------------------------------------------------------------------- calendar
    def list_calendar(self, program: Program) -> list[CalendarEntry]:
        rows = self._request(
            "GET",
            _CALENDAR_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "entry_id,title,channel,scheduled_date,status,piece_ref,owner",
                "order": "scheduled_date.asc",
            },
        )
        return [_row_to_calendar(r) for r in rows]

    def upsert_calendar_entry(
        self,
        program: Program,
        *,
        entry_id: UUID | None = None,
        title: str,
        channel: str,
        scheduled_date: date,
        status: str = "planned",
        piece_ref: str | None = None,
        owner: str = "content",
    ) -> CalendarEntry:
        payload: dict[str, Any] = {
            "title": title,
            "channel": channel,
            "scheduled_date": scheduled_date.isoformat(),
            "status": status,
            "piece_ref": piece_ref,
            "owner": owner,
            "program_id": program.value,
        }
        if entry_id is not None:
            payload["entry_id"] = str(entry_id)
        rows = self._request(
            "POST",
            _CALENDAR_TABLE,
            params={"on_conflict": "entry_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /content_calendar_entry returned no row")
        return _row_to_calendar(rows[0])

    def reschedule_entry(self, program: Program, entry_id: UUID, new_date: date) -> CalendarEntry:
        patched = self._request(
            "PATCH",
            _CALENDAR_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "entry_id": f"eq.{entry_id}",
            },
            payload={"scheduled_date": new_date.isoformat(), "updated_at": "now()"},
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown calendar entry: {entry_id!r}")
        return _row_to_calendar(patched[0])

    # -------------------------------------------------------------- channel metrics
    def list_channel_metrics(self, program: Program) -> list[ChannelMetric]:
        rows = self._request(
            "GET",
            _CHANNEL_METRIC_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "metric_id,channel,period_start,reach,clicks,conversions,source_kind",
                "order": "created_at.asc",
            },
        )
        return [_row_to_channel_metric(r) for r in rows]

    def upsert_channel_metric(
        self,
        program: Program,
        *,
        metric_id: UUID | None = None,
        channel: str,
        period_start: date,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        source_kind: str = "stood_in",
    ) -> ChannelMetric:
        payload: dict[str, Any] = {
            "channel": channel,
            "period_start": period_start.isoformat(),
            "reach": reach,
            "clicks": clicks,
            "conversions": conversions,
            "source_kind": source_kind,
            "program_id": program.value,
        }
        if metric_id is not None:
            payload["metric_id"] = str(metric_id)
        rows = self._request(
            "POST",
            _CHANNEL_METRIC_TABLE,
            params={"on_conflict": "program_id,channel,period_start"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /content_channel_metric returned no row")
        return _row_to_channel_metric(rows[0])

    # ------------------------------------------------------------------ piece perf
    def list_piece_perf(self, program: Program) -> list[PiecePerf]:
        rows = self._request(
            "GET",
            _PIECE_PERF_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": "perf_id,piece_title,channel,reach,clicks,conversions,utm_attributed",
                "order": "created_at.asc",
            },
        )
        return [_row_to_piece_perf(r) for r in rows]

    def upsert_piece_perf(
        self,
        program: Program,
        *,
        perf_id: UUID | None = None,
        piece_title: str,
        channel: str,
        reach: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        utm_attributed: bool = False,
    ) -> PiecePerf:
        payload: dict[str, Any] = {
            "piece_title": piece_title,
            "channel": channel,
            "reach": reach,
            "clicks": clicks,
            "conversions": conversions,
            "utm_attributed": utm_attributed,
            "program_id": program.value,
        }
        if perf_id is not None:
            payload["perf_id"] = str(perf_id)
        rows = self._request(
            "POST",
            _PIECE_PERF_TABLE,
            params={"on_conflict": "program_id,piece_title,channel"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /content_piece_perf returned no row")
        return _row_to_piece_perf(rows[0])


def _parse_date(raw: object) -> date | None:
    """Parse a PostgREST ``date`` to a :class:`datetime.date`, or ``None`` when absent."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _row_to_calendar(row: dict[str, Any]) -> CalendarEntry:
    """Map a PostgREST ``content_calendar_entry`` row to :class:`CalendarEntry`."""
    return CalendarEntry(
        entry_id=UUID(str(row["entry_id"])),
        title=str(row["title"]),
        channel=str(row["channel"]),
        scheduled_date=_parse_date(row["scheduled_date"]) or date.min,
        status=str(row.get("status") or "planned"),
        piece_ref=(str(row["piece_ref"]) if row.get("piece_ref") else None),
        owner=str(row.get("owner") or "content"),
    )


def _row_to_channel_metric(row: dict[str, Any]) -> ChannelMetric:
    """Map a PostgREST ``content_channel_metric`` row to :class:`ChannelMetric`."""
    return ChannelMetric(
        metric_id=UUID(str(row["metric_id"])),
        channel=str(row["channel"]),
        period_start=_parse_date(row["period_start"]) or date.min,
        reach=int(row.get("reach") or 0),
        clicks=int(row.get("clicks") or 0),
        conversions=int(row.get("conversions") or 0),
        source_kind=str(row.get("source_kind") or "stood_in"),
    )


def _row_to_piece_perf(row: dict[str, Any]) -> PiecePerf:
    """Map a PostgREST ``content_piece_perf`` row to :class:`PiecePerf`."""
    return PiecePerf(
        perf_id=UUID(str(row["perf_id"])),
        piece_title=str(row["piece_title"]),
        channel=str(row["channel"]),
        reach=int(row.get("reach") or 0),
        clicks=int(row.get("clicks") or 0),
        conversions=int(row.get("conversions") or 0),
        utm_attributed=bool(row.get("utm_attributed")),
    )


def build_supabase_content_metrics_store() -> SupabaseContentMetricsStore | None:
    """Construct the Supabase content-metrics store from the env, or ``None`` when unbound.

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
    return SupabaseContentMetricsStore(base_url=url, service_role_key=key)

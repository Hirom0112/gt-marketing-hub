"""Website & Digital-Analytics store (Module 13) — the program-scoped leadership-input seam.

The website-analytics METRICS are read off the GA4 boundary (``app.adapters.analytics``);
they are NOT persisted here. What the Hub OWNS and writes — and what this store persists
behind the same NFR-8 seam as the admissions/nurture stores (migration 0043) — is the
LEADERSHIP-input state only:

- ``page_flag``         — a page leadership flagged as underperforming for a content
  refresh (the spec's "Flag underperforming pages for content refresh"), optionally linked
  to the Content brief it produced (``brief_entry_id``) and/or the Decision-Queue card it
  raised (``decision_id``).
- ``analysis_request``  — a leadership request for analysis on a specific page or campaign
  (the spec's "Request analysis on specific pages or campaigns"), raised into the Decision
  Queue (``decision_id``).

- :class:`WebsiteStore` — the ABC every website-write route depends on.
- :class:`InMemoryWebsiteStore` — the v1 / CI-tested local impl (pure, no I/O), with a
  deterministic :meth:`InMemoryWebsiteStore.seed_demo` (no clock/random).
- :class:`SupabaseWebsiteStore` — the live impl over the 0043 tables, via the SAME
  PostgREST/service_role pattern as the admissions store.

All synthetic/aggregate data only (INV-1/INV-6 — NO real PII; page paths + reasons are
synthetic, never real visitors). Purity: plain data access — imports no ``app.ai`` /
``app.adapters`` modules, only the pure :class:`app.core.program.Program` enum + ``httpx``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

_REST = "/rest/v1"
_PAGE_FLAG_TABLE = f"{_REST}/page_flag"
_ANALYSIS_TABLE = f"{_REST}/analysis_request"

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 13). PII-free + clock/random-free: ids are
# UUID(int=...) and datetimes derive from a FIXED epoch. Two page flags (one open with a
# produced brief + raised decision, one resolved) and two analysis requests (one open with
# a raised decision, one resolved) so the surface renders sensibly but NOT maxed.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

# (page_path, site, reason, status, has_brief, has_decision, created_off, resolved_off)
_SEED_PAGE_FLAGS: tuple[tuple[str, str, str, str, bool, bool, int, int | None], ...] = (
    (
        "/blog/2-hour-learning",
        "gt.school",
        "62% bounce, traffic down 12% WoW — top-of-funnel explainer reads as thin.",
        "open",
        True,
        True,
        -4,
        None,
    ),
    (
        "/online-program",
        "anywhere.gt.school",
        "66% bounce on a key landing page — refreshed hero shipped, monitoring.",
        "resolved",
        True,
        False,
        -12,
        -3,
    ),
)

# (target, target_kind, question, status, has_decision, created_off, resolved_off)
_SEED_ANALYSIS: tuple[tuple[str, str, str, str, bool, int, int | None], ...] = (
    (
        "/tuition",
        "page",
        "Why did /tuition pageviews jump 19% WoW — which source drove it, and does it convert?",
        "open",
        True,
        -2,
        None,
    ),
    (
        "spring_open_house",
        "campaign",
        "Did the spring_open_house campaign actually move summer-camp registrations?",
        "resolved",
        False,
        -10,
        -5,
    ),
)


@dataclass(frozen=True)
class PageFlag:
    """One page leadership flagged as underperforming for a content refresh (0043 A)."""

    flag_id: UUID
    page_path: str
    site: str
    reason: str
    status: str
    brief_entry_id: UUID | None
    decision_id: UUID | None
    owner: str
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class AnalysisRequest:
    """One leadership request for analysis on a page/campaign (0043 B)."""

    request_id: UUID
    target: str
    target_kind: str
    question: str
    status: str
    decision_id: UUID | None
    owner: str
    created_at: datetime
    resolved_at: datetime | None


class WebsiteStore(ABC):
    """Read/write seam over the Module-13 leadership-input state (migration 0043).

    Every website-write route depends on this interface, never a concrete store. v1 binds
    the in-memory impl (seed-driven); production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped (the 0043
    tenancy tag) so one program's flags/requests never bleed into another's.
    """

    # ---------------------------------------------------------------- page flags
    @abstractmethod
    def list_page_flags(self, program: Program) -> list[PageFlag]:
        """The page-flag rows for ``program`` (created order)."""
        raise NotImplementedError

    @abstractmethod
    def create_page_flag(
        self,
        program: Program,
        *,
        flag_id: UUID | None = None,
        page_path: str,
        site: str,
        reason: str,
        status: str = "open",
        brief_entry_id: UUID | None = None,
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> PageFlag:
        """Create one page flag (gen a uuid when ``flag_id`` is None); return it."""
        raise NotImplementedError

    @abstractmethod
    def update_page_flag(self, program: Program, flag_id: UUID, **changes: Any) -> PageFlag:
        """Partially update one page flag (status / resolved_at / brief_entry_id /
        decision_id). Raises ``KeyError`` on an unknown ``flag_id`` (route → 404)."""
        raise NotImplementedError

    # --------------------------------------------------------- analysis requests
    @abstractmethod
    def list_analysis_requests(self, program: Program) -> list[AnalysisRequest]:
        """The analysis-request rows for ``program`` (created order)."""
        raise NotImplementedError

    @abstractmethod
    def create_analysis_request(
        self,
        program: Program,
        *,
        request_id: UUID | None = None,
        target: str,
        target_kind: str = "page",
        question: str,
        status: str = "open",
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> AnalysisRequest:
        """Create one analysis request (gen a uuid when None); return it."""
        raise NotImplementedError

    @abstractmethod
    def update_analysis_request(
        self, program: Program, request_id: UUID, **changes: Any
    ) -> AnalysisRequest:
        """Partially update one analysis request (status / resolved_at / decision_id).
        Raises ``KeyError`` on an unknown ``request_id`` (route → 404)."""
        raise NotImplementedError


_PAGE_FLAG_UPDATABLE: frozenset[str] = frozenset(
    {"status", "resolved_at", "brief_entry_id", "decision_id"}
)
_ANALYSIS_UPDATABLE: frozenset[str] = frozenset({"status", "resolved_at", "decision_id"})


class InMemoryWebsiteStore(WebsiteStore):
    """In-memory :class:`WebsiteStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseWebsiteStore` behind the same seam. :meth:`seed_demo` lays down the
    deterministic demo flags/requests (idempotent).
    """

    def __init__(self) -> None:
        self._flags: dict[Program, list[PageFlag]] = {}
        self._requests: dict[Program, list[AnalysisRequest]] = {}
        self._seeded: set[Program] = set()

    # ---------------------------------------------------------------- page flags
    def list_page_flags(self, program: Program) -> list[PageFlag]:
        return list(self._flags.get(program, []))

    def create_page_flag(
        self,
        program: Program,
        *,
        flag_id: UUID | None = None,
        page_path: str,
        site: str,
        reason: str,
        status: str = "open",
        brief_entry_id: UUID | None = None,
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> PageFlag:
        row = PageFlag(
            flag_id=flag_id if flag_id is not None else uuid4(),
            page_path=page_path,
            site=site,
            reason=reason,
            status=status,
            brief_entry_id=brief_entry_id,
            decision_id=decision_id,
            owner=owner,
            created_at=created_at if created_at is not None else datetime.now(UTC),
            resolved_at=resolved_at,
        )
        self._flags.setdefault(program, []).append(row)
        return row

    def update_page_flag(self, program: Program, flag_id: UUID, **changes: Any) -> PageFlag:
        applied = {k: v for k, v in changes.items() if k in _PAGE_FLAG_UPDATABLE and v is not None}
        rows = self._flags.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.flag_id == flag_id:
                updated = replace(existing, **applied)
                rows[i] = updated
                return updated
        raise KeyError(f"unknown page flag: {flag_id!r}")

    # --------------------------------------------------------- analysis requests
    def list_analysis_requests(self, program: Program) -> list[AnalysisRequest]:
        return list(self._requests.get(program, []))

    def create_analysis_request(
        self,
        program: Program,
        *,
        request_id: UUID | None = None,
        target: str,
        target_kind: str = "page",
        question: str,
        status: str = "open",
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> AnalysisRequest:
        row = AnalysisRequest(
            request_id=request_id if request_id is not None else uuid4(),
            target=target,
            target_kind=target_kind,
            question=question,
            status=status,
            decision_id=decision_id,
            owner=owner,
            created_at=created_at if created_at is not None else datetime.now(UTC),
            resolved_at=resolved_at,
        )
        self._requests.setdefault(program, []).append(row)
        return row

    def update_analysis_request(
        self, program: Program, request_id: UUID, **changes: Any
    ) -> AnalysisRequest:
        applied = {k: v for k, v in changes.items() if k in _ANALYSIS_UPDATABLE and v is not None}
        rows = self._requests.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.request_id == request_id:
                updated = replace(existing, **applied)
                rows[i] = updated
                return updated
        raise KeyError(f"unknown analysis request: {request_id!r}")

    # ------------------------------------------------------------------ demo seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo flags/requests (INV-1; idempotent)."""
        if program in self._seeded:
            return
        for i, (path, site, reason, status, has_brief, has_dec, c_off, r_off) in enumerate(
            _SEED_PAGE_FLAGS
        ):
            self.create_page_flag(
                program,
                flag_id=UUID(int=(0xB13_0000 + i)),
                page_path=path,
                site=site,
                reason=reason,
                status=status,
                brief_entry_id=UUID(int=(0xB13_1000 + i)) if has_brief else None,
                decision_id=UUID(int=(0xB13_2000 + i)) if has_dec else None,
                created_at=_SEED_EPOCH + timedelta(days=c_off),
                resolved_at=(_SEED_EPOCH + timedelta(days=r_off)) if r_off is not None else None,
            )
        for i, (target, kind, q, status, has_dec, c_off, r_off) in enumerate(_SEED_ANALYSIS):
            self.create_analysis_request(
                program,
                request_id=UUID(int=(0xB13_3000 + i)),
                target=target,
                target_kind=kind,
                question=q,
                status=status,
                decision_id=UUID(int=(0xB13_4000 + i)) if has_dec else None,
                created_at=_SEED_EPOCH + timedelta(days=c_off),
                resolved_at=(_SEED_EPOCH + timedelta(days=r_off)) if r_off is not None else None,
            )
        self._seeded.add(program)


class SupabaseWebsiteStore(WebsiteStore):
    """Live :class:`WebsiteStore` over Supabase PostgREST (service_role; 0043).

    Query-per-request (the stateless-runtime posture of the admissions store): each call
    issues a fresh PostgREST request over the injected (or per-call) ``httpx`` client. Both
    tables are program-scoped (``program_id``) so every read filters and every write stamps
    it. Inserts return the representation; updates PATCH by id. The ``service_role`` key
    BYPASSES RLS (server-only — INV-5 / D-RLS-4) and never leaves the backend.
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

    # ---------------------------------------------------------------- page flags
    _PAGE_FLAG_SELECT = (
        "flag_id,page_path,site,reason,status,brief_entry_id,decision_id,owner,"
        "created_at,resolved_at"
    )

    def list_page_flags(self, program: Program) -> list[PageFlag]:
        rows = self._request(
            "GET",
            _PAGE_FLAG_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._PAGE_FLAG_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_page_flag(r) for r in rows]

    def create_page_flag(
        self,
        program: Program,
        *,
        flag_id: UUID | None = None,
        page_path: str,
        site: str,
        reason: str,
        status: str = "open",
        brief_entry_id: UUID | None = None,
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> PageFlag:
        payload: dict[str, Any] = {
            "page_path": page_path,
            "site": site,
            "reason": reason,
            "status": status,
            "brief_entry_id": str(brief_entry_id) if brief_entry_id is not None else None,
            "decision_id": str(decision_id) if decision_id is not None else None,
            "owner": owner,
            "program_id": program.value,
        }
        if flag_id is not None:
            payload["flag_id"] = str(flag_id)
        if created_at is not None:
            payload["created_at"] = created_at.isoformat()
        if resolved_at is not None:
            payload["resolved_at"] = resolved_at.isoformat()
        rows = self._request(
            "POST",
            _PAGE_FLAG_TABLE,
            params={"on_conflict": "flag_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /page_flag returned no row")
        return _row_to_page_flag(rows[0])

    def update_page_flag(self, program: Program, flag_id: UUID, **changes: Any) -> PageFlag:
        payload = _patch_payload(changes, _PAGE_FLAG_UPDATABLE)
        patched = self._request(
            "PATCH",
            _PAGE_FLAG_TABLE,
            params={"program_id": f"eq.{program.value}", "flag_id": f"eq.{flag_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown page flag: {flag_id!r}")
        return _row_to_page_flag(patched[0])

    # --------------------------------------------------------- analysis requests
    _ANALYSIS_SELECT = (
        "request_id,target,target_kind,question,status,decision_id,owner,created_at,resolved_at"
    )

    def list_analysis_requests(self, program: Program) -> list[AnalysisRequest]:
        rows = self._request(
            "GET",
            _ANALYSIS_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "select": self._ANALYSIS_SELECT,
                "order": "created_at.asc",
            },
        )
        return [_row_to_analysis(r) for r in rows]

    def create_analysis_request(
        self,
        program: Program,
        *,
        request_id: UUID | None = None,
        target: str,
        target_kind: str = "page",
        question: str,
        status: str = "open",
        decision_id: UUID | None = None,
        owner: str = "website",
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> AnalysisRequest:
        payload: dict[str, Any] = {
            "target": target,
            "target_kind": target_kind,
            "question": question,
            "status": status,
            "decision_id": str(decision_id) if decision_id is not None else None,
            "owner": owner,
            "program_id": program.value,
        }
        if request_id is not None:
            payload["request_id"] = str(request_id)
        if created_at is not None:
            payload["created_at"] = created_at.isoformat()
        if resolved_at is not None:
            payload["resolved_at"] = resolved_at.isoformat()
        rows = self._request(
            "POST",
            _ANALYSIS_TABLE,
            params={"on_conflict": "request_id"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /analysis_request returned no row")
        return _row_to_analysis(rows[0])

    def update_analysis_request(
        self, program: Program, request_id: UUID, **changes: Any
    ) -> AnalysisRequest:
        payload = _patch_payload(changes, _ANALYSIS_UPDATABLE)
        patched = self._request(
            "PATCH",
            _ANALYSIS_TABLE,
            params={"program_id": f"eq.{program.value}", "request_id": f"eq.{request_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown analysis request: {request_id!r}")
        return _row_to_analysis(patched[0])


def _patch_payload(changes: dict[str, Any], updatable: frozenset[str]) -> dict[str, Any]:
    """Serialise a partial-update mapping to PostgREST-safe JSON (uuid/datetime → str)."""
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in updatable or v is None:
            continue
        payload[k] = (
            str(v) if isinstance(v, UUID) else (v.isoformat() if isinstance(v, datetime) else v)
        )
    return payload


def _parse_dt(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _opt_uuid(raw: object) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _row_to_page_flag(row: dict[str, Any]) -> PageFlag:
    """Map a PostgREST ``page_flag`` row to :class:`PageFlag`."""
    return PageFlag(
        flag_id=UUID(str(row["flag_id"])),
        page_path=str(row.get("page_path") or ""),
        site=str(row.get("site") or "gt.school"),
        reason=str(row.get("reason") or ""),
        status=str(row.get("status") or "open"),
        brief_entry_id=_opt_uuid(row.get("brief_entry_id")),
        decision_id=_opt_uuid(row.get("decision_id")),
        owner=str(row.get("owner") or "website"),
        created_at=_parse_dt(row.get("created_at")) or _SEED_EPOCH,
        resolved_at=_parse_dt(row.get("resolved_at")),
    )


def _row_to_analysis(row: dict[str, Any]) -> AnalysisRequest:
    """Map a PostgREST ``analysis_request`` row to :class:`AnalysisRequest`."""
    return AnalysisRequest(
        request_id=UUID(str(row["request_id"])),
        target=str(row.get("target") or ""),
        target_kind=str(row.get("target_kind") or "page"),
        question=str(row.get("question") or ""),
        status=str(row.get("status") or "open"),
        decision_id=_opt_uuid(row.get("decision_id")),
        owner=str(row.get("owner") or "website"),
        created_at=_parse_dt(row.get("created_at")) or _SEED_EPOCH,
        resolved_at=_parse_dt(row.get("resolved_at")),
    )


def build_supabase_website_store() -> SupabaseWebsiteStore | None:
    """Construct the Supabase website store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.admissions_store.build_supabase_admissions_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY``, returning ``None`` when either is
    absent or a placeholder ``<...>`` sentinel — so the caller falls back to the in-memory
    store (A-3).
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseWebsiteStore(base_url=url, service_role_key=key)

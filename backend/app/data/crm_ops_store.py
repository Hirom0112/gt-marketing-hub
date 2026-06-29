"""CRM-Ops store (Module 7) — the program-scoped data-quality queue + fix log.

The CRM-Ops surface owns two pieces of program-scoped state behind the same NFR-8 store
seam as the nurture/field-events stores (migration 0041):

- ``data_quality_issue`` — the persisted data-quality QUEUE. AUTO-detected issues carry a
  deterministic ``signature`` (entity_ref + kind) so a rescan UPSERTS (never duplicates)
  and existing acknowledged/resolved rows KEEP their status. MANUAL issues are filed by an
  owner. ``entity_ref`` is a SYNTHETIC token (a family id), NEVER PII (INV-1/INV-6).
- ``crm_fix_log`` — the applied-fix change log (a UTM normalization or a scoring-model
  change). The honest log the source-tracking + lead-scoring views render.

- :class:`CrmOpsStore` — the ABC every CRM-Ops route depends on.
- :class:`InMemoryCrmOpsStore` — the v1 / CI-tested local impl (pure, no I/O), with a
  deterministic :meth:`InMemoryCrmOpsStore.seed_demo` (no clock/random).
- :class:`SupabaseCrmOpsStore` — the live impl over the 0041 tables, via the SAME
  PostgREST/service_role pattern as the nurture store. The upsert passes ``on_conflict``
  in the PostgREST URL (the bit that bit us before).

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.program.Program` enum and ``httpx`` (the house transport).
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

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the nurture store makes). The 0041 names.
_REST = "/rest/v1"
_ISSUE_TABLE = f"{_REST}/data_quality_issue"
_FIX_TABLE = f"{_REST}/crm_fix_log"

# The closed vocabularies (the 0041 CHECKs mirror these; INV-11 carve-out, like the
# nurture store's SMS_STATUSES). Named, not bare literals.
CATEGORIES: tuple[str, ...] = ("utm", "sync", "scoring", "tracking", "other")
ISSUE_STATUSES: tuple[str, ...] = ("open", "acknowledged", "resolved")
ISSUE_SOURCES: tuple[str, ...] = ("auto", "manual")
SEVERITIES: tuple[str, ...] = ("high", "medium", "low")
FIX_KINDS: tuple[str, ...] = ("utm_fix", "scoring_change")

# The mutable columns an `update_issue` partial change may target.
_ISSUE_UPDATABLE: frozenset[str] = frozenset(
    {"status", "priority", "resolution", "resolved_by", "resolved_at"}
)

# ----------------------------------------------------------------------------- #
# Deterministic demo seed (Module 7). PII-free (INV-1) + clock/random-free: ids are
# UUID(int=...), datetimes derive from a FIXED epoch (no clock). A few manual + auto
# issues across categories incl one resolved (for the resolution log) + a few fix-log
# entries incl a scoring-change and utm-fixes.
# ----------------------------------------------------------------------------- #
_SEED_EPOCH = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

# (signature, category, kind, severity, description, source, status, entity_ref, priority,
#  resolution, resolved_by, resolved_offset_hours) — ~5 issues across the categories.
_SEED_ISSUES: tuple[
    tuple[str, str, str, str, str, str, str, str, str, str, str, int | None], ...
] = (
    (
        "Family-0007:utm_broken",
        "utm",
        "utm_broken",
        "high",
        "Broken UTM: utm_medium 'e-mail' not in the allowed-medium set.",
        "auto",
        "open",
        "Family-0007",
        "urgent",
        "",
        "",
        None,
    ),
    (
        "Family-0012:conflict",
        "sync",
        "conflict",
        "high",
        "Supabase and HubSpot diverge on stage — needs a reconcile decision.",
        "auto",
        "acknowledged",
        "Family-0012",
        "normal",
        "",
        "",
        None,
    ),
    (
        "manual:scoring-review-01",
        "scoring",
        "scoring_review",
        "medium",
        "Lead-score model flagged for review: threshold may be too low for fall cohort.",
        "manual",
        "open",
        "",
        "normal",
        "",
        "",
        None,
    ),
    (
        "Family-0021:unreliable_field",
        "other",
        "unreliable_field",
        "low",
        "Low-trust field 'income_tier' present — value is self-reported, unreliable.",
        "auto",
        "open",
        "Family-0021",
        "normal",
        "",
        "",
        None,
    ),
    (
        "manual:tracking-fix-01",
        "tracking",
        "missing_field",
        "medium",
        "Form submissions missing utm_campaign — landing-page tag was dropped.",
        "manual",
        "resolved",
        "",
        "normal",
        "Re-added the campaign tag to the apply landing page; backfilled the gap.",
        "leader",
        -48,
    ),
)

# (kind, summary, actor, applied_offset_hours) — ~3 fix-log entries incl a scoring change.
_SEED_FIXES: tuple[tuple[str, str, str, int], ...] = (
    (
        "utm_fix",
        "Normalized utm_medium 'e-mail' → 'email' on the email nurture campaign.",
        "crm",
        -6,
    ),
    (
        "utm_fix",
        "Backfilled missing utm_campaign on the apply landing page.",
        "leader",
        -48,
    ),
    (
        "scoring_change",
        "Raised the lead-score qualification threshold 55 → 60 for the fall cohort.",
        "leader",
        -24,
    ),
)


@dataclass(frozen=True)
class CrmOpsIssue:
    """One persisted data-quality issue (queue row). ``entity_ref`` is SYNTHETIC (INV-1)."""

    issue_id: UUID
    signature: str
    category: str
    kind: str
    severity: str
    description: str
    owner: str
    status: str
    source: str
    entity_ref: str
    priority: str
    created_at: datetime
    resolved_at: datetime | None
    resolution: str
    resolved_by: str


@dataclass(frozen=True)
class CrmFixLogEntry:
    """One applied CRM-Ops fix (a UTM normalization or a scoring-model change)."""

    fix_id: UUID
    kind: str
    summary: str
    actor: str
    applied_at: datetime


class CrmOpsStore(ABC):
    """Read/write seam over the Module-7 CRM-Ops state (migration 0041).

    Every CRM-Ops route depends on this interface, never a concrete store. v1 binds the
    in-memory impl (seed-driven); production swaps the Supabase-backed one with zero
    caller changes (the NFR-8 store-seam pattern). Every method is program-scoped (the
    0041 tenancy tag) so one program's CRM-Ops state never bleeds into another's.
    """

    # -------------------------------------------------------------- issue queue
    @abstractmethod
    def list_issues(self, program: Program, *, status: str | None = None) -> list[CrmOpsIssue]:
        """The data-quality issues for ``program``, optionally filtered by ``status``."""
        raise NotImplementedError

    @abstractmethod
    def upsert_issue(
        self,
        program: Program,
        *,
        signature: str,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        source: str = "auto",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        """Idempotently upsert an AUTO-detected issue keyed on ``signature``.

        A rescan dedups: an existing row with the same ``(program, signature)`` KEEPS its
        ``status``/``resolution``/``resolved_*``/``issue_id``/``created_at`` (so an
        acknowledged/resolved issue is never reopened) while refreshing the derived
        description/severity/category. A new signature inserts an OPEN row.
        """
        raise NotImplementedError

    @abstractmethod
    def file_issue(
        self,
        program: Program,
        *,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        """File a MANUAL (source='manual') issue — each one a distinct OPEN row."""
        raise NotImplementedError

    @abstractmethod
    def update_issue(self, program: Program, issue_id: UUID, **changes: Any) -> CrmOpsIssue:
        """Partially update one issue (acknowledge / prioritize / resolve).

        Only the columns in :data:`_ISSUE_UPDATABLE` may change. Raises ``KeyError`` on an
        unknown ``issue_id`` (the route maps it to a 404).
        """
        raise NotImplementedError

    # ----------------------------------------------------------------- fix log
    @abstractmethod
    def list_fix_log(self, program: Program, *, kind: str | None = None) -> list[CrmFixLogEntry]:
        """The applied-fix log for ``program``, optionally filtered by ``kind``."""
        raise NotImplementedError

    @abstractmethod
    def append_fix_log(
        self, program: Program, *, kind: str, summary: str, actor: str
    ) -> CrmFixLogEntry:
        """Append one applied-fix entry; return it."""
        raise NotImplementedError


class InMemoryCrmOpsStore(CrmOpsStore):
    """In-memory :class:`CrmOpsStore` — per-program lists; pure, no I/O.

    The v1 local store (A-3) and the CI-tested path. A production deploy swaps
    :class:`SupabaseCrmOpsStore` behind the same seam. :meth:`seed_demo` lays down the
    deterministic demo issues/fix-log (idempotent).
    """

    def __init__(self) -> None:
        self._issues: dict[Program, list[CrmOpsIssue]] = {}
        self._fixes: dict[Program, list[CrmFixLogEntry]] = {}
        self._seeded: set[Program] = set()

    # -------------------------------------------------------------- issue queue
    def list_issues(self, program: Program, *, status: str | None = None) -> list[CrmOpsIssue]:
        rows = list(self._issues.get(program, []))
        if status is not None:
            rows = [r for r in rows if r.status == status]
        return rows

    def upsert_issue(
        self,
        program: Program,
        *,
        signature: str,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        source: str = "auto",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        rows = self._issues.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.signature == signature:
                # Dedup: KEEP status/resolution/resolved_*/issue_id/created_at; refresh
                # only the derived description/severity/category.
                refreshed = replace(
                    existing,
                    category=category,
                    kind=kind,
                    severity=severity,
                    description=description,
                )
                rows[i] = refreshed
                return refreshed
        issue = CrmOpsIssue(
            issue_id=uuid4(),
            signature=signature,
            category=category,
            kind=kind,
            severity=severity,
            description=description,
            owner=owner,
            status="open",
            source=source,
            entity_ref=entity_ref,
            priority=priority,
            created_at=datetime.now(UTC),
            resolved_at=None,
            resolution="",
            resolved_by="",
        )
        rows.append(issue)
        return issue

    def file_issue(
        self,
        program: Program,
        *,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        issue = CrmOpsIssue(
            issue_id=uuid4(),
            signature=f"manual:{uuid4()}",
            category=category,
            kind=kind,
            severity=severity,
            description=description,
            owner=owner,
            status="open",
            source="manual",
            entity_ref=entity_ref,
            priority=priority,
            created_at=datetime.now(UTC),
            resolved_at=None,
            resolution="",
            resolved_by="",
        )
        self._issues.setdefault(program, []).append(issue)
        return issue

    def update_issue(self, program: Program, issue_id: UUID, **changes: Any) -> CrmOpsIssue:
        applied = {k: v for k, v in changes.items() if k in _ISSUE_UPDATABLE and v is not None}
        rows = self._issues.setdefault(program, [])
        for i, existing in enumerate(rows):
            if existing.issue_id == issue_id:
                # Resolving stamps resolved_at when not explicitly provided.
                if applied.get("status") == "resolved" and "resolved_at" not in applied:
                    applied["resolved_at"] = datetime.now(UTC)
                updated = replace(existing, **applied)
                rows[i] = updated
                return updated
        raise KeyError(f"unknown data-quality issue: {issue_id!r}")

    # ----------------------------------------------------------------- fix log
    def list_fix_log(self, program: Program, *, kind: str | None = None) -> list[CrmFixLogEntry]:
        rows = list(self._fixes.get(program, []))
        if kind is not None:
            rows = [r for r in rows if r.kind == kind]
        return rows

    def append_fix_log(
        self, program: Program, *, kind: str, summary: str, actor: str
    ) -> CrmFixLogEntry:
        entry = CrmFixLogEntry(
            fix_id=uuid4(),
            kind=kind,
            summary=summary,
            actor=actor,
            applied_at=datetime.now(UTC),
        )
        self._fixes.setdefault(program, []).append(entry)
        return entry

    # ------------------------------------------------------------------ seed
    def seed_demo(self, program: Program) -> None:
        """Lay down the deterministic demo CRM-Ops state (INV-1; idempotent).

        Clock/random-free: ids are derived (``UUID(int=...)``) and datetimes derive from
        :data:`_SEED_EPOCH`. ~5 issues across the categories (incl one resolved for the
        resolution log) + ~3 fix-log entries (incl a scoring-change + utm-fixes). Re-seeding
        the same program is a guarded no-op.
        """
        if program in self._seeded:
            return

        issues = self._issues.setdefault(program, [])
        for i, (
            signature,
            category,
            kind,
            severity,
            description,
            source,
            status,
            entity_ref,
            priority,
            resolution,
            resolved_by,
            resolved_off,
        ) in enumerate(_SEED_ISSUES):
            resolved_at = (
                None if resolved_off is None else _SEED_EPOCH + timedelta(hours=resolved_off)
            )
            issues.append(
                CrmOpsIssue(
                    issue_id=UUID(int=(0x4F51_0000 + i)),
                    signature=signature,
                    category=category,
                    kind=kind,
                    severity=severity,
                    description=description,
                    owner="crm",
                    status=status,
                    source=source,
                    entity_ref=entity_ref,
                    priority=priority,
                    created_at=_SEED_EPOCH - timedelta(hours=i * 3),
                    resolved_at=resolved_at,
                    resolution=resolution,
                    resolved_by=resolved_by,
                )
            )

        fixes = self._fixes.setdefault(program, [])
        for i, (kind, summary, actor, off_h) in enumerate(_SEED_FIXES):
            fixes.append(
                CrmFixLogEntry(
                    fix_id=UUID(int=(0x4F52_0000 + i)),
                    kind=kind,
                    summary=summary,
                    actor=actor,
                    applied_at=_SEED_EPOCH + timedelta(hours=off_h),
                )
            )

        self._seeded.add(program)


class SupabaseCrmOpsStore(CrmOpsStore):
    """Live :class:`CrmOpsStore` over Supabase PostgREST (service_role; 0041).

    Query-per-request (the stateless-runtime posture of the nurture store): each call
    issues a fresh PostgREST request over the injected (or per-call) ``httpx`` client.
    Every table is program-scoped (``program_id`` is the 0041 tenancy tag) so every read
    filters and every write stamps it. The ``service_role`` key BYPASSES RLS (server-only
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

    _ISSUE_SELECT = (
        "issue_id,signature,category,kind,severity,description,owner,status,source,"
        "entity_ref,priority,created_at,resolved_at,resolution,resolved_by"
    )

    # -------------------------------------------------------------- issue queue
    def list_issues(self, program: Program, *, status: str | None = None) -> list[CrmOpsIssue]:
        params = {
            "program_id": f"eq.{program.value}",
            "select": self._ISSUE_SELECT,
            "order": "created_at.desc",
        }
        if status is not None:
            params["status"] = f"eq.{status}"
        return [_row_to_issue(r) for r in self._request("GET", _ISSUE_TABLE, params=params)]

    def upsert_issue(
        self,
        program: Program,
        *,
        signature: str,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        source: str = "auto",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        # An existing row (any status) is KEPT — only its derived fields refresh — so an
        # acknowledged/resolved issue is never reopened by a rescan.
        existing = self._request(
            "GET",
            _ISSUE_TABLE,
            params={
                "program_id": f"eq.{program.value}",
                "signature": f"eq.{signature}",
                "select": self._ISSUE_SELECT,
            },
        )
        if existing:
            patched = self._request(
                "PATCH",
                _ISSUE_TABLE,
                params={"program_id": f"eq.{program.value}", "signature": f"eq.{signature}"},
                payload={
                    "category": category,
                    "kind": kind,
                    "severity": severity,
                    "description": description,
                },
                prefer="return=representation",
            )
            return _row_to_issue(patched[0])
        payload = {
            "signature": signature,
            "category": category,
            "kind": kind,
            "severity": severity,
            "description": description,
            "owner": owner,
            "status": "open",
            "source": source,
            "entity_ref": entity_ref,
            "priority": priority,
            "program_id": program.value,
        }
        rows = self._request(
            "POST",
            _ISSUE_TABLE,
            params={"on_conflict": "program_id,signature"},
            payload=payload,
            prefer="return=representation,resolution=merge-duplicates",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /data_quality_issue returned no representation row")
        return _row_to_issue(rows[0])

    def file_issue(
        self,
        program: Program,
        *,
        category: str,
        kind: str,
        severity: str,
        description: str,
        owner: str = "crm",
        entity_ref: str = "",
        priority: str = "normal",
    ) -> CrmOpsIssue:
        payload = {
            "signature": f"manual:{uuid4()}",
            "category": category,
            "kind": kind,
            "severity": severity,
            "description": description,
            "owner": owner,
            "status": "open",
            "source": "manual",
            "entity_ref": entity_ref,
            "priority": priority,
            "program_id": program.value,
        }
        rows = self._request(
            "POST",
            _ISSUE_TABLE,
            payload=payload,
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /data_quality_issue returned no representation row")
        return _row_to_issue(rows[0])

    def update_issue(self, program: Program, issue_id: UUID, **changes: Any) -> CrmOpsIssue:
        payload: dict[str, Any] = {
            k: v for k, v in changes.items() if k in _ISSUE_UPDATABLE and v is not None
        }
        if payload.get("status") == "resolved" and "resolved_at" not in payload:
            payload["resolved_at"] = "now()"
        if isinstance(payload.get("resolved_at"), datetime):
            payload["resolved_at"] = payload["resolved_at"].isoformat()
        patched = self._request(
            "PATCH",
            _ISSUE_TABLE,
            params={"program_id": f"eq.{program.value}", "issue_id": f"eq.{issue_id}"},
            payload=payload,
            prefer="return=representation",
        )
        if not patched:
            raise KeyError(f"unknown data-quality issue: {issue_id!r}")
        return _row_to_issue(patched[0])

    # ----------------------------------------------------------------- fix log
    def list_fix_log(self, program: Program, *, kind: str | None = None) -> list[CrmFixLogEntry]:
        params = {
            "program_id": f"eq.{program.value}",
            "select": "fix_id,kind,summary,actor,applied_at",
            "order": "applied_at.desc",
        }
        if kind is not None:
            params["kind"] = f"eq.{kind}"
        return [_row_to_fix(r) for r in self._request("GET", _FIX_TABLE, params=params)]

    def append_fix_log(
        self, program: Program, *, kind: str, summary: str, actor: str
    ) -> CrmFixLogEntry:
        rows = self._request(
            "POST",
            _FIX_TABLE,
            payload={
                "kind": kind,
                "summary": summary,
                "actor": actor,
                "program_id": program.value,
            },
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("PostgREST POST /crm_fix_log returned no representation row")
        return _row_to_fix(rows[0])


def _parse_dt(raw: object) -> datetime | None:
    """Parse a PostgREST timestamptz to a tz-aware datetime, or ``None`` when absent."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _row_to_issue(row: dict[str, Any]) -> CrmOpsIssue:
    """Map a PostgREST ``data_quality_issue`` row to :class:`CrmOpsIssue`."""
    return CrmOpsIssue(
        issue_id=UUID(str(row["issue_id"])),
        signature=str(row.get("signature") or ""),
        category=str(row.get("category") or "other"),
        kind=str(row.get("kind") or ""),
        severity=str(row.get("severity") or "medium"),
        description=str(row.get("description") or ""),
        owner=str(row.get("owner") or "crm"),
        status=str(row.get("status") or "open"),
        source=str(row.get("source") or "auto"),
        entity_ref=str(row.get("entity_ref") or ""),
        priority=str(row.get("priority") or "normal"),
        created_at=_parse_dt(row.get("created_at")) or _SEED_EPOCH,
        resolved_at=_parse_dt(row.get("resolved_at")),
        resolution=str(row.get("resolution") or ""),
        resolved_by=str(row.get("resolved_by") or ""),
    )


def _row_to_fix(row: dict[str, Any]) -> CrmFixLogEntry:
    """Map a PostgREST ``crm_fix_log`` row to :class:`CrmFixLogEntry`."""
    return CrmFixLogEntry(
        fix_id=UUID(str(row["fix_id"])),
        kind=str(row.get("kind") or ""),
        summary=str(row.get("summary") or ""),
        actor=str(row.get("actor") or ""),
        applied_at=_parse_dt(row.get("applied_at")) or _SEED_EPOCH,
    )


def build_supabase_crm_ops_store() -> SupabaseCrmOpsStore | None:
    """Construct the Supabase CRM-Ops store from the env, or ``None`` when unbound.

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
    return SupabaseCrmOpsStore(base_url=url, service_role_key=key)

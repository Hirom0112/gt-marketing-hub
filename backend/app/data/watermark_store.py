"""Durable per-program CRM-poll watermark store — the A2 poll-state seam.

The CRM-as-truth incremental poller (``app.api.crm_sync``) needs ONE durable piece
of state per ``(program, object_type)``: the last-synced HubSpot
``hs_lastmodifieddate`` watermark. On each poll it READS the watermark, pulls
records modified strictly after it, reconciles them, and ADVANCES the watermark to
the max ``hs_lastmodifieddate`` seen (the pure :func:`app.core.crm_sync.advance_watermark`).
This module is the NFR-8 store seam for that state — the same shape as the
:class:`app.data.repository.FamilyRepository`:

- :class:`WatermarkStore` — the ABC every poll endpoint depends on.
- :class:`InMemoryWatermarkStore` — the v1 / CI-tested local impl (a dict).
- :class:`SupabaseWatermarkStore` — the live impl over the ``crm_sync_watermark``
  table (migration 0025), via the SAME PostgREST/service_role pattern as
  :class:`app.data.supabase_repository.SupabaseFamilyRepository`.

The store is deliberately dumb: it stores and returns whatever it is handed. The
"never move the watermark backward" rule is the CALLER's (the poller advances the
watermark with :func:`advance_watermark` and only writes when it strictly moved
forward) — the store does not second-guess a write.

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only
the pure :class:`app.core.program.Program` enum and ``httpx`` (the house transport,
already a runtime dep).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed route — INV-11 does not apply to a third
# party's URL, the same carve-out the family repo makes). The 0025 table name.
_REST = "/rest/v1"
_WATERMARK_TABLE = f"{_REST}/crm_sync_watermark"


class WatermarkStore(ABC):
    """Read/write seam over the per-program CRM-poll watermark (A2; migration 0025).

    Every poll endpoint depends on this interface, never a concrete store. v1 binds
    the in-memory impl; production swaps the Supabase-backed one with zero caller
    changes (the NFR-8 store-seam pattern).
    """

    @abstractmethod
    def get_watermark(self, program: Program, object_type: str) -> datetime | None:
        """The last-synced watermark for ``(program, object_type)``, or ``None``.

        ``None`` means the object type has never been synced for this program — the
        poller does a cold full backfill from the epoch sentinel and then advances.
        """
        raise NotImplementedError

    @abstractmethod
    def set_watermark(self, program: Program, object_type: str, value: datetime) -> None:
        """Persist the advanced watermark for ``(program, object_type)`` (upsert).

        Idempotent on ``(program, object_type)``: re-writing the same value is a
        no-op in shape. The store does NOT enforce monotonicity — the caller only
        calls this when the watermark strictly advanced (the poller's contract).
        """
        raise NotImplementedError


class InMemoryWatermarkStore(WatermarkStore):
    """In-memory :class:`WatermarkStore` — a dict keyed ``(program, object_type)``.

    The v1 local store (A-3) and the CI-tested path: no credential, no I/O. A
    production deploy swaps :class:`SupabaseWatermarkStore` behind the same seam.
    """

    def __init__(self) -> None:
        self._watermarks: dict[tuple[Program, str], datetime] = {}

    def get_watermark(self, program: Program, object_type: str) -> datetime | None:
        return self._watermarks.get((program, object_type))

    def set_watermark(self, program: Program, object_type: str, value: datetime) -> None:
        self._watermarks[(program, object_type)] = value


class SupabaseWatermarkStore(WatermarkStore):
    """Live :class:`WatermarkStore` over Supabase PostgREST (service_role; 0025).

    Query-per-request (the stateless-Lambda posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call issues
    a fresh PostgREST request over the injected (or per-call) ``httpx`` client. The
    watermark row is itself program-scoped — ``program_id`` is the 0025 tenancy tag —
    so every read filters and every write stamps it. The ``service_role`` key
    BYPASSES RLS (server-only — INV-5 / D-RLS-4) and never leaves the backend.

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

    def _patch(self, path: str, params: dict[str, str], payload: dict[str, Any]) -> None:
        """One PostgREST PATCH (the watermark-advance write; fail loud)."""
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

    def _post(self, path: str, payload: dict[str, Any]) -> None:
        """One PostgREST POST (the first-ever watermark insert; fail loud)."""
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
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

    # ---------------------------------------------------------------- interface
    def get_watermark(self, program: Program, object_type: str) -> datetime | None:
        rows = self._get(
            _WATERMARK_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "object_type": f"eq.{object_type}",
                "select": "watermark_modified_at",
            },
        )
        if not rows:
            return None
        raw = rows[0].get("watermark_modified_at")
        return _parse_timestamp(raw)

    def set_watermark(self, program: Program, object_type: str, value: datetime) -> None:
        # Upsert on (program_id, object_type) via the existing-row check (the same
        # PATCH-or-POST pattern the family repo's `write_cursor` uses): a fresh row
        # is INSERTed, an existing one is UPDATEd. The 0025 UNIQUE(program_id,
        # object_type) constraint makes this idempotent.
        existing = self._get(
            _WATERMARK_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "object_type": f"eq.{object_type}",
                "select": "id",
            },
        )
        if existing:
            self._patch(
                _WATERMARK_TABLE,
                {"program_id": f"eq.{program.value}", "object_type": f"eq.{object_type}"},
                {"watermark_modified_at": value.isoformat(), "updated_at": "now()"},
            )
        else:
            self._post(
                _WATERMARK_TABLE,
                {
                    "program_id": program.value,
                    "object_type": object_type,
                    "watermark_modified_at": value.isoformat(),
                },
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


def build_supabase_watermark_store() -> SupabaseWatermarkStore | None:
    """Construct the Supabase watermark store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.supabase_repository.build_supabase_repository`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a
    placeholder ``<...>`` sentinel — so the caller falls back to the in-memory store
    (A-3). No program is threaded in: this store is constructed program-agnostic and
    bounded per call by the ``program`` argument each method already takes.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseWatermarkStore(base_url=url, service_role_key=key)

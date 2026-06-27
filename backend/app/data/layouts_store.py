"""Per-user Home layout store — the B3 composable-Home persistence seam.

The composable Home (B3) persists ONE piece of state per auth user: the
react-grid-layout placement array the operator arranged. On each Home load the
API READS the saved layout, reconciles it against the current widget registry
(the pure :func:`app.core.dashboard_layout.merge_starter_pack`), and serves the
result; on each save it UPSERTS the new placement array. This module is the NFR-8
store seam for that state — the same shape as
:class:`app.data.watermark_store.WatermarkStore` and
:class:`app.data.decisions_store.DecisionsStore`:

- :class:`LayoutsStore` — the ABC every layout route depends on.
- :class:`InMemoryLayoutsStore` — the v1 / CI-tested local impl (a dict keyed on
  ``user_id``; pure, no I/O).
- :class:`SupabaseLayoutsStore` — the live impl over the ``user_dashboard_layouts``
  table (migration 0029), via the SAME PostgREST/service_role pattern as the
  family/watermark/decisions stores (exercised only against a real DB).

The store is deliberately dumb: it stores and returns the placement list it is
handed. The reconcile (drop unknown widgets, re-hydrate missing starters) is the
CALLER's — the route runs ``merge_starter_pack`` on the way out; the store does
not interpret the layout's contents.

Scoping is by ``user_id`` (the verified principal's ``sub``). The table is
OWNER-scoped (RLS, migration 0029) and the API clamps reads/writes to the
principal's own row (the app-layer IDOR defense) — there is no cross-user read
path through this store: every method takes the ``user_id`` to operate on.

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only
``httpx`` (the house transport, already a runtime dep) and the shared
:class:`app.data.supabase_repository.SupabaseError`.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

import httpx

from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed route — INV-11 does not apply to a third
# party's URL, the same carve-out the family/watermark/decisions stores make). The
# 0029 table name.
_REST = "/rest/v1"
_LAYOUTS_TABLE = f"{_REST}/user_dashboard_layouts"

# An RGL placement array — a list of placement dicts (``{"i", "x", "y", "w", "h"}``).
# Modeled loosely; the API owns the strict serialization schema and the merge.
Placement = dict[str, Any]


class LayoutsStore(ABC):
    """Read/write seam over the per-user Home layout (B3; migration 0029).

    Every layout route depends on this interface, never a concrete store. v1 binds
    the in-memory impl; production swaps the Supabase-backed one with zero caller
    changes (the NFR-8 store-seam pattern). Both methods are keyed on ``user_id``
    (the auth user) — the owner-scoped tenancy of the 0029 table.
    """

    @abstractmethod
    def get_layout(self, user_id: UUID) -> list[Placement] | None:
        """The saved RGL placement array for ``user_id``, or ``None`` if never saved.

        ``None`` means the user has never persisted a layout — the caller serves
        the starter pack (``merge_starter_pack(None, ...)``) without persisting.
        """
        raise NotImplementedError

    @abstractmethod
    def put_layout(self, user_id: UUID, layout: list[Placement]) -> None:
        """UPSERT ``user_id``'s saved layout (overwrite in place; the table is mutable).

        One row per user (``user_id`` is the PK), so a save replaces the prior
        layout and refreshes ``updated_at``. The store records the placement list
        verbatim; the caller has already validated/merged it.
        """
        raise NotImplementedError


class InMemoryLayoutsStore(LayoutsStore):
    """In-memory :class:`LayoutsStore` — a per-user dict; no credential, no I/O.

    The v1 local store (A-3) and the CI-tested path. Layouts live in a dict keyed
    by ``user_id``; a save overwrites the prior value (the table's mutable, one-row-
    per-user semantics). A production deploy swaps :class:`SupabaseLayoutsStore`
    behind the same seam.
    """

    def __init__(self) -> None:
        self._layouts: dict[UUID, list[Placement]] = {}

    def get_layout(self, user_id: UUID) -> list[Placement] | None:
        saved = self._layouts.get(user_id)
        # Defensive copy so a caller mutating the returned list never edits our state.
        return [dict(p) for p in saved] if saved is not None else None

    def put_layout(self, user_id: UUID, layout: list[Placement]) -> None:
        # Store a copy so a later caller mutation of `layout` does not leak in.
        self._layouts[user_id] = [dict(p) for p in layout]


class SupabaseLayoutsStore(LayoutsStore):
    """Live :class:`LayoutsStore` over Supabase PostgREST (service_role; 0029).

    Query-per-request (the stateless-runtime posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call issues
    a fresh PostgREST request over the injected (or per-call) ``httpx`` client. The
    table is owner-scoped (RLS), but this server-side store uses the ``service_role``
    key (BYPASSRLS — server-only, INV-5 / D-RLS-4) and bounds every read/write to the
    ``user_id`` argument the API has already clamped to the verified principal.

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

    def _get(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """One PostgREST GET → the decoded JSON array (fail loud on non-2xx)."""
        url = f"{self._base_url}{_LAYOUTS_TABLE}"
        headers = self._headers()
        if self._client is not None:
            response = self._client.get(url, params=params, headers=headers)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST GET {_LAYOUTS_TABLE} → {response.status_code}: {response.text[:300]}"
            )
        body: Any = response.json()
        if not isinstance(body, list):
            raise SupabaseError(f"PostgREST GET {_LAYOUTS_TABLE} returned a non-array body")
        return body

    def _upsert(self, payload: dict[str, Any]) -> None:
        """One PostgREST UPSERT (POST + ``resolution=merge-duplicates``); fail loud.

        ``user_id`` is the PK, so ``Prefer: resolution=merge-duplicates`` turns the
        insert into an in-place overwrite when the row already exists (the table's
        mutable one-row-per-user semantics).
        """
        url = f"{self._base_url}{_LAYOUTS_TABLE}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        if self._client is not None:
            response = self._client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST POST {_LAYOUTS_TABLE} → {response.status_code}: {response.text[:300]}"
            )

    # ---------------------------------------------------------------- interface
    def get_layout(self, user_id: UUID) -> list[Placement] | None:
        rows = self._get(
            {
                "user_id": f"eq.{user_id}",
                "select": "layout",
            }
        )
        if not rows:
            return None
        layout = rows[0].get("layout")
        return layout if isinstance(layout, list) else None

    def put_layout(self, user_id: UUID, layout: list[Placement]) -> None:
        self._upsert({"user_id": str(user_id), "layout": layout})


def build_supabase_layouts_store() -> SupabaseLayoutsStore | None:
    """Construct the Supabase layouts store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.decisions_store.build_supabase_decisions_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a
    placeholder ``<...>`` sentinel — so the caller falls back to the in-memory store
    (A-3). The store is constructed program-agnostic (a layout is a personal, global
    preference — migration 0029 carries no ``program_id``).
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseLayoutsStore(base_url=url, service_role_key=key)

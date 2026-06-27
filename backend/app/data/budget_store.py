"""Budget Tracker store — the B4 workstream + spend-ledger data seam.

The Budget Tracker owns two pieces of state behind the same NFR-8 store seam as the
decisions/layouts stores: the four budget WORKSTREAMS (Grassroots / Content /
Guerrilla / Ops, each with its planned allocation) and the APPEND-ONLY spend/
commitment LEDGER that rolls up against them. The allocations' single canonical home
is ``params/params.yaml`` (INV-11) — the in-memory store SEEDS the four workstreams
from ``params.budget.workstreams`` at construction; the live store reads them from the
0030 ``budget_workstream`` table (also seeded from params at boot, schema-only).

- :class:`BudgetStore` — the ABC every budget route depends on.
- :class:`InMemoryBudgetStore` — the v1 / CI-tested local impl (params-seeded
  workstreams + an in-memory ledger; pure, no I/O).
- :class:`SupabaseBudgetStore` — the live impl over the 0030 ``budget_workstream`` /
  ``budget_entry`` tables, via the SAME PostgREST/service_role pattern as the
  decisions/layouts stores (exercised only against a real DB).

The store is deliberately dumb: it stores and returns what it is handed. The variance
reconcile (``app.core.budget.reconcile``) and the >10% → Decision-Queue WIRING are the
CALLER's (the route) — this store only persists the ledger and lists the rows.

Purity: plain data access — imports no ``app.ai`` / ``app.adapters`` modules, only the
pure :class:`app.core.params.Params` (for the seed), the
:class:`app.core.program.Program` enum, and ``httpx`` (the house transport).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.core.params import Params
from app.core.program import Program
from app.data.supabase_repository import SupabaseError

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a third
# party's URLs, the same carve-out the decisions/layouts stores make). The 0030 table
# names.
_REST = "/rest/v1"
_WORKSTREAM_TABLE = f"{_REST}/budget_workstream"
_ENTRY_TABLE = f"{_REST}/budget_entry"

# The append-only ledger's legal line-item kinds (mirrors the 0030 budget_entry CHECK).
ENTRY_KINDS = ("recommended", "planned", "committed", "actual")


@dataclass(frozen=True)
class Workstream:
    """One budget workstream — a name + its planned allocation (whole US dollars).

    Attributes:
        name: The UNIQUE workstream key (grassroots / content / guerrilla / ops).
        planned_usd: The allocated budget for this workstream (whole US dollars),
            sourced from ``params.budget.workstreams`` (INV-11).
    """

    name: str
    planned_usd: int


@dataclass(frozen=True)
class Entry:
    """One append-only spend/commitment ledger line item (the 0030 budget_entry shape).

    Attributes:
        workstream: The owning workstream key (keys :attr:`Workstream.name`).
        kind: The line-item kind — recommended / planned / committed / actual.
        amount_usd: The line amount (cent-capable :class:`~decimal.Decimal` money, so
            the roll-up never mixes float dollars).
        note: An optional free-form note (PII-free, synthetic — INV-1).
    """

    workstream: str
    kind: str
    amount_usd: Decimal
    note: str | None = None


class BudgetStore(ABC):
    """Read/write seam over the B4 Budget Tracker (migration 0030).

    Every budget route depends on this interface, never a concrete store. v1 binds the
    in-memory impl (params-seeded workstreams); production swaps the Supabase-backed one
    with zero caller changes (the NFR-8 store-seam pattern). Every method is
    program-scoped (the 0030 tenancy tag) so one program's budget never bleeds into
    another's.
    """

    @abstractmethod
    def list_workstreams(self, program: Program) -> list[Workstream]:
        """The budget workstreams for ``program`` — name + planned allocation."""
        raise NotImplementedError

    @abstractmethod
    def list_entries(self, program: Program) -> list[Entry]:
        """The spend/commitment ledger for ``program``, in append order."""
        raise NotImplementedError

    @abstractmethod
    def add_entry(
        self,
        program: Program,
        *,
        workstream: str,
        kind: str,
        amount_usd: Decimal,
        note: str | None = None,
    ) -> Entry:
        """Append one immutable ledger line item and return it (the 0030 append-only ledger).

        This store method does not authorize (the route's admin/leader gate does) and
        does not re-derive variance (the caller runs ``reconcile``).
        """
        raise NotImplementedError


class InMemoryBudgetStore(BudgetStore):
    """In-memory :class:`BudgetStore` — params-seeded workstreams + an in-memory ledger.

    The v1 local store (A-3) and the CI-tested path. The four workstreams are SEEDED
    from ``params.budget.workstreams`` at construction (INV-11 — the allocations' one
    canonical home); the ledger accumulates in a per-program list. No credential, no
    I/O. A production deploy swaps :class:`SupabaseBudgetStore` behind the same seam.
    """

    def __init__(self, *, params: Params) -> None:
        # Seed the workstreams from params (INV-11); dict insertion order is the display
        # order (grassroots / content / guerrilla / ops).
        self._workstreams: list[Workstream] = [
            Workstream(name=name, planned_usd=planned)
            for name, planned in params.budget.workstreams.items()
        ]
        self._names = {w.name for w in self._workstreams}
        # Append-only per-program ledger (mirrors the 0030 budget_entry table).
        self._entries: dict[Program, list[Entry]] = {}

    def list_workstreams(self, program: Program) -> list[Workstream]:
        # The seeded allocation is program-agnostic in v1 (one demo program); returned
        # for whichever program asks so the route can build its reconcile inputs.
        return list(self._workstreams)

    def list_entries(self, program: Program) -> list[Entry]:
        return list(self._entries.get(program, []))

    def add_entry(
        self,
        program: Program,
        *,
        workstream: str,
        kind: str,
        amount_usd: Decimal,
        note: str | None = None,
    ) -> Entry:
        if workstream not in self._names:
            # The 0030 FK (budget_entry.workstream → budget_workstream.name) rejects an
            # unknown workstream; fail loud here so the route maps it to a clean 422.
            raise KeyError(f"unknown workstream (not a seeded budget row): {workstream!r}")
        if kind not in ENTRY_KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}; expected one of {ENTRY_KINDS}")
        entry = Entry(workstream=workstream, kind=kind, amount_usd=amount_usd, note=note)
        self._entries.setdefault(program, []).append(entry)
        return entry


class SupabaseBudgetStore(BudgetStore):
    """Live :class:`BudgetStore` over Supabase PostgREST (service_role; 0030).

    Query-per-request (the stateless-runtime posture of
    :class:`app.data.supabase_repository.SupabaseFamilyRepository`): each call issues a
    fresh PostgREST request over the injected (or per-call) ``httpx`` client. Both tables
    are program-scoped — ``program_id`` is the 0030 tenancy tag — so every read filters
    and every write stamps it. The ``service_role`` key BYPASSES RLS (server-only —
    INV-5 / D-RLS-4) and never leaves the backend.

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

    def _post(self, path: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """One PostgREST POST (return=representation) → the decoded body; fail loud."""
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "return=representation",
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
        body: Any = response.json()
        if not isinstance(body, list):
            raise SupabaseError(f"PostgREST POST {path} returned a non-array body")
        return body

    # ---------------------------------------------------------------- interface
    def list_workstreams(self, program: Program) -> list[Workstream]:
        rows = self._get(
            _WORKSTREAM_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": "name,planned_usd",
                "order": "created_at.asc",
            },
        )
        return [Workstream(name=str(r["name"]), planned_usd=int(r["planned_usd"])) for r in rows]

    def list_entries(self, program: Program) -> list[Entry]:
        rows = self._get(
            _ENTRY_TABLE,
            {
                "program_id": f"eq.{program.value}",
                "select": "workstream,kind,amount_usd,note",
                "order": "created_at.asc",
            },
        )
        return [
            Entry(
                workstream=str(r["workstream"]),
                kind=str(r["kind"]),
                amount_usd=Decimal(str(r["amount_usd"])),
                note=r.get("note"),
            )
            for r in rows
        ]

    def add_entry(
        self,
        program: Program,
        *,
        workstream: str,
        kind: str,
        amount_usd: Decimal,
        note: str | None = None,
    ) -> Entry:
        rows = self._post(
            _ENTRY_TABLE,
            {
                "workstream": workstream,
                "kind": kind,
                "amount_usd": int(amount_usd),
                "note": note,
                "program_id": program.value,
            },
        )
        if not rows:
            raise SupabaseError("PostgREST POST /budget_entry returned no representation row")
        row = rows[0]
        return Entry(
            workstream=str(row["workstream"]),
            kind=str(row["kind"]),
            amount_usd=Decimal(str(row["amount_usd"])),
            note=row.get("note"),
        )


def build_supabase_budget_store() -> SupabaseBudgetStore | None:
    """Construct the Supabase budget store from the env, or ``None`` when unbound.

    Mirrors :func:`app.data.decisions_store.build_supabase_decisions_store`: reads
    ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the environment at
    the composition root, returning ``None`` when either is absent or is a placeholder
    ``<...>`` sentinel — so the caller falls back to the in-memory store (A-3). No
    program is threaded in: the store is constructed program-agnostic and bounded per
    call by the ``program`` argument each method takes.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseBudgetStore(base_url=url, service_role_key=key)

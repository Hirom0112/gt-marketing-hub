"""The brand-memory persistence boundary — interface only (FR-3.2, D-8, INV-9).

Brand memory (CONTENT_SPEC §8.3) is *memory*, not just storage: kept items
persist across sessions AND condition the next generation batch (FR-3.2). The
defining property of this boundary is **server-side persistence** (TECH_STACK
D-8): a kept item SURVIVES store re-instantiation — it is NOT browser
localStorage and has no in-memory-only fallback.

Like every external boundary (INV-9, ARCHITECTURE.md §7), this is an interface
with swappable impls selected in :mod:`app.adapters.registry`:

- :class:`app.adapters.brand_memory.sqlite_store.SqliteBrandMemoryStore` — the
  v1 local impl, backed by stdlib ``sqlite3`` (ASSUMPTIONS A-11, no Postgres in
  this env per A-3, no new dependency).
- a production Postgres-backed impl — table authored in
  `app/data/migrations/0002_brand_memory.sql` with deny-by-default RLS (INV-5).

This is a **clean persistence interface**: it does NOT run evals or call LLMs
(those live in `app/core/eval_gate.py` and the AI edge). It is also
seed-source-agnostic — it exposes :meth:`upsert`; a separate seed generator
produces the items and a later wave calls ``store.upsert(...)``. This module
imports nothing from ``anthropic`` / ``langgraph`` and does not couple to
``app.data.synthetic``.

:class:`app.ai.schemas.brand.BrandMemoryItem` is **frozen** — so :meth:`affirm`
and :meth:`weaken` cannot mutate in place: they build a NEW item via
``model_copy(update=...)`` (bumped weight/version) and persist it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.schemas.brand import BrandMemoryItem, BrandMemorySignal


class BrandMemoryStore(ABC):
    """Persistence boundary for :class:`BrandMemoryItem` (FR-3.2, D-8, INV-9).

    A clean CRUD-plus-signal interface over kept/curated brand memory. It owns
    persistence only — never evals, never LLM calls. The defining invariant
    (D-8): items survive store re-instantiation against the same backing store.
    """

    @abstractmethod
    def upsert(self, item: BrandMemoryItem) -> BrandMemoryItem:
        """Insert or replace ``item`` by its ``id``; return the stored item.

        Idempotent on ``id`` — re-upserting the same id replaces the prior row
        (never duplicates). The write is durable (D-8): a later store instance
        against the same backing store reads it back.
        """

    @abstractmethod
    def get(self, item_id: str) -> BrandMemoryItem | None:
        """Return the item with ``item_id``, or ``None`` if absent."""

    @abstractmethod
    def list_active(self, channel: str | None = None) -> list[BrandMemoryItem]:
        """Return active items (``active=True`` only), optionally channel-scoped.

        With no ``channel`` filter, returns every active item. With a ``channel``
        (a :class:`app.ai.schemas.content.Channel` value), returns active items
        whose ``channel_scope`` is empty (applies to all channels) OR contains
        that channel — the §8.3.2 conditioning set for a given channel.
        """

    @abstractmethod
    def affirm(self, item_id: str, signal: BrandMemorySignal) -> BrandMemoryItem:
        """Affirm (keep) ``item_id``: bump weight + version, persist, return it.

        A keep STRENGTHENS the item. Because :class:`BrandMemoryItem` is frozen,
        this builds a NEW item via ``model_copy`` with a higher weight, an
        incremented ``version``, and ``signal`` recorded, then persists it.

        Raises:
            KeyError: if ``item_id`` is unknown.
        """

    @abstractmethod
    def weaken(self, item_id: str, signal: BrandMemorySignal) -> BrandMemoryItem:
        """Weaken (discard) ``item_id``: lower weight, bump version, persist, return.

        A discard STRENGTHENS a dont/discarded signal by lowering the item's
        weight (it conditions the next batch less). Builds a NEW frozen item via
        ``model_copy`` with a lower weight, an incremented ``version``, and
        ``signal`` recorded, then persists it.

        Raises:
            KeyError: if ``item_id`` is unknown.
        """

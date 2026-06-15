"""SQLite-backed brand-memory store — persistent local impl (FR-3.2, D-8, A-11).

The v1 local impl of the :class:`app.adapters.brand_memory.base.BrandMemoryStore`
boundary. There is no Postgres in this env (ASSUMPTIONS A-3), so per A-11 the
local impl is backed by the Python **stdlib** ``sqlite3`` module — **no new
dependency** — while the production Postgres table is authored in
`app/data/migrations/0002_brand_memory.sql` (deny-by-default RLS, INV-5).

The defining property (TECH_STACK D-8): brand memory is server-side
**persistent**, not browser localStorage. Each :class:`BrandMemoryItem` is
serialized via Pydantic ``model_dump_json`` into a row keyed by ``id``; a
brand-new store instance opened against the SAME ``db_path`` reads the prior
items back from disk. An in-memory ``":memory:"`` path is permitted only for a
single-connection use (its data dies with the connection); the persistence
guarantee requires a real file path.

This module is a clean persistence layer: it runs no evals and calls no LLM. It
imports nothing from ``anthropic`` / ``langgraph`` and does not couple to
``app.data.synthetic``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.adapters.brand_memory.base import BrandMemoryStore
from app.ai.schemas.brand import BrandMemoryItem, BrandMemorySignal

# Per-signal weight delta applied by affirm/weaken (a configuration seam, NOT a
# hardcoded magic in logic — INV-11). It is a constructor default so a caller can
# override it; its canonical params home (`brand_memory.weight_step`) is reserved
# for the wave that owns `params/params.yaml` (a shared contract, CLAUDE.md §7).
_DEFAULT_WEIGHT_STEP = 1.0


class SqliteBrandMemoryStore(BrandMemoryStore):
    """A persistent :class:`BrandMemoryStore` backed by stdlib ``sqlite3`` (A-11).

    Items are stored one-row-per-``id`` as serialized JSON. Re-instantiating
    against the same ``db_path`` returns the prior items (D-8): persistence is
    on disk, never in-memory/localStorage. ``affirm``/``weaken`` build a NEW
    frozen item (``model_copy``) with bumped weight/version and persist it.
    """

    def __init__(self, db_path: str | Path, *, weight_step: float = _DEFAULT_WEIGHT_STEP) -> None:
        # ":memory:" is allowed only for single-connection use (data is not
        # durable); the persistence guarantee (D-8) needs a real file path.
        self._db_path = str(db_path)
        self._weight_step = weight_step
        self._ensure_schema()

    # -- internals ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the backing sqlite file (stdlib only, no I/O deps)."""
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        """Create the brand_memory table if it does not yet exist (idempotent)."""
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS brand_memory ("
                "  id      TEXT PRIMARY KEY,"
                "  active  INTEGER NOT NULL,"
                "  payload TEXT NOT NULL"
                ")"
            )

    # -- BrandMemoryStore interface ----------------------------------------

    def upsert(self, item: BrandMemoryItem) -> BrandMemoryItem:
        """Insert or replace ``item`` by ``id`` (idempotent); persist and return it.

        ``active`` is denormalized into its own column so ``list_active`` filters
        in SQL; the full record is the serialized ``model_dump_json`` payload.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO brand_memory (id, active, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET active=excluded.active, payload=excluded.payload",
                (item.id, 1 if item.active else 0, item.model_dump_json()),
            )
        return item

    def get(self, item_id: str) -> BrandMemoryItem | None:
        """Return the persisted item with ``item_id``, or ``None`` if absent."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM brand_memory WHERE id = ?", (item_id,)
            ).fetchone()
        if row is None:
            return None
        return BrandMemoryItem.model_validate_json(row[0])

    def list_active(self, channel: str | None = None) -> list[BrandMemoryItem]:
        """Return active items, optionally scoped to ``channel`` (§8.3.2).

        Only ``active=True`` rows are returned. With a ``channel`` filter, an
        item is included when its ``channel_scope`` is empty (applies to all
        channels) OR contains that channel.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM brand_memory WHERE active = 1 ORDER BY id"
            ).fetchall()
        items = [BrandMemoryItem.model_validate_json(row[0]) for row in rows]
        if channel is None:
            return items
        return [
            item
            for item in items
            if not item.channel_scope or channel in [c.value for c in item.channel_scope]
        ]

    def affirm(self, item_id: str, signal: BrandMemorySignal) -> BrandMemoryItem:
        """Affirm (keep) ``item_id``: bump weight + version, persist, return it.

        Builds a NEW frozen item (``model_copy``) — the schema is immutable — with
        a higher weight, an incremented ``version``, and ``signal`` recorded.

        Raises:
            KeyError: if ``item_id`` is unknown.
        """
        current = self._require(item_id)
        bumped = current.model_copy(
            update={
                "weight": current.weight + self._weight_step,
                "version": current.version + 1,
                "signal": signal,
            }
        )
        return self.upsert(bumped)

    def weaken(self, item_id: str, signal: BrandMemorySignal) -> BrandMemoryItem:
        """Weaken (discard) ``item_id``: lower weight, bump version, persist, return.

        Builds a NEW frozen item (``model_copy``) with a lower weight, an
        incremented ``version``, and ``signal`` recorded — a discard strengthens
        the discarded/dont signal by reducing the item's conditioning weight.

        Raises:
            KeyError: if ``item_id`` is unknown.
        """
        current = self._require(item_id)
        bumped = current.model_copy(
            update={
                "weight": current.weight - self._weight_step,
                "version": current.version + 1,
                "signal": signal,
            }
        )
        return self.upsert(bumped)

    def _require(self, item_id: str) -> BrandMemoryItem:
        """Return the item or raise ``KeyError`` (affirm/weaken need an existing row)."""
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"brand-memory item {item_id!r} not found")
        return item

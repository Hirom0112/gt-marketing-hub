"""The content library — searchable store of kept + validated assets (FR-3.4; §5).

The library is the durable, reusable, searchable home for curated content. Per
CONTENT_SPEC §5 only **validated** content enters it, and in this engine the only
promotion path is an explicit human `keep` (FR-3.5) — so every stored asset is
both ``lifecycle=kept`` and validated (it carries a passing ``ValidationResult``
id). :func:`search` is the FR-3.4 surface: it returns ONLY kept + validated
assets, filtered over the denormalized ``search_text`` and ``tags`` (the search
index is denormalized on promotion so search is a single pass — no re-derivation).

This is a composition-layer boundary (CLAUDE.md §7), not core: it is an interface
with a swappable impl (INV-9), the same seam pattern as
:class:`app.observability.log_store.ObservabilityLog` and
:class:`app.adapters.brand_memory.base.BrandMemoryStore`. v1 is in-memory
(ASSUMPTIONS A-3); production swaps a Supabase-backed impl behind this interface.
It imports nothing from ``anthropic`` / ``langgraph`` and runs no eval / LLM.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from app.ai.schemas.brand import LibraryAsset
from app.ai.schemas.content import LifecycleStage


class ContentLibrary(ABC):
    """Persistence + search boundary for kept, validated :class:`LibraryAsset`s (FR-3.4).

    The library stores only assets that have been explicitly kept and validated;
    :meth:`search` never returns anything else. The interface is deliberately
    add + query only — there is no delete (a kept asset is durable).
    """

    @abstractmethod
    def add(self, asset: LibraryAsset) -> LibraryAsset:
        """Add ``asset`` to the library (idempotent on ``id``); return it.

        The caller (the keep path) is responsible for only adding kept +
        validated assets; :meth:`search` re-asserts the kept filter defensively.
        """

    @abstractmethod
    def get(self, asset_id: str) -> LibraryAsset | None:
        """Return the asset with ``asset_id``, or ``None`` if absent."""

    @abstractmethod
    def search(
        self, *, search_text: str | None = None, tags: list[str] | None = None
    ) -> list[LibraryAsset]:
        """Return kept + validated assets matching ``search_text`` / ``tags`` (FR-3.4).

        Only ``lifecycle=kept`` assets with a non-empty ``validation`` are
        candidates. ``search_text`` is a case-insensitive substring match against
        the denormalized ``search_text`` index; ``tags`` requires every supplied
        tag to be present on the asset. With neither filter, returns the whole
        kept+validated set in insertion order.
        """

    @abstractmethod
    def list_drafts(self, source_ref: str) -> list[LibraryAsset]:
        """Return the DRAFT assets carrying ``source_ref`` (insertion order).

        The surgical companion to :meth:`search` for cross-module stubs (e.g. the
        Module-2 grassroots testimonial drafts, ``source_ref='grassroots_testimonial'``):
        :meth:`search` deliberately hides drafts (it returns only kept+validated
        assets), so the Content surface needs this narrow read to surface the
        recently-captured stubs for the team to pick up. Returns ONLY
        ``lifecycle=draft`` assets whose ``source_ref`` matches exactly.
        """


class SqliteContentLibrary(ContentLibrary):
    """Persistent :class:`ContentLibrary` backed by stdlib ``sqlite3`` (D-8, A-11).

    The persistent v1 local impl, mirroring
    :class:`app.adapters.brand_memory.sqlite_store.SqliteBrandMemoryStore`: there
    is no Postgres in this env (ASSUMPTIONS A-3), so the local impl uses the Python
    **stdlib** ``sqlite3`` module — **no new dependency**. The production Postgres
    table (deny-by-default RLS, INV-5) is the prod swap.

    The defining property (D-8): a kept asset is server-side **persistent**, not
    browser localStorage. Each :class:`LibraryAsset` is serialized via Pydantic
    ``model_dump_json`` into a row keyed by ``id``; a brand-new store opened against
    the SAME ``db_path`` reads the prior assets back from disk. ``lifecycle`` is
    denormalized into its own column so :meth:`search` can re-assert the kept filter
    in SQL. Runs no evals and calls no LLM (imports nothing from ``anthropic`` /
    ``langgraph``).
    """

    def __init__(self, db_path: str | Path) -> None:
        # ":memory:" is allowed only for single-connection use (data is not
        # durable); the persistence guarantee (D-8) needs a real file path.
        self._db_path = str(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the backing sqlite file with the schema asserted.

        Stdlib only (no I/O deps). The idempotent ``CREATE TABLE IF NOT EXISTS`` is
        re-run on EVERY connection (belt-and-suspenders, cheap): if the backing file
        was unlinked out from under this instance — e.g. a test's
        ``reset_content_library(fresh=True)`` unlinking a shared path — sqlite would
        otherwise open a brand-new EMPTY file and the next query would raise
        "no such table: content_library". Re-asserting the schema here lets such a
        reused file self-heal instead of throwing, without weakening the D-8
        persistence contract: the table is only created when absent; existing rows
        are never touched, so a kept asset still survives a restart.
        """
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS content_library ("
            "  id        TEXT PRIMARY KEY,"
            "  seq       INTEGER,"
            "  lifecycle TEXT NOT NULL,"
            "  payload   TEXT NOT NULL"
            ")"
        )
        return conn

    def _ensure_schema(self) -> None:
        """Assert the content_library schema exists (idempotent).

        ``seq`` preserves insertion order (the in-memory impl's contract) so search
        returns assets in the order they were added, deterministically. The schema
        is now asserted by :meth:`_connect` on every connection (self-healing); this
        remains as the explicit ``__init__`` call site for clarity.
        """
        with self._connect():
            pass

    def add(self, asset: LibraryAsset) -> LibraryAsset:
        """Insert or replace ``asset`` by ``id`` (idempotent); persist and return it.

        On a first insert the asset takes the next sequence number (insertion
        order); a replace keeps the existing ``seq`` so re-adding does not reorder.
        """
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT seq FROM content_library WHERE id = ?", (asset.id,)
            ).fetchone()
            if existing is not None:
                seq = existing[0]
            else:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM content_library"
                ).fetchone()
                seq = row[0]
            conn.execute(
                "INSERT INTO content_library (id, seq, lifecycle, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "lifecycle=excluded.lifecycle, payload=excluded.payload",
                (asset.id, seq, asset.lifecycle.value, asset.model_dump_json()),
            )
        return asset

    def get(self, asset_id: str) -> LibraryAsset | None:
        """Return the persisted asset with ``asset_id``, or ``None`` if absent."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM content_library WHERE id = ?", (asset_id,)
            ).fetchone()
        if row is None:
            return None
        return LibraryAsset.model_validate_json(row[0])

    def search(
        self, *, search_text: str | None = None, tags: list[str] | None = None
    ) -> list[LibraryAsset]:
        """Return kept + validated assets matching ``search_text`` / ``tags`` (FR-3.4).

        The kept filter is pushed to SQL (``lifecycle='kept'``); the text/tag
        filters re-use the in-memory impl's semantics over the deserialized asset.
        Results keep insertion order via the ``seq`` column.
        """
        text = search_text.lower().strip() if search_text else None
        wanted_tags = {t.lower() for t in tags} if tags else set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM content_library WHERE lifecycle = ? ORDER BY seq",
                (LifecycleStage.KEPT.value,),
            ).fetchall()
        results: list[LibraryAsset] = []
        for (payload,) in rows:
            asset = LibraryAsset.model_validate_json(payload)
            # Defensive re-assert: only kept + validated assets surface (FR-3.4 / §5).
            if asset.lifecycle is not LifecycleStage.KEPT or not asset.validation:
                continue
            if text is not None and text not in asset.search_text.lower():
                continue
            if wanted_tags and not wanted_tags.issubset({t.lower() for t in asset.tags}):
                continue
            results.append(asset)
        return results

    def list_drafts(self, source_ref: str) -> list[LibraryAsset]:
        """Return DRAFT assets carrying ``source_ref`` (insertion order via ``seq``).

        Pushes the draft filter to SQL (``lifecycle='draft'``) then matches
        ``source_ref`` over the deserialized asset. Does NOT touch :meth:`search`'s
        kept-only contract — a draft never surfaces in search (FR-3.4 / §5).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM content_library WHERE lifecycle = ? ORDER BY seq",
                (LifecycleStage.DRAFT.value,),
            ).fetchall()
        results: list[LibraryAsset] = []
        for (payload,) in rows:
            asset = LibraryAsset.model_validate_json(payload)
            if asset.lifecycle is LifecycleStage.DRAFT and asset.source_ref == source_ref:
                results.append(asset)
        return results

    @classmethod
    def seeded(cls, db_path: str | Path) -> SqliteContentLibrary:
        """Hydrate a persistent library, preferring distilled real assets (Phase-1).

        Prefers IMPORT-provenance assets distilled from GT's OWN public marketing
        (``app.data.library_ingest.load_library_assets``), falling back to the
        §11.4 synthetic inventory when the committed seed JSON is absent, but
        persisted to ``db_path`` so the seed survives a restart. ``add`` is
        idempotent on ``id``, so re-seeding an existing file is a no-op in shape.
        """
        from app.data.library_ingest import load_library_assets
        from app.data.synthetic import generate_library_assets

        library = cls(db_path)
        imported = load_library_assets()
        for asset in imported if imported else generate_library_assets():
            library.add(asset)
        return library

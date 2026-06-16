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

from abc import ABC, abstractmethod

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


class InMemoryContentLibrary(ContentLibrary):
    """In-memory :class:`ContentLibrary` (v1; ASSUMPTIONS A-3).

    Storage is an insertion-ordered dict keyed by asset id. Production swaps a
    Supabase-backed impl behind the same interface with zero caller changes.
    """

    def __init__(self) -> None:
        self._assets: dict[str, LibraryAsset] = {}

    def add(self, asset: LibraryAsset) -> LibraryAsset:
        self._assets[asset.id] = asset
        return asset

    def get(self, asset_id: str) -> LibraryAsset | None:
        return self._assets.get(asset_id)

    def search(
        self, *, search_text: str | None = None, tags: list[str] | None = None
    ) -> list[LibraryAsset]:
        text = search_text.lower().strip() if search_text else None
        wanted_tags = {t.lower() for t in tags} if tags else set()
        results: list[LibraryAsset] = []
        for asset in self._assets.values():
            # Only kept + validated assets are ever surfaced (FR-3.4 / §5).
            if asset.lifecycle is not LifecycleStage.KEPT or not asset.validation:
                continue
            if text is not None and text not in asset.search_text.lower():
                continue
            if wanted_tags and not wanted_tags.issubset({t.lower() for t in asset.tags}):
                continue
            results.append(asset)
        return results

    @classmethod
    def seeded(cls) -> InMemoryContentLibrary:
        """Hydrate the library, preferring distilled real assets (Phase-1 marketing).

        Prefers the IMPORT-provenance assets distilled from GT's OWN public
        marketing (`app.data.library_ingest.load_library_assets`) — each is
        gate-routed at load, so it carries a real passing `ValidationResult` id
        and `lifecycle=kept`, surfacing in search immediately. Falls back to the
        §11.4 synthetic seed inventory when the committed seed JSON is absent
        (default dev / fresh checkout), keeping existing tests green and the
        store always non-empty (NFR-1). Imported lazily to keep the import graph
        thin.
        """
        from app.data.library_ingest import load_library_assets
        from app.data.synthetic import generate_library_assets

        library = cls()
        imported = load_library_assets()
        for asset in imported if imported else generate_library_assets():
            library.add(asset)
        return library

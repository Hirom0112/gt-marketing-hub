"""Test-only in-memory :class:`ContentLibrary` double.

The wired production impl is :class:`app.marketing.library.SqliteContentLibrary`
(see ``app/api/deps.py``). The former ``InMemoryContentLibrary`` was a zombie in
the production module (zero non-test references) and was removed in the
2026-06-18 dead-code audit. Its in-memory ``add`` / ``get`` / ``search`` shape is
still useful as a lightweight test double for tests that exercise OTHER live
behavior (gallery endpoints, keep-promotion, library search semantics) without
standing up a sqlite file — so it lives here, in test support, not in app code.

``search`` mirrors the production :class:`SqliteContentLibrary.search` semantics
exactly: only ``lifecycle=KEPT`` + validated assets surface, case-insensitive
substring match over the denormalized ``search_text``, and an all-tags subset
match, in insertion order.
"""

from __future__ import annotations

from app.ai.schemas.brand import LibraryAsset
from app.ai.schemas.content import LifecycleStage
from app.marketing.library import ContentLibrary


class InMemoryContentLibrary(ContentLibrary):
    """In-memory :class:`ContentLibrary` test double (insertion-ordered dict)."""

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
        """Hydrate from the same seed contract as the production library.

        Prefers IMPORT-provenance distilled assets, falling back to the §11.4
        synthetic seed inventory when the committed seed JSON is absent.
        """
        from app.data.library_ingest import load_library_assets
        from app.data.synthetic import generate_library_assets

        library = cls()
        imported = load_library_assets()
        for asset in imported if imported else generate_library_assets():
            library.add(asset)
        return library

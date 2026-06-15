"""Library search returns only kept + validated assets (FR-3.4).

The §5 content library is the durable, searchable store of curated, validated
content. `search` returns ONLY `kept` + validated `LibraryAsset`s, filtered over
the denormalized `search_text` and `tags`. A promoted asset denormalizes its
`search_text` so search is a single index pass.

Drives `app/marketing/library.py` (`InMemoryContentLibrary`) and, lightly, the
`GET/POST /content/library` route shape.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import Channel, ContentFormat, GeneratedBy, LifecycleStage, Provenance
from app.main import app
from app.marketing.library import InMemoryContentLibrary

client = TestClient(app)


def _asset(
    *,
    asset_id: str,
    title: str,
    search_text: str,
    tags: list[str],
    lifecycle: LifecycleStage = LifecycleStage.KEPT,
) -> LibraryAsset:
    return LibraryAsset(
        id=asset_id,
        title=title,
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        body="Body text.",
        tags=tags,
        search_text=search_text,
        validation="vr-pass-1",
        lifecycle=lifecycle,
        provenance=Provenance(
            generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-01-01T00:00:00+00:00"
        ),
    )


def test_search_filters_by_text_and_tags() -> None:
    """search returns only kept+validated assets matching the query text/tags."""
    library = InMemoryContentLibrary()
    library.add(
        _asset(
            asset_id="lib-mastery",
            title="Mastery caption",
            search_text="mastery-based gifted k-8 caption",
            tags=["mastery", "k8"],
        )
    )
    library.add(
        _asset(
            asset_id="lib-funding",
            title="Funding FAQ",
            search_text="tefa funding installments tuition",
            tags=["funding", "tefa"],
        )
    )

    # Full library (no query) returns both.
    assert {a.id for a in library.search()} == {"lib-mastery", "lib-funding"}

    # Text filter narrows to the matching asset.
    assert {a.id for a in library.search(search_text="mastery")} == {"lib-mastery"}
    # Tag filter narrows by tag.
    assert {a.id for a in library.search(tags=["tefa"])} == {"lib-funding"}
    # A miss returns empty.
    assert library.search(search_text="nonexistent-token") == []


def test_search_excludes_non_kept_assets() -> None:
    """An asset that is not `kept` is never returned by search (only validated+kept)."""
    library = InMemoryContentLibrary()
    library.add(
        _asset(
            asset_id="lib-draft",
            title="Draft only",
            search_text="draft caption text",
            tags=["draft"],
            lifecycle=LifecycleStage.DRAFT,
        )
    )
    assert library.search() == []
    assert library.search(search_text="draft") == []


# --------------------------------------------------------------------------- #
# API surface — GET /content/library searches the seeded library.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_library_route_returns_kept_assets() -> None:
    """`GET /content/library` returns the kept+validated seeded assets (FR-3.4)."""
    resp = client.get("/content/library")
    assert resp.status_code == 200
    assets = resp.json()
    assert len(assets) >= 4  # the §11.4 seed inventory (all kept + validated)
    assert all(a["lifecycle"] == "kept" for a in assets)
    assert all(a["validation"] for a in assets)

    # A search query narrows the result set over search_text.
    funding = client.get("/content/library", params={"q": "tefa"})
    assert funding.status_code == 200
    assert all("tefa" in a["search_text"] or "funding" in a["search_text"] for a in funding.json())

"""`GET /content/gallery` — the posted-content gallery surface (FR-3.4; INV-1/INV-11).

Acceptance tests for the read-only posted-content gallery endpoint: the kept+validated
library assets that carry a social platform tag (GT's own proven posts — synthetic, INV-1)
surfaced as posts grouped by the platform they came FROM, with a sort toggle
(most_valuable / most_recent) and an optional platform filter (the "click into Facebook"
drill). The math lives in the pure `app.marketing.posted_gallery` core (pinned separately);
these prove it is wired behind HTTP faithfully and degrades cleanly (never a 500).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import (
    Channel,
    ContentFormat,
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.api.deps import get_content_library_dep
from app.main import app
from tests.support.content_library import InMemoryContentLibrary

client = TestClient(app)


def _asset(asset_id: str, platform: str, body: str = "A proven caption.") -> LibraryAsset:
    return LibraryAsset(
        id=asset_id,
        title=f"{platform} caption",
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        body=body,
        source_ref=f"https://example.test/{asset_id}",
        tags=["gifted_identity", platform, "social", "proven"],
        search_text=f"{platform} {body}".lower(),
        validation="vr-import-pass",
        lifecycle=LifecycleStage.KEPT,
        provenance=Provenance(
            generated_by=GeneratedBy.IMPORT, created_at="2026-06-15T00:00:00+00:00"
        ),
    )


def _library_with(*assets: LibraryAsset) -> InMemoryContentLibrary:
    lib = InMemoryContentLibrary()
    for asset in assets:
        lib.add(asset)
    return lib


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_gallery_groups_by_platform_with_counts() -> None:
    """No platform filter ⇒ platform tiles with per-platform counts (FR-3.4)."""
    lib = _library_with(
        _asset("fb1", "facebook"),
        _asset("fb2", "facebook"),
        _asset("ig1", "instagram"),
    )
    app.dependency_overrides[get_content_library_dep] = lambda: lib

    resp = client.get("/content/gallery")
    assert resp.status_code == 200
    body = resp.json()
    groups = {g["platform"]: g["count"] for g in body["groups"]}
    assert groups == {"facebook": 2, "instagram": 1}
    assert body["posts"] == []


def test_gallery_platform_filter_drills_into_one_platform() -> None:
    """`?platform=facebook` is the click-in drill — only Facebook posts, no groups."""
    lib = _library_with(_asset("fb1", "facebook"), _asset("ig1", "instagram"))
    app.dependency_overrides[get_content_library_dep] = lambda: lib

    resp = client.get("/content/gallery", params={"platform": "facebook"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"] == []
    assert body["posts"]
    assert all(p["platform"] == "facebook" for p in body["posts"])
    # Each post carries the words, an image placeholder, a date, and a value.
    post = body["posts"][0]
    assert post["caption"]
    assert post["image_ref"]
    assert post["posted_at"]
    assert "value" in post


def test_gallery_most_valuable_sort() -> None:
    """`sort=most_valuable` orders posts by value descending."""
    lib = _library_with(*[_asset(f"id{i}", "facebook") for i in range(6)])
    app.dependency_overrides[get_content_library_dep] = lambda: lib

    resp = client.get("/content/gallery", params={"platform": "facebook", "sort": "most_valuable"})
    assert resp.status_code == 200
    values = [p["value"] for p in resp.json()["posts"]]
    assert values == sorted(values, reverse=True)


def test_gallery_most_recent_sort() -> None:
    """`sort=most_recent` orders posts by posted_at descending (newest first)."""
    lib = _library_with(*[_asset(f"id{i}", "facebook") for i in range(6)])
    app.dependency_overrides[get_content_library_dep] = lambda: lib

    resp = client.get("/content/gallery", params={"platform": "facebook", "sort": "most_recent"})
    assert resp.status_code == 200
    dates = [p["posted_at"] for p in resp.json()["posts"]]
    assert dates == sorted(dates, reverse=True)


def test_gallery_empty_library_degrades_cleanly() -> None:
    """An empty library returns empty groups/posts, never a 500."""
    app.dependency_overrides[get_content_library_dep] = lambda: InMemoryContentLibrary()
    resp = client.get("/content/gallery")
    assert resp.status_code == 200
    assert resp.json() == {"groups": [], "posts": []}


def test_gallery_seeded_library_has_multiple_platforms() -> None:
    """Against the real seeded library the gallery spans several platforms (demo-ready)."""
    resp = client.get("/content/gallery")
    assert resp.status_code == 200
    groups = resp.json()["groups"]
    # The distilled GT catalog spans facebook/instagram/x-twitter/youtube/tiktok →
    # collapsed to the platform tags; expect a believable multi-platform gallery.
    assert len(groups) >= 2
    assert all(g["count"] >= 1 for g in groups)

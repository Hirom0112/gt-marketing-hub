"""`GET /content/gallery` over the REAL posted catalog (FR-3.4; INV-1 exception).

When ``GT_POSTED_CATALOG_ROOT`` is set AND the catalog exists, the gallery sources from the
REAL posted catalog (real captions, real media refs, engagement-based ranking — the scoped
INV-1 exception, ASSUMPTIONS); otherwise it falls back UNCHANGED to the library gallery.
These run against the SYNTHETIC fixture catalog (nothing real); they prove the catalog path
groups/ranks/filters and exposes the real-shaped fields, and that the no-env boot still
returns the library gallery.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_content_library_dep, get_settings_dep
from app.core.settings import Settings
from app.main import app
from app.marketing.library import InMemoryContentLibrary

client = TestClient(app)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "unit" / "marketing" / "fixtures"


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _use_catalog() -> None:
    app.dependency_overrides[get_settings_dep] = lambda: Settings(posted_catalog_root=FIXTURE_ROOT)


def test_catalog_groups_by_platform_with_counts() -> None:
    """With the catalog configured, the landing groups real posts by platform."""
    _use_catalog()
    body = client.get("/content/gallery").json()
    groups = {g["platform"]: g["count"] for g in body["groups"]}
    # The fixture spans instagram(2), facebook(1), x/twitter(2), youtube(1), tiktok(1).
    assert groups == {
        "instagram": 2,
        "facebook": 1,
        "x/twitter": 2,
        "youtube": 1,
        "tiktok": 1,
    }
    assert body["posts"] == []


def test_catalog_drill_exposes_real_shaped_fields() -> None:
    """Drilling a platform returns posts carrying engagement + media_ref + url + caption."""
    _use_catalog()
    body = client.get("/content/gallery", params={"platform": "instagram"}).json()
    assert body["groups"] == []
    assert all(p["platform"] == "instagram" for p in body["posts"])
    post = next(p for p in body["posts"] if p["id"].endswith("alpha") or "alpha" in p["url"])
    assert post["caption"]
    assert post["image_ref"].startswith("/posted-media/")
    assert post["url"].startswith("https://example.invalid/")
    assert post["likes"] == 100
    assert post["views"] == 1000
    assert post["comments"] == 5
    assert post["value"] == 215.0  # 100*1.0 + 1000*0.1 + 5*3.0
    assert post["asset_type"] in {"image", "video"}


def test_catalog_most_valuable_sort_orders_by_engagement_desc() -> None:
    """sort=most_valuable orders the drilled grid by the engagement value desc."""
    _use_catalog()
    body = client.get(
        "/content/gallery", params={"platform": "x/twitter", "sort": "most_valuable"}
    ).json()
    values = [p["value"] for p in body["posts"]]
    assert values == sorted(values, reverse=True)


def test_catalog_most_recent_sort_orders_by_date_desc() -> None:
    """sort=most_recent orders the drilled grid by the real posted_at desc."""
    _use_catalog()
    body = client.get(
        "/content/gallery", params={"platform": "x/twitter", "sort": "most_recent"}
    ).json()
    dates = [p["posted_at"] for p in body["posts"]]
    assert dates == sorted(dates, reverse=True)


def test_no_env_falls_back_to_library_gallery() -> None:
    """No catalog root ⇒ the existing library gallery (unchanged behaviour)."""
    app.dependency_overrides[get_settings_dep] = lambda: Settings(posted_catalog_root=None)
    app.dependency_overrides[get_content_library_dep] = lambda: InMemoryContentLibrary()
    resp = client.get("/content/gallery")
    assert resp.status_code == 200
    # Empty library + no catalog ⇒ the library gallery's clean-degrade shape.
    assert resp.json() == {"groups": [], "posts": []}

"""Posted-content gallery core tests (FR-3.4; INV-1/INV-11; CLAUDE §4.1).

The marketing library becomes a posted-content gallery: every kept+validated asset
that carries a social platform tag (GT's own proven captions, distilled from the
real public posts — synthetic-shaped, INV-1) is a "post" grouped by the platform it
came FROM, each carrying its caption (the WORDS), an image placeholder ref (media-gen
isn't wired yet), a deterministic synthetic posted_at, and a deterministic synthetic
value (the "most valuable" sort key — no real engagement feed exists yet).

These pin the PURE core: grouping by platform, both sorts (most_valuable / most_recent),
the optional platform filter, and that value + posted_at are DETERMINISTIC and read
their band/window from params (a drifted param moves the result and the test fails).
"""

from __future__ import annotations

from pathlib import Path

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import (
    Channel,
    ContentFormat,
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.core.params import load_params
from app.marketing.posted_gallery import (
    build_gallery,
    gallery_value,
    posted_at,
    posted_platform,
)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[4] / "params" / "params.example.yaml"


def _params():  # type: ignore[no-untyped-def]
    return load_params(EXAMPLE_PARAMS)


def _asset(asset_id: str, platform: str, body: str = "A proven caption.") -> LibraryAsset:
    """A kept+validated social COPY asset tagged with its origin platform."""
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
        validation="vr-import-pass-x",
        lifecycle=LifecycleStage.KEPT,
        provenance=Provenance(
            generated_by=GeneratedBy.IMPORT, created_at="2026-06-15T00:00:00+00:00"
        ),
    )


def test_posted_platform_reads_the_origin_tag() -> None:
    """The post's platform ("WHERE") is the social tag, not the collapsed channel."""
    assert posted_platform(_asset("a", "facebook")) == "facebook"
    assert posted_platform(_asset("b", "x/twitter")) == "x/twitter"
    # A non-social asset (no platform tag) is not a gallery post.
    non_social = _asset("c", "facebook").model_copy(update={"tags": ["website", "owned"]})
    assert posted_platform(non_social) is None


def test_value_is_deterministic_and_within_band() -> None:
    """Value is a stable hash of the id mapped into [value_min, value_max] (INV-11)."""
    params = _params()
    v1 = gallery_value("lib-import-abc", params)
    v2 = gallery_value("lib-import-abc", params)
    assert v1 == v2  # deterministic
    assert params.posted_gallery.value_min <= v1 <= params.posted_gallery.value_max
    # Different ids land at different points in the band (spread, not constant).
    assert gallery_value("lib-import-abc", params) != gallery_value("lib-import-xyz", params)


def test_value_band_reads_from_params() -> None:
    """A drifted band moves the value — the value is not a code literal (INV-11)."""
    params = _params()
    wide = params.model_copy(
        update={
            "posted_gallery": params.posted_gallery.model_copy(
                update={"value_min": 1000.0, "value_max": 2000.0}
            )
        }
    )
    v = gallery_value("lib-import-abc", wide)
    assert 1000.0 <= v <= 2000.0


def test_posted_at_is_deterministic_and_before_the_epoch() -> None:
    """posted_at is a stable, synthetic backdate within the params window (INV-11)."""
    params = _params()
    p1 = posted_at("lib-import-abc", params)
    p2 = posted_at("lib-import-abc", params)
    assert p1 == p2  # deterministic
    # Backdated before the fixed import epoch (2026-06-15), within the window.
    assert p1 < "2026-06-15"
    assert p1 >= "2025-06-15"  # posted_within_days=365 window floor


def test_build_gallery_groups_by_platform() -> None:
    """With no platform filter, the gallery is grouped by origin platform."""
    params = _params()
    assets = [
        _asset("fb1", "facebook"),
        _asset("fb2", "facebook"),
        _asset("ig1", "instagram"),
        _asset("non", "facebook").model_copy(update={"tags": ["website", "owned"]}),
    ]
    result = build_gallery(assets, params=params)
    groups = {g.platform: g for g in result.groups}
    assert set(groups) == {"facebook", "instagram"}
    assert groups["facebook"].count == 2
    assert groups["instagram"].count == 1
    # The non-social asset is excluded — only posted social content is a "post".
    assert sum(g.count for g in result.groups) == 3


def test_build_gallery_most_valuable_sort_orders_by_value_desc() -> None:
    """sort=most_valuable orders posts by synthetic value descending (stable)."""
    params = _params()
    assets = [_asset(f"id{i}", "facebook") for i in range(6)]
    result = build_gallery(assets, params=params, platform="facebook", sort="most_valuable")
    values = [p.value for p in result.posts]
    assert values == sorted(values, reverse=True)


def test_build_gallery_most_recent_sort_orders_by_posted_at_desc() -> None:
    """sort=most_recent orders posts by posted_at descending (newest first)."""
    params = _params()
    assets = [_asset(f"id{i}", "facebook") for i in range(6)]
    result = build_gallery(assets, params=params, platform="facebook", sort="most_recent")
    dates = [p.posted_at for p in result.posts]
    assert dates == sorted(dates, reverse=True)


def test_build_gallery_platform_filter_returns_only_that_platform() -> None:
    """A platform filter is the "click into Facebook" drill — only its posts."""
    params = _params()
    assets = [_asset("fb1", "facebook"), _asset("ig1", "instagram")]
    result = build_gallery(assets, params=params, platform="facebook")
    assert result.posts
    assert all(p.platform == "facebook" for p in result.posts)
    # No groups when a single platform is drilled into.
    assert result.groups == []


def test_post_item_carries_caption_image_ref_and_value() -> None:
    """Each post item exposes the words, an image placeholder, platform + value."""
    params = _params()
    result = build_gallery(
        [_asset("fb1", "facebook", body="Gifted kids deserve their pace.")],
        params=params,
        platform="facebook",
    )
    post = result.posts[0]
    assert post.platform == "facebook"
    assert post.caption == "Gifted kids deserve their pace."
    assert post.asset_type == "copy"
    assert post.image_ref  # a non-empty placeholder ref (media-gen not wired yet)
    assert post.value >= params.posted_gallery.value_min
    assert post.posted_at


def test_build_gallery_empty_input_degrades_cleanly() -> None:
    """No assets ⇒ empty groups/posts, never an error (degrade cleanly)."""
    params = _params()
    result = build_gallery([], params=params)
    assert result.groups == []
    assert result.posts == []

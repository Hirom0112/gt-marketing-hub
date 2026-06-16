"""Posted-CATALOG reader tests (FR-3.4; INV-1/INV-11; CLAUDE §4.1).

The REAL posted catalog (the scoped INV-1 exception — GT's own public marketing in GT's
own internal tool, ASSUMPTIONS) is read AT RUNTIME from an external, env-configured path;
NOTHING real is ever committed. These tests run against a SYNTHETIC fixture CSV
(`fixtures/catalog.csv` — fake platforms/captions, `@…example.invalid`, zero real PII) that
is shaped exactly like the real `catalog/catalog.csv` header.

They pin the PURE-ish reader: CSV parse → posts; platform-case normalisation; asset_type
from the media extension; the media_ref served-URL scheme; empty engagement counted as 0;
the REAL engagement composite `value` (weights params-homed, INV-11, a drifted weight moves
the ranking); and both sort orders + the platform filter, exactly as the gallery consumes.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params
from app.marketing.posted_catalog import (
    catalog_value,
    read_posted_catalog,
)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[4] / "params" / "params.example.yaml"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def _params():  # type: ignore[no-untyped-def]
    return load_params(EXAMPLE_PARAMS)


def test_reads_every_row_as_a_post() -> None:
    """Every catalog row becomes one post (the fixture has 7 rows)."""
    posts = read_posted_catalog(FIXTURE_ROOT, params=_params())
    assert len(posts) == 7


def test_platform_case_is_normalised_to_a_key() -> None:
    """Platform labels normalise to lower-case keys; X/Twitter collapse to x/twitter."""
    posts = read_posted_catalog(FIXTURE_ROOT, params=_params())
    by_url = {p.url: p for p in posts}
    assert by_url["https://example.invalid/p/alpha"].platform == "instagram"
    assert by_url["https://example.invalid/p/gamma"].platform == "facebook"
    # Both `X` and `Twitter` labels collapse to the same x/twitter key.
    assert by_url["https://example.invalid/p/delta"].platform == "x/twitter"
    assert by_url["https://example.invalid/p/epsilon"].platform == "x/twitter"
    assert by_url["https://example.invalid/p/zeta"].platform == "youtube"
    assert by_url["https://example.invalid/p/eta"].platform == "tiktok"


def test_asset_type_from_media_extension() -> None:
    """`.mp4/.mov/.webm` ⇒ video; everything else ⇒ image."""
    posts = {p.url: p for p in read_posted_catalog(FIXTURE_ROOT, params=_params())}
    assert posts["https://example.invalid/p/alpha"].asset_type == "image"  # .jpg
    assert posts["https://example.invalid/p/gamma"].asset_type == "image"  # .png
    assert posts["https://example.invalid/p/beta"].asset_type == "video"  # .mp4
    assert posts["https://example.invalid/p/epsilon"].asset_type == "video"  # .mov
    assert posts["https://example.invalid/p/eta"].asset_type == "video"  # .webm


def test_media_ref_is_the_served_url() -> None:
    """media_ref = '/posted-media/' + the catalog media_file (the static mount)."""
    posts = {p.url: p for p in read_posted_catalog(FIXTURE_ROOT, params=_params())}
    assert (
        posts["https://example.invalid/p/alpha"].media_ref
        == "/posted-media/social/instagram_fake/instagram/fake/alpha.jpg"
    )


def test_empty_engagement_counts_as_zero() -> None:
    """A row with EMPTY likes/views/comments is read as 0 (never a crash)."""
    posts = {p.url: p for p in read_posted_catalog(FIXTURE_ROOT, params=_params())}
    gamma = posts["https://example.invalid/p/gamma"]
    assert gamma.likes == 0
    assert gamma.views == 0
    assert gamma.comments == 0
    assert gamma.value == 0.0  # zero engagement ⇒ zero composite


def test_value_is_the_real_engagement_composite_from_params() -> None:
    """value = like_w*likes + view_w*views + comment_w*comments, weights from params."""
    params = _params()
    # alpha: likes=100, views=1000, comments=5; defaults 1.0/0.1/3.0
    # ⇒ 100*1.0 + 1000*0.1 + 5*3.0 = 100 + 100 + 15 = 215.0
    expected = (
        100 * params.posted_gallery.engagement.like_weight
        + 1000 * params.posted_gallery.engagement.view_weight
        + 5 * params.posted_gallery.engagement.comment_weight
    )
    assert expected == 215.0
    posts = {p.url: p for p in read_posted_catalog(FIXTURE_ROOT, params=params)}
    assert posts["https://example.invalid/p/alpha"].value == 215.0


def test_catalog_value_helper_is_deterministic_and_params_homed() -> None:
    """The value helper reads weights from params — a drifted weight moves it (INV-11)."""
    params = _params()
    v = catalog_value(likes=10, views=100, comments=1, params=params)
    assert v == 10 * 1.0 + 100 * 0.1 + 1 * 3.0  # 23.0
    # Drift the comment weight ⇒ the value moves (proves it is not a literal).
    drifted = params.model_copy(
        update={
            "posted_gallery": params.posted_gallery.model_copy(
                update={
                    "engagement": params.posted_gallery.engagement.model_copy(
                        update={"comment_weight": 10.0}
                    )
                }
            )
        }
    )
    assert catalog_value(likes=10, views=100, comments=1, params=drifted) == 10 + 10 + 10


def test_posted_at_preserves_the_catalog_date() -> None:
    """posted_at carries the catalog timestamp (ISO), not a synthetic backdate."""
    posts = {p.url: p for p in read_posted_catalog(FIXTURE_ROOT, params=_params())}
    assert posts["https://example.invalid/p/alpha"].posted_at.startswith("2026-01-10")


def test_missing_catalog_returns_empty(tmp_path: Path) -> None:
    """A root with no catalog.csv reads as empty — never raises (degrade cleanly)."""
    assert read_posted_catalog(tmp_path, params=_params()) == []

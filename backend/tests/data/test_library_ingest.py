"""Tests for the distilled scraper-library runtime loader (Phase-1 marketing).

The loader (`app.data.library_ingest`) reads the COMMITTED
`app/data/seeds/brand_library.json` (produced offline by
`scripts/distill_library.py` from GT's OWN public marketing) and maps it to the
locked brand-memory / GEO / library schemas. These tests lock:

  * determinism (same JSON → byte-stable lists),
  * V-2 filtering at distill time (a banned-claim caption is excluded),
  * weight ordering normalized WITHIN platform,
  * the $10,400 → $10,474 reconciliation (no $10,400 survives),
  * provenance is IMPORT with a source_ref set,
  * GEO competitor_set stays LOCKED and claims_text empty,
  * params drive the tunables (INV-11 — no magic numbers).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.ai.schemas.brand import BrandMemoryKind, BrandMemorySignal
from app.ai.schemas.content import GeneratedBy
from app.core.eval_gate import RuleVerdict, check_v2
from app.core.params import load_params
from app.data.library_ingest import (
    SEED_PATH,
    load_brand_memory_exemplars,
    load_geo_content_pieces,
    load_library_assets,
)
from app.marketing.geo import GIFTED_SCHOOL_COMPETITOR_SET, validate_competitor_set

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params():
    return load_params(EXAMPLE_PARAMS)


def _seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The committed seed exists and is clean.
# --------------------------------------------------------------------------- #
def test_committed_seed_exists() -> None:
    """The distilled JSON is committed to the working tree (distill ran)."""
    assert SEED_PATH.exists(), "run scripts/distill_library.py to produce the seed"
    seed = _seed()
    assert seed["exemplars"], "expected distilled exemplars"
    assert seed["website_pages"], "expected distilled website pages"


def test_no_dollar_10400_survives() -> None:
    """$10,400 (or bare 10,400) is reconciled to the canonical $10,474 (INV-11)."""
    seed = _seed()
    for rec in seed["exemplars"]:
        assert not re.search(r"\$?10,400", rec["caption"])
    for page in seed["website_pages"]:
        assert not re.search(r"\$?10,400", page["body_summary"])


def test_distilled_captions_pass_v2_grounding() -> None:
    """Every distilled caption passes the REAL V-2 gate (no banned-claim exemplar)."""

    class _Probe:
        def __init__(self, text: str) -> None:
            self.copy_text = text

        @property
        def claims(self) -> list[str]:
            return []

    seed = _seed()
    for rec in seed["exemplars"]:
        assert check_v2(_Probe(rec["caption"])) is RuleVerdict.PASS


def test_distill_dropped_banned_captions() -> None:
    """The distill DROPPED captions carrying a 3x/4x-style multiplier (V-2)."""
    seed = _seed()
    banned = re.compile(r"\b\d+\s*x\b", re.IGNORECASE)
    assert not any(banned.search(rec["caption"]) for rec in seed["exemplars"])


# --------------------------------------------------------------------------- #
# Brand-memory exemplars.
# --------------------------------------------------------------------------- #
def test_exemplars_are_import_kept_with_source_ref() -> None:
    """Each exemplar is an IMPORT-provenance KEPT EXEMPLAR carrying a source_ref."""
    items = load_brand_memory_exemplars(_params())
    assert items
    for item in items:
        assert item.kind is BrandMemoryKind.EXEMPLAR
        assert item.signal is BrandMemorySignal.KEPT
        assert item.provenance.generated_by is GeneratedBy.IMPORT
        assert item.source_ref  # the post url
        assert item.content.strip()
        assert 0.0 <= item.weight <= 1.0


def test_exemplars_are_deterministic() -> None:
    """Same JSON → byte-stable list (ids + order + weights identical)."""
    a = load_brand_memory_exemplars(_params())
    b = load_brand_memory_exemplars(_params())
    assert [(i.id, i.weight, i.content) for i in a] == [(i.id, i.weight, i.content) for i in b]


def test_exemplars_capped_per_theme_by_params() -> None:
    """No theme contributes more exemplars than params.top_n_per_theme (INV-11)."""
    params = _params()
    items = load_brand_memory_exemplars(params)
    top_n = params.library_ingest.top_n_per_theme
    per_theme: dict[str, int] = {}
    for item in items:
        # id form: bm-import-<theme>-<suffix>; theme is everything between.
        theme = item.id[len("bm-import-") : item.id.rfind("-")]
        per_theme[theme] = per_theme.get(theme, 0) + 1
    assert per_theme
    assert all(count <= top_n for count in per_theme.values())


def test_weight_ordering_within_platform() -> None:
    """Higher engagement → higher weight, normalized within the same platform."""
    params = _params()
    from app.data.library_ingest import _normalized_weight

    # Within a platform, more raw engagement never lowers the weight.
    assert _normalized_weight("x/twitter", 100_000, params) >= _normalized_weight(
        "x/twitter", 1_000, params
    )
    assert _normalized_weight("instagram", 150, params) >= _normalized_weight(
        "instagram", 10, params
    )
    # Caps come from params — a value at/above the cap clamps to 1.0.
    cap = params.library_ingest.normalization.x_views_max
    assert _normalized_weight("x/twitter", cap * 2, params) == 1.0


def test_empty_when_seed_missing(tmp_path, monkeypatch) -> None:
    """A missing seed JSON yields [] so the caller falls back to synthetic."""
    import app.data.library_ingest as mod

    monkeypatch.setattr(mod, "SEED_PATH", tmp_path / "absent.json")
    assert mod.load_brand_memory_exemplars(_params()) == []
    assert mod.load_library_assets(_params()) == []


# --------------------------------------------------------------------------- #
# GEO pieces.
# --------------------------------------------------------------------------- #
def test_geo_pieces_locked_competitor_set_and_empty_claims() -> None:
    """GEO competitor_set stays the LOCKED set; claims_text stays empty (INV-6/V-2)."""
    pieces = load_geo_content_pieces()
    assert pieces
    for piece in pieces:
        assert piece.competitor_set == list(GIFTED_SCHOOL_COMPETITOR_SET)
        assert validate_competitor_set(piece.competitor_set)
        assert piece.claims_text == []
        assert piece.baseline_coverage == 0.0
        assert piece.provenance.generated_by is GeneratedBy.IMPORT


def test_geo_pieces_deterministic() -> None:
    """Same call → identical ids (stable hash, no uuid4)."""
    a = load_geo_content_pieces()
    b = load_geo_content_pieces()
    assert [p.id for p in a] == [p.id for p in b]


# --------------------------------------------------------------------------- #
# Library assets (gate-routed).
# --------------------------------------------------------------------------- #
def test_library_assets_gate_routed_with_real_validation_id() -> None:
    """Assets are IMPORT-provenance, KEPT, and carry a gate-produced validation id."""
    assets = load_library_assets(_params())
    assert assets
    for asset in assets:
        assert asset.provenance.generated_by is GeneratedBy.IMPORT
        assert asset.validation  # produced by the real gate, not fabricated
        assert asset.source_ref
        # No fabricated vr-seed-pass-* id (the forbidden shortcut).
        assert not asset.validation.startswith("vr-seed-pass-")


def test_library_assets_deterministic() -> None:
    """Same JSON → byte-stable asset ids + bodies."""
    a = load_library_assets(_params())
    b = load_library_assets(_params())
    assert [(x.id, x.body) for x in a] == [(x.id, x.body) for x in b]


# --------------------------------------------------------------------------- #
# Segmented content shelf — every gate-passing social caption is surfaced.
# --------------------------------------------------------------------------- #
def test_library_surfaces_many_social_copy_assets() -> None:
    """ALL gate-passing exemplars become COPY assets, not just one per theme."""
    from app.ai.schemas.brand import LibraryAssetType

    assets = load_library_assets(_params())
    copy_assets = [a for a in assets if a.asset_type is LibraryAssetType.COPY]
    # The seed holds 404 exemplars across themes/platforms; far more than the
    # 10-theme cap the old loader produced. Assert the shelf is well populated.
    assert len(copy_assets) > 50


def test_social_copy_assets_tagged_with_theme_and_platform() -> None:
    """Each social copy asset carries its theme + platform + the social/proven tags.

    The UI filters by these tags, so every exemplar's theme and platform must be
    present, and the platform must be one of the catalog keys.
    """
    from app.ai.schemas.brand import LibraryAssetType

    seed = _seed()
    seed_platforms = {str(rec["platform"]) for rec in seed["exemplars"]}
    assets = load_library_assets(_params())
    copy_assets = [a for a in assets if a.asset_type is LibraryAssetType.COPY]
    for asset in copy_assets:
        assert "social" in asset.tags
        assert "proven" in asset.tags
        # Exactly one of the asset's tags is a seed platform (theme + platform
        # + the two literals); the platform tag is recognizable.
        platform_tags = [t for t in asset.tags if t in seed_platforms]
        assert len(platform_tags) == 1


def test_website_pages_tagged_blog_vs_website() -> None:
    """Website pages are BLOG_POST assets tagged 'blog' (a /resource article) or 'website'."""
    from app.ai.schemas.brand import LibraryAssetType

    assets = load_library_assets(_params())
    pages = [a for a in assets if a.asset_type is LibraryAssetType.BLOG_POST]
    assert pages
    for page in pages:
        assert "owned" in page.tags
        source = page.source_ref or ""
        if "/resource" in source:
            assert "blog" in page.tags
            assert "website" not in page.tags
        else:
            assert "website" in page.tags
            assert "blog" not in page.tags


def test_social_copy_assets_dedup_by_stable_id() -> None:
    """Asset ids are unique (dedup by stable id) and deterministic across loads."""
    a = load_library_assets(_params())
    ids = [x.id for x in a]
    assert len(ids) == len(set(ids))
    b = load_library_assets(_params())
    assert [x.id for x in a] == [x.id for x in b]


def test_library_assets_still_gate_routed_drops_banned(monkeypatch) -> None:
    """A banned-claim caption injected into the seed is DROPPED by the real gate (INV-4)."""
    import app.data.library_ingest as mod
    from app.ai.schemas.brand import LibraryAssetType

    seed = _seed()
    banned_caption = "Our students score 4X higher than public school — guaranteed!"
    seed["exemplars"] = [
        {
            "caption": banned_caption,
            "engagement_kind": "likes",
            "engagement_raw": 999,
            "platform": "instagram",
            "theme": "academic_outcomes",
            "url": "https://example.test/banned-post",
        }
    ]
    monkeypatch.setattr(mod, "_load_seed", lambda: seed)
    assets = mod.load_library_assets(_params())
    copy_assets = [a for a in assets if a.asset_type is LibraryAssetType.COPY]
    # The gate blocks the 4X claim; it never becomes a library asset.
    assert all(banned_caption not in (a.body or "") for a in copy_assets)
    assert not copy_assets

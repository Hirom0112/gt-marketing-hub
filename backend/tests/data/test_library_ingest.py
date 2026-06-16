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
    assert [(i.id, i.weight, i.content) for i in a] == [
        (i.id, i.weight, i.content) for i in b
    ]


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

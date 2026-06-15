"""BrandMemoryItem / BrandRule / LibraryAsset schema tests (S4; CONTENT_SPEC §8.3/§8.4/§5).

§8.3: BrandMemoryItem is *memory*, not just storage — kept items persist AND
condition the next generation batch. The [req] fields `weight`, `active`,
`version` (plus `kind`, `content`) are what make conditioning + audit (NFR-6)
work; a record missing any is schema-INVALID (V-1). `kind` is a CLOSED enum over
{voice_attribute, exemplar, do_rule, dont_rule, signal}. These tests pin that the
data fails closed: a malformed item RAISES `ValidationError`, never coerces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas.brand import (
    BrandMemoryItem,
    BrandMemoryKind,
    BrandRule,
    LibraryAsset,
)


def _prov() -> dict[str, object]:
    return {"generated_by": "synthetic_seed", "created_at": "2026-06-14T00:00:00Z"}


def _valid_memory_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for a BrandMemoryItem (§8.3)."""
    return {
        "id": "01J0BRANDMEM00000000000000",
        "kind": "voice_attribute",
        "content": "Confident, mastery-focused, parent-respectful, never hypey.",
        "weight": 0.8,
        "active": True,
        "version": 1,
        "provenance": _prov(),
    }


def test_memory_accepts_wellformed() -> None:
    """A well-formed memory item builds with a typed kind enum (§8.3)."""
    item = BrandMemoryItem(**_valid_memory_kwargs())  # type: ignore[arg-type]
    assert item.kind is BrandMemoryKind.VOICE_ATTRIBUTE
    assert item.weight == 0.8
    assert item.active is True
    assert item.version == 1
    # [opt] fields default to None / empty.
    assert item.signal is None
    assert item.source_ref is None
    assert item.channel_scope == []


def test_memory_rejects_missing_required_field() -> None:
    """Missing weight / active / version (or kind/content) RAISES (§8.3, V-1)."""
    for field in ("weight", "active", "version", "kind", "content"):
        bad = _valid_memory_kwargs()
        del bad[field]
        with pytest.raises(ValidationError):
            BrandMemoryItem(**bad)  # type: ignore[arg-type]


def test_memory_kind_enum_closed() -> None:
    """`kind` is CLOSED — an out-of-range value RAISES (§8.3)."""
    bad = _valid_memory_kwargs()
    bad["kind"] = "tagline"
    with pytest.raises(ValidationError):
        BrandMemoryItem(**bad)  # type: ignore[arg-type]
    assert {k.value for k in BrandMemoryKind} == {
        "voice_attribute",
        "exemplar",
        "do_rule",
        "dont_rule",
        "signal",
    }


def test_memory_rejects_extra_field() -> None:
    """An unknown extra field is forbidden (extra='forbid'; §1.2 V-1)."""
    extra = _valid_memory_kwargs()
    extra["confidence"] = 0.9
    with pytest.raises(ValidationError):
        BrandMemoryItem(**extra)  # type: ignore[arg-type]


def test_memory_is_frozen() -> None:
    """A memory item is immutable once built (edits bump `version`, §8.3)."""
    item = BrandMemoryItem(**_valid_memory_kwargs())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        item.active = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BrandRule (§8.4)
# ---------------------------------------------------------------------------


def _valid_rule_kwargs() -> dict[str, object]:
    return {
        "id": "01J0BRANDRULE0000000000000",
        "rule_type": "never",
        "statement": "Never assert unverifiable performance claims (4X/2X, fastest).",
        "enforced_by": "grounding",
        "severity": "block",
        "active": True,
        "provenance": _prov(),
    }


def test_rule_accepts_wellformed_and_closed_enums() -> None:
    """A well-formed brand rule builds with all closed enums (§8.4)."""
    rule = BrandRule(**_valid_rule_kwargs())  # type: ignore[arg-type]
    assert rule.rule_type.value == "never"
    assert rule.enforced_by.value == "grounding"
    assert rule.severity.value == "block"
    assert rule.applies_to == []


def test_rule_rejects_out_of_range_enums() -> None:
    """rule_type / enforced_by / severity are CLOSED — out-of-range RAISES."""
    for field, value in (
        ("rule_type", "suggest"),
        ("enforced_by", "vibes"),
        ("severity", "ignore"),
    ):
        bad = _valid_rule_kwargs()
        bad[field] = value
        with pytest.raises(ValidationError):
            BrandRule(**bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LibraryAsset (§5)
# ---------------------------------------------------------------------------


def _valid_asset_kwargs() -> dict[str, object]:
    return {
        "id": "01J0LIBASSET000000000000000",
        "title": "Mastery-based gifted K-8 — short caption",
        "asset_type": "copy",
        "tags": ["caption", "prospective_parent"],
        "search_text": "mastery-based gifted k-8 virtual school caption",
        "validation": "01J0VALIDATION00000000000",
        "lifecycle": "kept",
        "provenance": _prov(),
    }


def test_library_asset_accepts_wellformed() -> None:
    """A validated, kept LibraryAsset builds (§5)."""
    asset = LibraryAsset(**_valid_asset_kwargs())  # type: ignore[arg-type]
    assert asset.asset_type.value == "copy"
    assert asset.lifecycle.value == "kept"
    assert asset.tags == ["caption", "prospective_parent"]
    assert asset.channel is None  # [opt]


def test_library_asset_rejects_missing_required() -> None:
    """Missing [req] fields RAISE — only validated content enters the library (§5)."""
    for field in ("title", "asset_type", "tags", "search_text", "validation", "lifecycle"):
        bad = _valid_asset_kwargs()
        del bad[field]
        with pytest.raises(ValidationError):
            LibraryAsset(**bad)  # type: ignore[arg-type]


def test_library_asset_type_enum_closed() -> None:
    """`asset_type` is CLOSED — an out-of-range value RAISES (§5)."""
    bad = _valid_asset_kwargs()
    bad["asset_type"] = "podcast"
    with pytest.raises(ValidationError):
        LibraryAsset(**bad)  # type: ignore[arg-type]

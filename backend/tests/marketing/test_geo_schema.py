"""GeoContentPiece schema + LOCKED competitor-set tests — CONTENT_SPEC §7 (S5).

§7.1 locks the `GeoContentPiece` shape (the GEO content-as-data record); §7.3
locks the gifted-school competitor set and declares any test-prep brand
content-invalid for this category (INV-6); §7.4 makes a single-snapshot coverage
claim invalid (must be measured by repeated sampling).

Per CLAUDE.md §4.1 these are pure red→green logic/schema tests: an out-of-range
`geoStructure` RAISES (closed enum), required fields are enforced,
`baselineCoverage` defaults to the 0% baseline, and `validate_competitor_set`
validates the gifted-school universe while rejecting any test-prep brand.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.schemas.content import (
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.marketing.geo import (
    GIFTED_SCHOOL_COMPETITOR_SET,
    validate_competitor_set,
)
from app.marketing.schemas.geo import GeoContentPiece, GeoStructure


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.LLM, created_at="2026-06-14T00:00:00Z")


def _piece(**overrides: object) -> GeoContentPiece:
    base: dict[str, object] = {
        "id": uuid4(),
        "target_prompt": "what is the best online gifted school",
        "geo_structure": GeoStructure.DEFINITION,
        "body": "GT School is an online school for profoundly gifted learners.",
        "competitor_set": list(GIFTED_SCHOOL_COMPETITOR_SET),
        "validation": "val-1",
        "lifecycle": LifecycleStage.CANDIDATE,
        "provenance": _provenance(),
    }
    base.update(overrides)
    return GeoContentPiece(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# §7.3 — competitor set is the LOCKED gifted-school universe.
# --------------------------------------------------------------------------- #
def test_competitor_set_is_gifted_school() -> None:
    """The gifted-school set validates; any test-prep brand ⇒ content-invalid."""
    # The LOCKED universe validates True.
    assert validate_competitor_set(GIFTED_SCHOOL_COMPETITOR_SET) is True
    assert validate_competitor_set(list(GIFTED_SCHOOL_COMPETITOR_SET)) is True
    # A subset of gifted-school domains is still valid.
    assert validate_competitor_set(["joinprisma.com", "k12.com"]) is True

    # Any test-prep brand in the set ⇒ content-invalid (False) — the caller BLOCKs.
    assert validate_competitor_set(["kaplan.com"]) is False
    assert validate_competitor_set(["princetonreview.com"]) is False
    assert validate_competitor_set([*GIFTED_SCHOOL_COMPETITOR_SET, "kaplan.com"]) is False

    # An empty set is not a valid competitor set.
    assert validate_competitor_set([]) is False

    # The LOCKED set is exactly the §7.3 gifted-school domains.
    assert GIFTED_SCHOOL_COMPETITOR_SET == (
        "joinprisma.com",
        "fusionacademy.com",
        "davidsononline.org",
        "k12.com",
        "niche.com",
    )


# --------------------------------------------------------------------------- #
# §7.1 — GeoContentPiece schema (LOCKED).
# --------------------------------------------------------------------------- #
def test_geo_content_piece_schema() -> None:
    """baselineCoverage default 0, geoStructure enum in-range, required fields present."""
    piece = _piece()

    # baselineCoverage starts at the 0% baseline.
    assert piece.baseline_coverage == 0.0

    # Required fields are present and typed.
    assert piece.target_prompt
    assert piece.geo_structure is GeoStructure.DEFINITION
    assert piece.body
    assert piece.competitor_set
    assert piece.validation == "val-1"
    assert piece.lifecycle is LifecycleStage.CANDIDATE
    assert piece.provenance.generated_by is GeneratedBy.LLM

    # Optional fields default empty/None.
    assert piece.citation_targets == []
    assert piece.structured_data_note is None
    assert piece.sampling_note is None
    assert piece.claims_text == []

    # geoStructure is a CLOSED enum — an out-of-range value RAISES (V-1, §9.2).
    with pytest.raises(ValidationError):
        _piece(geo_structure="press_release")

    # Every enum member is in range and round-trips.
    for member in GeoStructure:
        assert _piece(geo_structure=member).geo_structure is member

    # A missing required field RAISES (fails closed).
    with pytest.raises(ValidationError):
        GeoContentPiece(  # type: ignore[call-arg]
            id=uuid4(),
            geo_structure=GeoStructure.FAQ,
            body="x",
            competitor_set=list(GIFTED_SCHOOL_COMPETITOR_SET),
            validation="val-1",
            lifecycle=LifecycleStage.CANDIDATE,
            provenance=_provenance(),
        )

    # An unknown extra field is rejected (extra="forbid", §1.2).
    with pytest.raises(ValidationError):
        _piece(unexpected_field="nope")


def test_geo_content_piece_wire_aliases() -> None:
    """The §7.1 camelCase wire names (`baselineCoverage`, `geoStructure`) populate."""
    piece = GeoContentPiece(  # type: ignore[call-arg]
        id=uuid4(),
        targetPrompt="best gifted school",
        geoStructure="faq",
        body="GT School serves gifted learners.",
        competitorSet=list(GIFTED_SCHOOL_COMPETITOR_SET),
        baselineCoverage=0,
        validation="val-1",
        lifecycle=LifecycleStage.CANDIDATE,
        provenance=_provenance(),
    )
    assert piece.geo_structure is GeoStructure.FAQ
    assert piece.baseline_coverage == 0.0
    assert piece.target_prompt == "best gifted school"


def test_geo_content_piece_exposes_claims_for_gate() -> None:
    """`claims` is a read-only property returning the empirical claim strings.

    A GeoContentPiece must structurally satisfy the gate's `GatedRecord`
    Protocol (whose `claims` is a `@property -> Sequence[object]`), so it can
    flow through the EXISTING grounding gate (A-10).
    """
    piece = _piece(claims_text=["GT appears in directory listings (cited)."])
    assert list(piece.claims) == ["GT appears in directory listings (cited)."]
    # `claims` is read-only (a property, not a settable field).
    with pytest.raises((AttributeError, ValidationError)):
        piece.claims = ["mutate"]  # type: ignore[misc]

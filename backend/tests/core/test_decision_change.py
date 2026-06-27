"""Decision-change core tests (E1; CLAUDE.md §4.1, INV-2/INV-11).

The headline proof of the brief: an Open Data (Texas-district) enrichment that
CHANGES a decision. :func:`app.core.decision_change.enrich_decision` takes a
Decision-Queue recommendation + a district enrichment + params, and BOOSTS the
recommendation's priority iff the district is genuinely under-served — low A–F
rating AND STAAR below the proficiency floor AND enrollment at/above the minimum.
A healthy / A-rated district produces NO boost.

Every threshold reads from the committed params (``open_data.decision_change``) so
a param drift fails the build (INV-11). The enrichment is built here via a local
structural stand-in for the adapter's ``DistrictEnrichment`` — the core depends on
a ``Protocol``, never on ``app.adapters`` (the core-purity test guards this).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.decision_change import DecisionRec, enrich_decision
from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


@dataclass(frozen=True)
class _Enrichment:
    """A structural stand-in matching ``EnrichmentLike`` (NOT the adapter import).

    Mirrors the seeded adapter's poles' shape so the core can be exercised without
    importing ``app.adapters`` (purity). Fields match ``DistrictEnrichment``.
    """

    d_rating: str
    staar_proficiency: float
    enrollment: int
    per_pupil_spend: float


def test_low_rating_district_changes_rec() -> None:
    """A D/F district below the STAAR floor with enough enrollment BOOSTS the rec."""
    params = load_params(EXAMPLE_PARAMS)
    dc = params.open_data.decision_change

    # An under-served district: a low grade, STAAR under the floor, enrollment at/above min.
    low_grade = dc.low_rating_grades[0]
    under_served = _Enrichment(
        d_rating=low_grade,
        staar_proficiency=dc.staar_proficiency_floor - 0.05,
        enrollment=dc.min_enrollment,
        per_pupil_spend=9_100.0,
    )

    base_priority = 3
    rec = DecisionRec(priority=base_priority, payload={"family_id": "fam-1"})
    enriched = enrich_decision(rec, under_served, params=params)

    # The "changes a decision" proof: the enriched priority is STRICTLY greater.
    assert enriched.priority > base_priority
    assert enriched.priority == base_priority + dc.priority_boost

    prov = enriched.provenance
    assert prov.changed is True
    assert prov.delta == dc.priority_boost
    # All three signals are recorded as the cause of the change.
    assert len(prov.signals) == 3
    assert prov.reason


def test_a_rated_district_no_boost() -> None:
    """An A-rated, healthy district trips NONE of the conditions ⇒ priority UNCHANGED."""
    params = load_params(EXAMPLE_PARAMS)
    dc = params.open_data.decision_change

    healthy = _Enrichment(
        d_rating="A",
        staar_proficiency=dc.staar_proficiency_floor + 0.30,
        enrollment=dc.min_enrollment + 1_000,
        per_pupil_spend=11_200.0,
    )

    base_priority = 3
    rec = DecisionRec(priority=base_priority, payload={"family_id": "fam-2"})
    enriched = enrich_decision(rec, healthy, params=params)

    assert enriched.priority == base_priority
    prov = enriched.provenance
    assert prov.changed is False
    assert prov.delta == 0
    assert prov.signals == ()


def test_enrollment_just_below_min_no_boost() -> None:
    """The AND gate: a low/under-floor district just UNDER min enrollment is NOT boosted."""
    params = load_params(EXAMPLE_PARAMS)
    dc = params.open_data.decision_change

    thin = _Enrichment(
        d_rating=dc.low_rating_grades[-1],
        staar_proficiency=dc.staar_proficiency_floor - 0.05,
        enrollment=dc.min_enrollment - 1,
        per_pupil_spend=9_100.0,
    )

    base_priority = 5
    rec = DecisionRec(priority=base_priority, payload={})
    enriched = enrich_decision(rec, thin, params=params)

    assert enriched.priority == base_priority
    assert enriched.provenance.changed is False


def test_staar_above_floor_no_boost() -> None:
    """A low-rated district whose STAAR is at/above the floor is NOT boosted (AND gate)."""
    params = load_params(EXAMPLE_PARAMS)
    dc = params.open_data.decision_change

    rec = DecisionRec(priority=4, payload={})
    not_low_enough = _Enrichment(
        d_rating=dc.low_rating_grades[0],
        staar_proficiency=dc.staar_proficiency_floor,  # at the floor is not BELOW it
        enrollment=dc.min_enrollment + 100,
        per_pupil_spend=9_100.0,
    )
    enriched = enrich_decision(rec, not_low_enough, params=params)
    assert enriched.priority == 4
    assert enriched.provenance.changed is False

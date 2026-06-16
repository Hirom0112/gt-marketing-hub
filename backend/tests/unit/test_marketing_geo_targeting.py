"""S6 marketing-breadth geo-targeting + new-recipe tests (FR-3.9/3.12; INV-6/7).

These pin the Breadth gaps the investigation surfaced:

  ``GET /geo-targeting`` (FR-3.9, INV-6) — a DISTINCT endpoint from the ``/geo``
  GEO board: it rolls the synthetic ``LeadsNew.region`` field up into AGGREGATE
  region counts (region is already aggregate — no minor keying, no per-child
  data) and surfaces the strategy's named demand metros (Austin/Houston/Dallas +
  Raleigh NC). The response carries NO per-child / minor field (INV-6).

  ``generate_recipes`` (FR-3.12, INV-7) — the two strategically-critical themes
  the catalog under-posted (TEFA $0-net affordability; socialization-as-proof)
  are present, each carrying the LOCKED Tom Babb attribution.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.data.repository import DEFAULT_FAMILY_COUNT, DEFAULT_SEED
from app.data.synthetic import _TOM_BABB_ATTRIBUTION, generate, generate_recipes
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + no stray dependency overrides per test."""
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


# --------------------------------------------------------------------------- #
# GET /geo-targeting — aggregate region rollup (FR-3.9, INV-6).
# --------------------------------------------------------------------------- #

# A per-child / minor-keyed field must NEVER appear in the aggregate response
# (INV-6 — the whole point of the panel is that targeting is aggregate-only).
_FORBIDDEN_CHILD_FIELDS = frozenset(
    {
        "child",
        "children",
        "minor",
        "minors",
        "student",
        "students",
        "kid",
        "kids",
        "num_children",
        "child_id",
        "student_id",
        "name",
        "email",
        "phone",
        "lead_id",
        "family_id",
    }
)


def test_get_geo_targeting_is_distinct_from_geo_board() -> None:
    """GET /geo-targeting is its OWN endpoint (not the /geo GEO board)."""
    resp = client.get("/geo-targeting")
    assert resp.status_code == 200


def test_get_geo_targeting_contract_shape() -> None:
    """GET /geo-targeting returns {regions, demand_metros, total} aggregate rows."""
    body = client.get("/geo-targeting").json()
    assert "regions" in body
    assert "demand_metros" in body
    assert "total" in body
    assert isinstance(body["regions"], list)
    assert body["regions"], "expected at least one aggregate region row"
    for row in body["regions"]:
        for field in ("region", "lead_count", "share"):
            assert field in row, f"missing region field: {field}"


def test_get_geo_targeting_rolls_up_synthetic_regions() -> None:
    """Region counts equal the synthetic LeadsNew.region rollup (faithful wiring)."""
    # Recreate the seed the in-memory repo hydrates from, then roll up by region.
    dataset = generate(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    expected: dict[str, int] = {}
    for lead in dataset.leads:
        expected[lead.region] = expected.get(lead.region, 0) + 1

    body = client.get("/geo-targeting").json()
    got = {row["region"]: row["lead_count"] for row in body["regions"]}
    assert got == expected
    assert body["total"] == sum(expected.values())


def test_get_geo_targeting_sorted_by_count_desc() -> None:
    """Aggregate region rows are sorted by lead_count descending (then region)."""
    rows = client.get("/geo-targeting").json()["regions"]
    counts = [row["lead_count"] for row in rows]
    assert counts == sorted(counts, reverse=True)


def test_get_geo_targeting_share_sums_to_one() -> None:
    """Each region's share is its fraction of the total; shares sum to ~1.0."""
    body = client.get("/geo-targeting").json()
    total_share = sum(row["share"] for row in body["regions"])
    assert total_share == pytest.approx(1.0)
    for row in body["regions"]:
        assert row["share"] == pytest.approx(row["lead_count"] / body["total"])


def test_get_geo_targeting_surfaces_named_demand_metros() -> None:
    """The strategy's named demand metros (Austin/Houston/Dallas/Raleigh) surface."""
    metros = client.get("/geo-targeting").json()["demand_metros"]
    names = " ".join(m["metro"] for m in metros)
    for expected in ("Austin", "Houston", "Dallas", "Raleigh"):
        assert expected in names, f"missing demand metro: {expected}"


def test_get_geo_targeting_no_per_child_or_minor_field() -> None:
    """INV-6: no per-child / minor / individual-keyed field anywhere in the response."""
    body = client.get("/geo-targeting").json()

    def _walk(node: object) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from _walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from _walk(item)

    for key in _walk(body):
        assert key.lower() not in _FORBIDDEN_CHILD_FIELDS, (
            f"INV-6 violation: child/minor-keyed field '{key}' in geo-targeting response"
        )


# --------------------------------------------------------------------------- #
# generate_recipes — the two new under-posted themes (FR-3.12, INV-7).
# --------------------------------------------------------------------------- #


def test_new_tefa_and_socialization_recipes_present() -> None:
    """The TEFA-affordability and socialization-as-proof recipes are added."""
    recipes = generate_recipes()
    blob = " ".join(f"{r.id} {r.name} {r.description}".lower() for r in recipes)
    assert "tefa" in blob, "expected a TEFA $0-net affordability recipe"
    assert "social" in blob, "expected a socialization-as-proof recipe"


def test_new_recipes_carry_tom_babb_attribution() -> None:
    """Every recipe — including the two new themes — names Tom Babb (INV-7)."""
    recipes = generate_recipes()
    assert len(recipes) >= 5, "expected the 3 originals + the 2 new themes"
    for recipe in recipes:
        assert recipe.attribution == _TOM_BABB_ATTRIBUTION
        assert "Tom Babb" in recipe.attribution


def test_new_recipes_surface_over_http_with_attribution() -> None:
    """GET /recipes surfaces the new recipes, each attributing Tom Babb (INV-7)."""
    body = client.get("/recipes").json()
    blob = " ".join(f"{r['id']} {r['name']} {r['description']}".lower() for r in body)
    assert "tefa" in blob
    assert "social" in blob
    for recipe in body:
        assert "Tom Babb" in recipe["attribution"]

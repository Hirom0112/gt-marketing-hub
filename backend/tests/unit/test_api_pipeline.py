"""Read-API pipeline tests (S0; ARCHITECTURE.md §6, FR-2.1).

Boots the FastAPI app over a fixed-seed synthetic dataset and asserts the
deterministic per-stage tally `GET /pipeline` returns. The seed is fixed
(`DEFAULT_SEED`/`DEFAULT_FAMILY_COUNT`, documented constants) so the counts are
exact: `synthetic.generate` is byte-reproducible under a seed (CLAUDE.md §4.1),
so the pipeline counts a test may assert are themselves deterministic.

These tests own the repository → API seam end to end: the in-memory repository
(ASSUMPTIONS A-3) is seeded once from the same generator the test draws its
expectations from, then the read endpoints are exercised over it.
"""

from __future__ import annotations

from collections import Counter

from fastapi.testclient import TestClient

from app.data.repository import DEFAULT_FAMILY_COUNT, DEFAULT_SEED
from app.data.synthetic import generate
from app.main import app

client = TestClient(app)

# The four funnel stages, in funnel order (§4.8 Stage).
_STAGE_KEYS = ("interest", "apply", "enroll", "tuition")


def _expected_stage_counts() -> dict[str, int]:
    """Per-stage counts derived from the SAME fixed-seed dataset the app boots over."""
    ds = generate(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    return dict(Counter(f.current_stage.value for f in ds.families))


def test_pipeline_returns_per_stage_counts() -> None:
    """GET /pipeline returns deterministic per-stage counts that sum to the total."""
    response = client.get("/pipeline")
    assert response.status_code == 200

    body = response.json()
    counts = body["counts"]

    expected = _expected_stage_counts()

    # Every funnel stage is present and matches the fixed-seed expectation exactly.
    for stage in _STAGE_KEYS:
        assert counts[stage] == expected.get(stage, 0), stage

    # The per-stage counts sum to the family total (DEFAULT_FAMILY_COUNT).
    assert sum(counts[s] for s in _STAGE_KEYS) == DEFAULT_FAMILY_COUNT
    assert body["total"] == DEFAULT_FAMILY_COUNT

    # The CRM-seam summary is present and sums to the same family total (FR-2.6).
    seam = body["seam"]
    assert set(seam) == {"synced", "unsynced", "conflict"}
    assert sum(seam.values()) == DEFAULT_FAMILY_COUNT


def test_families_list_and_filter() -> None:
    """GET /families lists all families and filters by stage (FR-2.1)."""
    all_resp = client.get("/families")
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == DEFAULT_FAMILY_COUNT

    expected = _expected_stage_counts()
    filtered = client.get("/families", params={"stage": "interest"})
    assert filtered.status_code == 200
    rows = filtered.json()
    assert len(rows) == expected["interest"]
    assert all(r["current_stage"] == "interest" for r in rows)


def test_get_family_returns_joined_record() -> None:
    """GET /families/{id} returns the spine record joined to its four source rows (FR-2.2)."""
    first_id = client.get("/families").json()[0]["family_id"]

    detail = client.get(f"/families/{first_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["family"]["family_id"] == first_id
    # The four joined source rows are present.
    assert body["lead"]["family_id"] == first_id
    assert body["app_form"]["family_id"] == first_id
    assert body["enrollment_forms"]["family_id"] == first_id
    assert body["community_profile"]["family_id"] == first_id


def test_get_unknown_family_returns_404() -> None:
    """An unknown family_id yields a 404 (read-only, no leakage)."""
    missing = client.get("/families/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404


def test_pipeline_carries_per_child_grain() -> None:
    """GET /pipeline ALSO returns the per-CHILD grain (A-24): each child placed in
    its own derived stage, so a multi-child household spans every stage its children
    occupy. Driven over the 12-household demo cohort (13 children — only the Rivera
    household has 2, at DIFFERENT derived stages)."""
    from app.api import deps
    from app.data.models import Stage
    from app.data.repository import InMemoryFamilyRepository, student_stage_counts
    from app.data.synthetic import generate_demo_cohort

    repo = InMemoryFamilyRepository(generate_demo_cohort(params=deps._params), params=deps._params)
    app.dependency_overrides[deps.get_repository] = lambda: repo
    try:
        body = client.get("/pipeline").json()
        # Household grain unchanged: 12 households.
        assert body["total"] == 12
        # Per-child grain present and a partition of all 13 children.
        assert set(_STAGE_KEYS) <= set(body["student_counts"])
        assert body["total_students"] == 13
        assert sum(body["student_counts"][s] for s in _STAGE_KEYS) == 13
        # A DIFFERENT grain than households (13 children ≠ 12 households).
        assert body["total_students"] != body["total"]
        # Matches the deterministic per-child derivation over the same cohort.
        expected = student_stage_counts(repo.list_students(), deps._params)
        for stage in _STAGE_KEYS:
            assert body["student_counts"][stage] == expected[Stage(stage)]
        # The one 2-child household (Rivera) splits across ≥2 derived stages — proof
        # a household genuinely spans columns at the child grain.
        from app.core.stage_machine import FamilyInputs, derive_stage

        rivera = [
            js for js in repo.list_students() if js.family.display_name == "The Rivera Family"
        ]
        rivera_stages = {
            derive_stage(
                FamilyInputs(
                    app_form=js.app_form,
                    enrollment_forms=js.enrollment_forms,
                    stalled_since=js.student.stalled_since,
                ),
                deps._params,
            )
            for js in rivera
        }
        assert len(rivera) == 2 and len(rivera_stages) >= 2
    finally:
        app.dependency_overrides.pop(deps.get_repository, None)

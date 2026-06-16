"""Per-child board endpoint tests (A-24; `GET /students`).

Boots the FastAPI app over the SAME fixed-seed synthetic dataset the app serves,
and pins the §6-style contract for the per-child board: one row per child, ranked
by ``recoverable_now_student`` and grouped by household, with the household
$-at-risk summing one per-child tuition over still-active students (the per-child
replacement for the old all-or-nothing family value). Deterministic without a
local ``params/params.yaml``: the app loads the committed example file.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.api.deps import get_crm_adapter_dep
from app.core.params import load_params
from app.data.repository import DEFAULT_FAMILY_COUNT, DEFAULT_SEED, InMemoryFamilyRepository
from app.main import app

client = TestClient(app)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _seeded() -> InMemoryFamilyRepository:
    return InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)


def test_students_board_contract() -> None:
    """`GET /students` returns one row per child, grouped by household, ranked."""
    resp = client.get("/students")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    repo = _seeded()
    students = repo.list_students()
    family_ids = {str(js.student.family_id) for js in students}  # response serializes UUIDs as str

    # One row per child overall — exactly the seeded student count.
    assert body["total_students"] == len(students)
    flat = [row for h in body["households"] for row in h["students"]]
    assert len(flat) == len(students)

    # Every household groups only its own children; ids line up with the seed.
    assert {h["family_id"] for h in body["households"]} == family_ids
    for h in body["households"]:
        assert h["students"], "a household with no students should not appear"
        assert all(row["family_id"] == h["family_id"] for row in h["students"])
        assert all(row["household_name"] == h["household_name"] for row in h["students"])
        # Distinct per-student labels within the household (de-dupes the board).
        labels = [row["display_label"] for row in h["students"]]
        assert len(set(labels)) == len(labels)


def test_students_value_is_one_child_tuition_and_at_risk_sums_active() -> None:
    """value == one per-child tuition; household value_at_risk sums ACTIVE students."""
    params = load_params(EXAMPLE_PARAMS)
    tuition = params.work_queue.value.tuition_annual_default

    body = client.get("/students").json()
    active_states = {"stalled", "working"}

    total = 0.0
    for h in body["households"]:
        # Every child is worth exactly one per-child tuition (no num_children mult).
        assert all(row["value"] == tuition for row in h["students"])
        # value_at_risk = one tuition per still-active student (not all-or-nothing).
        active = [r for r in h["students"] if r["recovery_state"] in active_states]
        assert h["value_at_risk"] == len(active) * tuition
        total += h["value_at_risk"]

    assert body["total_value_at_risk"] == total


def test_students_ranked_by_recoverable_now_desc() -> None:
    """Students rank within a household, and households by their top child (A-24)."""
    body = client.get("/students").json()

    # Within each household, students are ordered by recoverable_now (desc).
    for h in body["households"]:
        within = [row["recoverable_now"] for row in h["students"]]
        assert within == sorted(within, reverse=True)

    # Households surface in most-recoverable-child order (each household's top
    # student leads it, and the most-recoverable child leads the board).
    tops = [h["students"][0]["recoverable_now"] for h in body["households"]]
    assert tops == sorted(tops, reverse=True)

    valid = {"stalled", "working", "recovered", "dismissed"}
    flat = [row for h in body["households"] for row in h["students"]]
    assert all(row["recovery_state"] in valid for row in flat)


def test_transfer_student_to_crm_simulated_records_push() -> None:
    """POST /students/{id}/seam pushes one child through the simulated CRM seam (INV-9)."""
    fake = SimulatedCRMAdapter()
    app.dependency_overrides[get_crm_adapter_dep] = lambda: fake
    try:
        # A real student id from the same fixed-seed store the app serves.
        student = deps.get_repository().list_students()[0].student  # type: ignore[attr-defined]
        resp = client.post(f"/students/{student.student_id}/seam")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["simulated"] is True
        assert body["student_id"] == str(student.student_id)
        assert body["family_id"] == str(student.family_id)
        # Recorded, never sent — the per-child audit log holds exactly this push.
        assert len(fake.pushed_student_log) == 1
        assert fake.pushed_student_log[0].student_id == student.student_id
    finally:
        app.dependency_overrides.clear()


def test_transfer_unknown_student_404() -> None:
    """An unknown student id is a clean 404, not a 500."""
    from uuid import uuid4

    fake = SimulatedCRMAdapter()
    app.dependency_overrides[get_crm_adapter_dep] = lambda: fake
    try:
        resp = client.post(f"/students/{uuid4()}/seam")
        assert resp.status_code == 404
        assert not fake.pushed_student_log
    finally:
        app.dependency_overrides.clear()

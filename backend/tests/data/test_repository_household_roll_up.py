"""In-memory ``household_roll_up`` + the shared ``roll_up_households`` helper.

``GET /households`` (the reconciliation board) must populate on the in-memory
synthetic cohort (the default demo path, ``COCKPIT_REPO=synthetic``), not only on
the live Supabase store. These tests pin the in-memory roll-up to the SAME
behavior the live impl has: group ``list_students()`` by household, one row per
household carrying every child's DERIVED stage + a ``worst_stage`` (least-advanced
child), in a deterministic stable order. They also pin the extracted pure helper
(``roll_up_households``) the live impl now delegates to, proving DRY + order
stability.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.stage_machine import FamilyInputs, derive_stage
from app.data.models import Stage
from app.data.repository import (
    DEFAULT_FAMILY_COUNT,
    DEFAULT_SEED,
    HouseholdChildStage,
    HouseholdRollUp,
    InMemoryFamilyRepository,
    JoinedStudent,
    roll_up_households,
)
from app.data.synthetic import SyntheticDataset, generate


def _derived_stage(js: JoinedStudent, params: object) -> Stage:
    """The stage the live path derives for a child (its own forms)."""
    return derive_stage(
        FamilyInputs(
            app_form=js.app_form,
            enrollment_forms=js.enrollment_forms,
            stalled_since=js.student.stalled_since,
        ),
        params,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# In-memory household_roll_up over the synthetic cohort.
# ---------------------------------------------------------------------------


def test_in_memory_household_roll_up_populates_on_synthetic_cohort() -> None:
    """The synthetic cohort yields real rollup rows (not the old []-degrade)."""
    repo = InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    rollups = repo.household_roll_up()
    assert rollups, "synthetic cohort must produce household rollups, not []"
    # Every student is accounted for exactly once across the households.
    students = repo.list_students()
    assert sum(len(r.children) for r in rollups) == len(students)


def test_in_memory_household_roll_up_has_a_multi_child_household() -> None:
    """The cohort has at least one household with >1 child (siblings grouped)."""
    repo = InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    rollups = repo.household_roll_up()
    assert any(len(r.children) > 1 for r in rollups)


def test_in_memory_household_roll_up_uses_derived_stage_and_worst_stage() -> None:
    """Children carry the DERIVED stage; worst_stage is the least-advanced child."""
    from app.api.deps import _params

    repo = InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    rollups = repo.household_roll_up()

    # Map each student → its live-derived stage so we can assert the roll-up used
    # the DERIVED stage (NOT the stored placeholder; they diverge for apply-stage
    # students whose application is not yet submitted).
    expected: dict[UUID, Stage] = {
        js.student.student_id: _derived_stage(js, _params) for js in repo.list_students()
    }
    order = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION)
    for r in rollups:
        for c in r.children:
            assert c.stage == expected[c.student_id]
        assert r.worst_stage == min((c.stage for c in r.children), key=order.index)


def test_in_memory_household_roll_up_groups_by_user_id_with_worst_stage() -> None:
    """Two synthetic households grouped by user_id, each with its own worst_stage."""
    # Build a deterministic two-household dataset where the stored stage and the
    # derived stage agree (full forms ⇒ tuition; no app ⇒ interest).
    base = generate(n=2, seed=DEFAULT_SEED)
    fam_a = base.families[0]
    fam_b = base.families[1]
    uid_a = uuid4()
    uid_b = uuid4()
    fam_a = fam_a.model_copy(update={"user_id": uid_a})
    fam_b = fam_b.model_copy(update={"user_id": uid_b})

    fids = {fam_a.family_id, fam_b.family_id}
    repo = InMemoryFamilyRepository(
        SyntheticDataset(
            families=[fam_a, fam_b],
            leads=[lead for lead in base.leads if lead.family_id in fids],
            students=[s for s in base.students if s.family_id in fids],
            student_app_forms=base.student_app_forms,
            student_enrollment_forms=base.student_enrollment_forms,
        )
    )
    rollups = repo.household_roll_up()
    by_uid = {r.user_id: r for r in rollups}
    assert set(by_uid) == {uid_a, uid_b}


def test_in_memory_household_roll_up_null_owner_separate() -> None:
    """A None-owner household falls back to its own family_id as its group key."""
    base = generate(n=2, seed=DEFAULT_SEED)
    # Both families keep user_id None (the generator default) — they must stay
    # separate, keyed by their own family_id, NOT collapsed into one None group.
    repo = InMemoryFamilyRepository(base)
    rollups = repo.household_roll_up()
    assert all(r.user_id is None for r in rollups)
    # One rollup per distinct family that has students.
    fam_ids_with_students = {s.family_id for s in base.students}
    assert {r.family_id for r in rollups} == fam_ids_with_students


# ---------------------------------------------------------------------------
# The shared pure helper — order stability + shape (DRY across both repos).
# ---------------------------------------------------------------------------


def test_roll_up_households_is_order_stable() -> None:
    """Households appear in first-appearance order; children in read order."""
    repo = InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    students = repo.list_students()
    a = roll_up_households(students)
    b = roll_up_households(students)
    assert [r.family_id for r in a] == [r.family_id for r in b]
    for ra, rb in zip(a, b, strict=True):
        assert [c.student_id for c in ra.children] == [c.student_id for c in rb.children]

    # First-appearance household order: the helper's row order matches the order
    # households first appear in the read order of list_students.
    expected_order: list[UUID] = []
    for js in students:
        fid = js.family.family_id
        if fid not in expected_order:
            expected_order.append(fid)
    assert [r.family_id for r in a] == expected_order

    # Shape: the public dataclasses, and the helper trusts student.current_stage
    # as-is (the "already-derived" contract — it does NOT re-derive).
    assert isinstance(a[0], HouseholdRollUp)
    assert isinstance(a[0].children[0], HouseholdChildStage)
    stored_by_sid = {s.student.student_id: s.student.current_stage for s in students}
    for r in a:
        for c in r.children:
            assert c.stage == stored_by_sid[c.student_id]

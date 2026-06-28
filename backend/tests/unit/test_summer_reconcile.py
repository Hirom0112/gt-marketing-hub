"""Unit tests for the summer-camp dual-source reconciler (D2).

Pins the no-double-count contract (INV-2) and the fail-closed conflict path (INV-4):

* an overlapping registrant (same identity in BOTH sources) is counted ONCE;
* the per-campus rollup sums to the fixed capacity total, registered ≤ capacity;
* paid vs lead splits per campus;
* an ambiguous registrant (same identity, conflicting campus) is HELD OUT of every
  campus count and surfaced as a conflict — never silently merged.
"""

from __future__ import annotations

from app.core.summer_reconcile import PROGRAM_ID, CampRegistration, reconcile
from app.data.synthetic_summer import (
    _PAID_TARGET,
    _REGISTERED_TARGET,
    CAMPUS_CAPACITY,
    TOTAL_CAPACITY,
    generate_summer_dataset,
    generate_summer_sources,
)


def _reg(
    ext: str,
    source: str,
    campus: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    paid: bool = False,
    band: str = "K-2",
) -> CampRegistration:
    return CampRegistration(
        external_id=ext,
        source=source,
        campus=campus,
        child_grade_band=band,
        synthetic_email=email,
        synthetic_phone=phone,
        paid=paid,
    )


def test_overlapping_registrant_counted_once() -> None:
    """Same email in BOTH sources, same campus ⇒ ONE unique registration, one merged."""
    rows = [
        _reg("site-1", "summer_site", "Austin", email="a@example.invalid", paid=True),
        _reg("form-1", "registration_form", "Austin", email="A@Example.Invalid", paid=False),
    ]
    result = reconcile(rows, {"Austin": 100})

    assert result.unique_registrations == 1  # counted ONCE, not twice
    assert result.raw_source_rows == 2
    assert result.duplicates_merged == 1  # the second appearance folded
    austin = result.per_campus[0]
    assert austin.registered == 1
    # paid is OR-ed across sources: paid in EITHER ⇒ paid.
    assert austin.paid == 1
    assert austin.lead == 0
    assert result.conflicts == ()


def test_phone_fallback_dedups_when_no_email() -> None:
    """No email ⇒ dedup falls back to normalized phone digits (punctuation-agnostic)."""
    rows = [
        _reg("site-1", "summer_site", "Dallas", phone="(512) 555-0101"),
        _reg("form-1", "registration_form", "Dallas", phone="512.555.0101"),
    ]
    result = reconcile(rows, {"Dallas": 100})
    assert result.unique_registrations == 1
    assert result.duplicates_merged == 1


def test_distinct_registrants_not_merged() -> None:
    """Different identities are NOT merged — both counted."""
    rows = [
        _reg("site-1", "summer_site", "Austin", email="a@example.invalid"),
        _reg("form-1", "registration_form", "Austin", email="b@example.invalid"),
    ]
    result = reconcile(rows, {"Austin": 100})
    assert result.unique_registrations == 2
    assert result.duplicates_merged == 0


def test_unkeyed_row_is_its_own_registration() -> None:
    """A row with no email AND no phone cannot match — never false-merged."""
    rows = [
        _reg("site-1", "summer_site", "Austin", email=None, phone=None),
        _reg("form-1", "registration_form", "Austin", email=None, phone=None),
    ]
    result = reconcile(rows, {"Austin": 100})
    assert result.unique_registrations == 2  # neither can be matched to the other
    assert result.duplicates_merged == 0


def test_conflicting_campus_fails_closed() -> None:
    """Same identity, DIFFERENT campus ⇒ held for review, counted toward NEITHER (INV-4)."""
    rows = [
        _reg("site-1", "summer_site", "Austin", email="x@example.invalid"),
        _reg("form-1", "registration_form", "Dallas", email="x@example.invalid"),
    ]
    result = reconcile(rows, {"Austin": 100, "Dallas": 100})

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.campuses == ("Austin", "Dallas")
    # Held out of EVERY campus count — no silent merge into either.
    assert result.total_registered == 0
    assert all(c.registered == 0 for c in result.per_campus)


def test_per_campus_rollup_sums_to_capacity() -> None:
    """The four campus capacities roll up to the fixed total; registered ≤ capacity."""
    rows, capacities = generate_summer_dataset()
    result = reconcile(rows, capacities)

    assert sum(c.capacity for c in result.per_campus) == TOTAL_CAPACITY == 350
    assert result.total_capacity == 350
    for c in result.per_campus:
        assert 0 <= c.registered <= c.capacity
        assert c.seats_remaining == c.capacity - c.registered


def test_synthetic_no_double_count_proof() -> None:
    """The synthetic overlap is real: raw union > unique, and the difference is merged."""
    site, form = generate_summer_sources()
    rows = [*site, *form]
    result = reconcile(rows, dict(CAMPUS_CAPACITY))

    expected_unique = sum(_REGISTERED_TARGET.values())  # 288
    assert result.unique_registrations == expected_unique == 288
    assert result.raw_source_rows == len(site) + len(form)
    # Overlap MUST exist or the dedup test is vacuous.
    assert result.raw_source_rows > result.unique_registrations
    # With no campus conflicts in the synthetic set, every extra appearance is merged.
    assert result.conflicts == ()
    assert result.duplicates_merged == result.raw_source_rows - result.unique_registrations
    assert result.total_registered == expected_unique


def test_paid_vs_lead_split() -> None:
    """Paid totals match the synthetic target; lead = registered - paid per campus."""
    rows, capacities = generate_summer_dataset()
    result = reconcile(rows, capacities)

    assert result.total_paid == sum(_PAID_TARGET.values()) == 219
    assert result.total_lead == result.total_registered - result.total_paid
    for c in result.per_campus:
        assert c.lead == c.registered - c.paid
        assert c.paid <= c.registered


def test_program_id_is_summer_camp() -> None:
    """The reconcile is isolated to the summer_camp program (Phase-1 isolation)."""
    rows, capacities = generate_summer_dataset()
    assert reconcile(rows, capacities).program_id == PROGRAM_ID == "summer_camp"


def test_deterministic() -> None:
    """Same seed ⇒ identical sources (determinism, CLAUDE.md §4.1)."""
    assert generate_summer_sources(seed=42) == generate_summer_sources(seed=42)

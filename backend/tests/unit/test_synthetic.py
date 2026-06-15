"""Synthetic data generator tests (S0; FR-1.2, NFR-1, INV-1; THREAT_MODEL.md §5.2).

`app.data.synthetic.generate(n, seed)` is the **only seed writer** (ARCHITECTURE.md
§1.3): it produces the in-memory dataset that the runtime store is seeded from
(ASSUMPTIONS.md A-3 — no live Supabase locally). These tests pin three properties:

  1. it yields ``n`` `FamilyRecord`s each joined by ``family_id`` to its four
     source-table rows (LeadsNew/AppForm/EnrollmentForms/CommunityProfile), and
     is **deterministic** under a fixed seed (same seed ⇒ identical output);
  2. it never emits the C-SYN-2 real-PII cluster signature — emails/phones carry
     synthetic markers, names are obviously-synthetic household labels, incomes
     are absent (no ``household_income`` field), and precise geo is absent
     (aggregate ``region`` label only, §4.2);
  3. it scales to 5,000 families (NFR-9) without manual pagination.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    LeadsNew,
)
from app.data.synthetic import SyntheticDataset, generate, generate_back_to_school


def _all_field_values(obj: object) -> list[object]:
    """Flatten every scalar field value of a pydantic/dataclass-ish row."""
    if hasattr(obj, "model_dump"):
        return list(obj.model_dump().values())  # type: ignore[attr-defined]
    if is_dataclass(obj) and not isinstance(obj, type):
        return [getattr(obj, f.name) for f in fields(obj)]
    return [obj]


def test_generates_n_families_with_joined_source_rows() -> None:
    """`generate(n=50, seed=…)` ⇒ 50 families, each joined to 4 source rows, deterministic."""
    ds: SyntheticDataset = generate(n=50, seed=1234)

    # Exactly n families, each a FamilyRecord (§4.1 spine).
    assert len(ds.families) == 50
    assert all(isinstance(fam, FamilyRecord) for fam in ds.families)

    # Each of the four source tables has exactly one row per family, joined by family_id.
    assert len(ds.leads) == 50
    assert len(ds.app_forms) == 50
    assert len(ds.enrollment_forms) == 50
    assert len(ds.community_profiles) == 50

    assert all(isinstance(r, LeadsNew) for r in ds.leads)
    assert all(isinstance(r, AppForm) for r in ds.app_forms)
    assert all(isinstance(r, EnrollmentForms) for r in ds.enrollment_forms)
    assert all(isinstance(r, CommunityProfile) for r in ds.community_profiles)

    # The join keys line up: every family_id appears once in each source table, and
    # the spine carries the matching FK back to each source row.
    family_ids = {fam.family_id for fam in ds.families}
    assert len(family_ids) == 50  # unique family ids

    for table in (ds.leads, ds.app_forms, ds.enrollment_forms, ds.community_profiles):
        assert {row.family_id for row in table} == family_ids

    leads_by_family = {r.family_id: r for r in ds.leads}
    apps_by_family = {r.family_id: r for r in ds.app_forms}
    enroll_by_family = {r.family_id: r for r in ds.enrollment_forms}
    profiles_by_family = {r.family_id: r for r in ds.community_profiles}

    for fam in ds.families:
        assert fam.lead_id == leads_by_family[fam.family_id].lead_id
        assert fam.app_form_id == apps_by_family[fam.family_id].app_form_id
        assert fam.enrollment_form_id == enroll_by_family[fam.family_id].enrollment_form_id
        assert fam.community_profile_id == profiles_by_family[fam.family_id].community_profile_id

    # Deterministic under a fixed seed: same seed ⇒ identical output.
    ds_again: SyntheticDataset = generate(n=50, seed=1234)
    assert [f.model_dump() for f in ds.families] == [f.model_dump() for f in ds_again.families]
    assert [r.model_dump() for r in ds.leads] == [r.model_dump() for r in ds_again.leads]

    # A different seed ⇒ a different dataset (the seed actually drives generation).
    ds_other: SyntheticDataset = generate(n=50, seed=9999)
    assert [f.model_dump() for f in ds.families] != [f.model_dump() for f in ds_other.families]


def test_no_pii_shaped_values_in_output() -> None:
    """No row co-occurs the C-SYN-2 signature; emails/phones carry synthetic markers."""
    ds: SyntheticDataset = generate(n=200, seed=77)

    # Every synthetic email ends @example.invalid (the recognised synthetic marker).
    emails = [ds_fam.primary_contact_synthetic_email for ds_fam in ds.families]
    emails += [lead.synthetic_email for lead in ds.leads]
    assert emails, "expected synthetic emails to assert on"
    for email in emails:
        assert email.endswith("@example.invalid"), email

    # Every synthetic phone is in a clearly-fake 555-01xx range (NANP fictitious block).
    for lead in ds.leads:
        assert "555-01" in lead.synthetic_phone, lead.synthetic_phone

    # No row carries a household_income field at all (the C-SYN-2 cluster needs it).
    all_rows: list[object] = [
        *ds.families,
        *ds.leads,
        *ds.app_forms,
        *ds.enrollment_forms,
        *ds.community_profiles,
    ]
    for row in all_rows:
        assert "household_income" not in type(row).model_fields  # type: ignore[attr-defined]

    # Names are obviously-synthetic household labels ("The <Surname> Family"), not
    # bare "First Last" personal names — defuses the name component of C-SYN-2.
    for fam in ds.families:
        assert fam.display_name.startswith("The "), fam.display_name
        assert fam.display_name.endswith(" Family"), fam.display_name

    # Region is an aggregate label only — no ZIP / precise geo of minors (§4.2, P-4).
    import re

    zip_re = re.compile(r"\b\d{5}(?:-\d{4})?\b")
    for lead in ds.leads:
        assert lead.region and not zip_re.search(lead.region), lead.region

    # Belt-and-braces: no 5-digit ZIP and no household_income token anywhere in any
    # serialised field value (the precise C-SYN-2 cluster must be impossible).
    for row in all_rows:
        for value in _all_field_values(row):
            text = str(value)
            assert "household_income" not in text.lower(), text


def test_scale_5000_families() -> None:
    """`generate(n=5000)` returns 5,000 joined families (NFR-9), no manual pagination."""
    ds: SyntheticDataset = generate(n=5000, seed=2026)
    assert len(ds.families) == 5000
    assert len(ds.leads) == 5000
    assert len(ds.app_forms) == 5000
    assert len(ds.enrollment_forms) == 5000
    assert len(ds.community_profiles) == 5000
    # Joins still hold at scale.
    assert {fam.family_id for fam in ds.families} == {r.family_id for r in ds.leads}


# --------------------------------------------------------------------------- #
# S12 W2 — the back-to-school volume cohort (A-21). A SEPARATE deterministic
# cohort drawn from its own RNG, so the default stream stays byte-identical.
# --------------------------------------------------------------------------- #
_BTS_KW = {
    "count": 120,
    "seed": 2024,
    "spike_year": 2025,
    "spike_month": 8,
    "spike_day": 24,
    "spike_share": 0.5,
    "spread_days": 21,
}


def test_back_to_school_is_deterministic() -> None:
    """Same seed/shape ⇒ byte-identical cohort (CLAUDE §4.1)."""
    a = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    b = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    assert len(a.families) == 120
    assert [f.model_dump() for f in a.families] == [f.model_dump() for f in b.families]
    assert [r.model_dump() for r in a.leads] == [r.model_dump() for r in b.leads]
    # Each of the four source tables has exactly one row per family (the join).
    fids = {f.family_id for f in a.families}
    assert len(fids) == 120
    for table in (a.leads, a.app_forms, a.enrollment_forms, a.community_profiles):
        assert {row.family_id for row in table} == fids


def test_back_to_school_does_not_perturb_default_stream() -> None:
    """The cohort draws from its OWN RNG ⇒ the default `generate` stream is untouched.

    The byte-determinism the June fixtures depend on holds even when the volume
    cohort is generated in the same process (A-21 — appended/isolated draws).
    """
    before = generate(n=24, seed=42)
    _ = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    after = generate(n=24, seed=42)
    assert [f.model_dump() for f in before.families] == [f.model_dump() for f in after.families]


def test_back_to_school_spike_day_concentration() -> None:
    """A `spike_share` fraction of the cohort stalls EXACTLY on the spike day.

    The spike families' ``stalled_since`` lands on 2025-08-24 (the single-day
    surge); the off-spike families spread across the band, so the spike day is the
    cohort's largest single-day cluster.
    """
    from collections import Counter

    ds = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    # Every family is an active stall ⇒ stalled_since is always set (the anchor).
    assert all(f.stalled_since is not None for f in ds.families)
    by_day = Counter(
        (f.stalled_since.year, f.stalled_since.month, f.stalled_since.day)
        for f in ds.families
        if f.stalled_since is not None
    )
    spike_count = by_day[(2025, 8, 24)]
    # spike_share=0.5 of 120 ⇒ at least ~60 on the spike day (off-spike families
    # may also random-land on it, so >=, never <).
    assert spike_count >= round(120 * 0.5)
    # The spike day is the single largest day cluster, by a wide margin over the
    # next-busiest day (the surge is a real spike, not a flat distribution).
    second_busiest = sorted(by_day.values(), reverse=True)[1]
    assert spike_count == max(by_day.values())
    assert spike_count > 2 * second_busiest


def test_back_to_school_families_are_active_stalls() -> None:
    """Every cohort family reads as an ACTIVE stall — never auto-RECOVERED (A-21).

    The cohort is meant to be a recovery surface, so no family may trip the
    recovery deriver's DETECTED-recovered signals: the stall reason's mapped
    stall-stage equals the current stage (no "advanced past"), forms are never
    fully cleared, and funding sits below the §5.4 first-installment floor. We
    assert the structural preconditions the deriver reads.
    """
    from app.data.models import FundingState, Stage, StallReason

    # The recovery deriver's stall_reason → stall_stage map (mirrors api/families).
    stall_stage = {
        StallReason.INFO_SESSION_NO_SHOW: Stage.INTEREST,
        StallReason.NO_RESPONSE: Stage.INTEREST,
        StallReason.APP_INCOMPLETE: Stage.APPLY,
        StallReason.FORMS_PARTIAL: Stage.ENROLL,
        StallReason.FUNDING_PENDING: Stage.ENROLL,
    }
    recovered_funding = {FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED}

    ds = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    enroll_by_family = {r.family_id: r for r in ds.enrollment_forms}
    for fam in ds.families:
        assert fam.stall_reason is not None
        # No stage-advance: the mapped stall stage equals the current stage.
        assert stall_stage[fam.stall_reason] == fam.current_stage
        # No funding-recovery: below the first-installment floor.
        assert fam.funding_state not in recovered_funding
        # No forms-cleared: not all six forms signed.
        forms = enroll_by_family[fam.family_id]
        assert forms.forms_signed < forms.forms_total


def test_back_to_school_is_synthetic_only() -> None:
    """The cohort carries the same synthetic markers as the default world (INV-1).

    PII-scan stays clean: synthetic emails (@example.invalid), 555-01xx phones,
    obviously-synthetic household names, aggregate region only, no household_income.
    """
    import re

    ds = generate_back_to_school(**_BTS_KW)  # type: ignore[arg-type]
    for fam in ds.families:
        assert fam.primary_contact_synthetic_email.endswith("@example.invalid")
        assert fam.display_name.startswith("The ") and fam.display_name.endswith(" Family")
    zip_re = re.compile(r"\b\d{5}(?:-\d{4})?\b")
    for lead in ds.leads:
        assert lead.synthetic_email.endswith("@example.invalid")
        assert "555-01" in lead.synthetic_phone
        assert lead.region and not zip_re.search(lead.region)
    all_rows: list[object] = [
        *ds.families,
        *ds.leads,
        *ds.app_forms,
        *ds.enrollment_forms,
        *ds.community_profiles,
    ]
    for row in all_rows:
        assert "household_income" not in type(row).model_fields  # type: ignore[attr-defined]

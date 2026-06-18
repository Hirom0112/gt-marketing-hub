"""Family Record + source-table model tests (S0; ARCHITECTURE.md §4.1–§4.5, §4.8).

These assert the Pydantic v2 contract for the `family_record` spine and the four
synthetic source tables, with the §4.8 enumerations enforced at the type level and
the NFR-1/INV-1 synthetic-only field naming (CLAUDE.md §1).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    LeadsNew,
    Student,
)


def _valid_family_record_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for a FamilyRecord (§4.1)."""
    return {
        "family_id": uuid4(),
        "display_name": "The Rivera Family",
        "primary_contact_synthetic_email": "rivera.synthetic@example.invalid",
        "current_stage": "interest",
        "funding_type": "tefa_standard",
        "attribution_source": "referral",
        "attribution_utm": {"utm_source": "newsletter", "utm_campaign": "spring"},
    }


def test_family_record_enums_and_attribution_funding() -> None:
    """FamilyRecord accepts §4.1 columns, enforces §4.8 enums, requires attribution."""
    record = FamilyRecord(**_valid_family_record_kwargs())  # type: ignore[arg-type]
    assert record.display_name == "The Rivera Family"
    # Enum-typed fields coerce the string inputs to the proper Enum members.
    assert record.current_stage.value == "interest"
    assert record.funding_type is not None
    assert record.funding_type.value == "tefa_standard"
    # attribution_utm is a jsonb-shaped dict field.
    assert record.attribution_utm == {"utm_source": "newsletter", "utm_campaign": "spring"}

    # Rejects an out-of-enum funding_type (allowed set is exactly §4.1).
    bad_funding = _valid_family_record_kwargs()
    bad_funding["funding_type"] = "scholarship"
    with pytest.raises(ValidationError):
        FamilyRecord(**bad_funding)  # type: ignore[arg-type]

    # Rejects an out-of-enum current_stage (interest|apply|enroll|tuition).
    bad_stage = _valid_family_record_kwargs()
    bad_stage["current_stage"] = "graduated"
    with pytest.raises(ValidationError):
        FamilyRecord(**bad_stage)  # type: ignore[arg-type]

    # Requires attribution_source (FR-1.4).
    no_source = _valid_family_record_kwargs()
    del no_source["attribution_source"]
    with pytest.raises(ValidationError):
        FamilyRecord(**no_source)  # type: ignore[arg-type]

    # Requires attribution_utm (FR-1.4).
    no_utm = _valid_family_record_kwargs()
    del no_utm["attribution_utm"]
    with pytest.raises(ValidationError):
        FamilyRecord(**no_utm)  # type: ignore[arg-type]


def test_synthetic_email_field_named_synthetic() -> None:
    """Synthetic naming per NFR-1 / INV-1 (§4.1, §4.2)."""
    # FamilyRecord exposes the synthetic-named primary contact email (§4.1).
    assert "primary_contact_synthetic_email" in FamilyRecord.model_fields
    record = FamilyRecord(**_valid_family_record_kwargs())  # type: ignore[arg-type]
    assert record.primary_contact_synthetic_email == "rivera.synthetic@example.invalid"

    # leads_new uses synthetic_* naming for all PII-shaped fields (§4.2).
    for field in (
        "synthetic_first_name",
        "synthetic_last_name",
        "synthetic_email",
        "synthetic_phone",
    ):
        assert field in LeadsNew.model_fields, field

    lead = LeadsNew(
        lead_id=uuid4(),
        family_id=uuid4(),
        synthetic_first_name="Maria",
        synthetic_last_name="Rivera",
        synthetic_email="maria.synthetic@example.invalid",
        synthetic_phone="+1-555-0100",
        source="referral",
        utm={"utm_source": "newsletter"},
        product_interest="campus",
        grade_interest="K",
        region="Southwest",
    )
    assert lead.synthetic_first_name == "Maria"
    assert lead.product_interest.value == "campus"


def test_family_record_carries_both_household_guardians() -> None:
    """A-36: BOTH parents live on the ONE household, synthetic, never child-keyed."""
    # The guardian columns exist on the spine (mirrors migration 0022).
    for field in (
        "guardian_1_relationship",
        "secondary_contact_name",
        "secondary_contact_synthetic_email",
        "guardian_2_relationship",
    ):
        assert field in FamilyRecord.model_fields, field

    # They default to None (nullable — a row predating the field stays valid).
    bare = FamilyRecord(**_valid_family_record_kwargs())  # type: ignore[arg-type]
    assert bare.guardian_1_relationship is None
    assert bare.secondary_contact_synthetic_email is None

    # A household listing two guardians round-trips both on the SAME family_id
    # (household-grained, INV-6 — no student_id anywhere).
    with_two = FamilyRecord(
        **_valid_family_record_kwargs(),  # type: ignore[arg-type]
        guardian_1_relationship="mother",
        secondary_contact_name="Birch Rivera",
        secondary_contact_synthetic_email="birch.rivera.synthetic@example.invalid",
        guardian_2_relationship="father",
    )
    assert with_two.guardian_1_relationship == "mother"
    assert with_two.guardian_2_relationship == "father"
    assert with_two.secondary_contact_synthetic_email.endswith("@example.invalid")


def test_source_table_models_present() -> None:
    """The four §4.2–§4.5 source-table models exist and accept their columns."""
    family_id = uuid4()

    app_form = AppForm(app_form_id=uuid4(), family_id=family_id)
    assert app_form.app_form_id is not None

    forms = EnrollmentForms(enrollment_form_id=uuid4(), family_id=family_id)
    # §4.4: forms_total defaults to the six-form gauntlet.
    assert forms.forms_total == 6

    profile = CommunityProfile(community_profile_id=uuid4(), family_id=family_id)
    assert profile.community_profile_id is not None


# ---------------------------------------------------------------------------
# A-24 — per-child Student rows: one application per child, each its own funnel.
# ---------------------------------------------------------------------------


def _valid_student_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for a Student (A-24)."""
    return {
        "student_id": uuid4(),
        "family_id": uuid4(),
        "display_label": "Rivera household — Alex · Grade 3",
        "synthetic_first_name": "Alex",
        "grade": "3",
        "current_stage": "enroll",
        "funding_type": "tefa_standard",
    }


def test_student_carries_its_own_funnel_and_distinct_label() -> None:
    """A Student owns a full per-child funnel + a distinct display label (A-24)."""
    student = Student(**_valid_student_kwargs())  # type: ignore[arg-type]

    # Per-child funnel state — the whole point of A-24: each child its own funnel.
    assert student.current_stage.value == "enroll"
    assert student.funding_state is FundingState.NONE  # derived default (§5.4)
    assert student.stall_reason is None
    # One application + one enrollment packet PER STUDENT (default unset).
    assert "app_form_id" in Student.model_fields
    assert "enrollment_form_id" in Student.model_fields

    # The distinct per-student label (also disambiguates same-surname households).
    assert student.display_label == "Rivera household — Alex · Grade 3"

    # Child identity is synthetic-named (NFR-1 / INV-1).
    assert "synthetic_first_name" in Student.model_fields
    assert student.synthetic_first_name == "Alex"

    # Enforces §4.8 enums on the per-student funnel fields.
    bad_stage = _valid_student_kwargs()
    bad_stage["current_stage"] = "graduated"
    with pytest.raises(ValidationError):
        Student(**bad_stage)  # type: ignore[arg-type]


def test_app_form_and_enrollment_are_keyed_per_student() -> None:
    """One application + one enrollment packet PER STUDENT (A-24)."""
    family_id = uuid4()
    student_id = uuid4()

    # AppForm/EnrollmentForms accept a student_id (the per-child key). Optional so
    # pre-A-24 family-only fixtures stay valid (non-breaking).
    app_form = AppForm(app_form_id=uuid4(), family_id=family_id, student_id=student_id)
    assert app_form.student_id == student_id

    forms = EnrollmentForms(enrollment_form_id=uuid4(), family_id=family_id, student_id=student_id)
    assert forms.student_id == student_id

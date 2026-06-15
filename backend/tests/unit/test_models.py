"""Family Record + source-table model tests (S0; ARCHITECTURE.md §4.1–§4.5, §4.8).

These assert the Pydantic v2 contract for the `family_record` spine and the four
synthetic source tables, with the §4.8 enumerations enforced at the type level and
the NFR-1/INV-1 synthetic-only field naming (CLAUDE.md §1).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    LeadsNew,
)
from pydantic import ValidationError


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

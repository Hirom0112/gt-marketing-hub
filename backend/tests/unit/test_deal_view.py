"""S1 deal-view projection tests (ARCHITECTURE.md §4.1/§4.3/§4.7, §6; FR-2.2).

`GET /families/{id}` exposes the *deal view*: a flat, operator-facing projection
over a :class:`JoinedFamily` (the spine joined to its four source rows). These
tests pin the FR-2.2 field set `assemble_deal_view` must surface and assert each
field is **correctly sourced** from the underlying rows:

- profile — `display_name` + synthetic contact, from the spine row;
- `stall_reason` / `funding_type` — deterministic spine columns (§4.1, §4.8);
- `attribution_source` + `attribution_utm` — the FR-1.4 attribution pair;
- `map_score` + `academic_signals` — the §4.3 `app_form` academic signals;
- `crm_seam_status` — derived via the §4.7 seam deriver (`core/seam.py`).

Pure unit: no I/O, no adapters, no LLM — only the models + the projection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.family_record import assemble_deal_view

from app.core.seam import MirrorState
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingType,
    LeadsNew,
    ProductInterest,
    SeamStatus,
    Stage,
    StallReason,
)
from app.data.repository import JoinedFamily

# Fixed instants so the seam derivation is exact and reproducible.
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # local last-touched baseline.
_AFTER = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)  # one hour later (a clean push).


def _joined_family() -> JoinedFamily:
    """A JoinedFamily fixture exercising every FR-2.2 deal-view field."""
    family_id = uuid4()
    family = FamilyRecord(
        family_id=family_id,
        display_name="The Rivera Family",
        primary_contact_synthetic_email="rivera.synthetic@example.invalid",
        current_stage=Stage.ENROLL,
        stall_reason=StallReason.FORMS_PARTIAL,
        funding_type=FundingType.TEFA_STANDARD,
        attribution_source="referral",
        attribution_utm={"utm_source": "newsletter", "click_id": "clk_abc123"},
        crm_seam_status=SeamStatus.UNSYNCED,  # seeded value — the deriver is the source of truth.
        crm_synced_at=_AFTER,
        updated_at=_T0,
    )
    lead = LeadsNew(
        lead_id=uuid4(),
        family_id=family_id,
        synthetic_first_name="Jordan",
        synthetic_last_name="Rivera",
        synthetic_email="rivera.synthetic@example.invalid",
        synthetic_phone="555-0142",
        source="referral",
        utm={"utm_source": "newsletter"},
        product_interest=ProductInterest.CAMPUS,
        grade_interest="3",
        region="Northeast",
    )
    app_form = AppForm(
        app_form_id=uuid4(),
        family_id=family_id,
        submitted_at=_T0,
        completion_pct=100.0,
        map_score=212.5,
        academic_signals={"reading_percentile": 78, "math_percentile": 84},
    )
    enrollment = EnrollmentForms(
        enrollment_form_id=uuid4(),
        family_id=family_id,
        forms_total=6,
        forms_signed=3,
    )
    profile = CommunityProfile(
        community_profile_id=uuid4(),
        family_id=family_id,
        engagement_signals={"events_attended": 2},
        referral_network={"referrals_made": 1},
    )
    return JoinedFamily(
        family=family,
        lead=lead,
        app_form=app_form,
        enrollment_forms=enrollment,
        community_profile=profile,
    )


def test_deal_view_projection() -> None:
    """`assemble_deal_view` surfaces the FR-2.2 field set, each correctly sourced."""
    joined = _joined_family()
    # Mirror agrees on the tracked field (stage) ⇒ status is the timestamp rule.
    view = assemble_deal_view(
        joined,
        mirror=MirrorState(stage=Stage.ENROLL, mirror_updated_at=_AFTER),
    )

    # --- profile: display_name + synthetic contact, from the spine row. ---
    assert view.family_id == joined.family.family_id
    assert view.display_name == "The Rivera Family"
    assert view.primary_contact_synthetic_email == "rivera.synthetic@example.invalid"

    # --- stall_reason + funding_type: deterministic spine columns (§4.1). ---
    assert view.stall_reason is StallReason.FORMS_PARTIAL
    assert view.funding_type is FundingType.TEFA_STANDARD

    # --- attribution pair (FR-1.4). ---
    assert view.attribution_source == "referral"
    assert view.attribution_utm == {"utm_source": "newsletter", "click_id": "clk_abc123"}

    # --- academic signals from the §4.3 app_form. ---
    assert view.map_score == 212.5
    assert view.academic_signals == {"reading_percentile": 78, "math_percentile": 84}

    # --- crm_seam_status DERIVED via the §4.7 seam deriver, not the seeded value:
    #     crm_synced_at (_AFTER) >= updated_at (_T0) and mirror agrees ⇒ synced,
    #     even though the spine row was seeded `unsynced`. The deriver wins. ---
    assert view.crm_seam_status is SeamStatus.SYNCED


def test_deal_view_handles_missing_app_form() -> None:
    """An interest-stage family with no app_form yields null academic signals."""
    joined = _joined_family()
    interest = JoinedFamily(
        family=joined.family,
        lead=joined.lead,
        app_form=None,
        enrollment_forms=joined.enrollment_forms,
        community_profile=joined.community_profile,
    )
    view = assemble_deal_view(interest)
    assert view.map_score is None
    assert view.academic_signals == {}

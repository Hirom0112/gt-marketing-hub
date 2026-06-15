"""Pydantic response schemas for the read API (ARCHITECTURE.md §6).

All read-only (INV-2 for S0). These shape the GET responses for the landing
dashboard: per-stage pipeline counts (FR-2.1) and the basic joined Family Record
deal view (FR-2.2 — the full deal view lands in S1).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    LeadsNew,
    Stage,
)


class PipelineResponse(BaseModel):
    """Per-stage pipeline tally (FR-2.1).

    ``counts`` is keyed by the §4.8 funnel stage (interest/apply/enroll/tuition),
    every stage present (zero-filled). ``total`` is the family total, == the sum
    of ``counts`` — handy for the dashboard's "of N families" copy.
    """

    counts: dict[Stage, int]
    total: int


class FamilyDetailResponse(BaseModel):
    """A spine row joined to its four source rows (FR-2.2, basic).

    The full deal view (notes timeline, funding installments) arrives in S1; this
    is the read-only landing-slice projection.
    """

    family: FamilyRecord
    lead: LeadsNew | None
    app_form: AppForm | None
    enrollment_forms: EnrollmentForms | None
    community_profile: CommunityProfile | None

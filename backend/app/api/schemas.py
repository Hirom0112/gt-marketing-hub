"""Pydantic response schemas for the read API (ARCHITECTURE.md §6).

All read-only (INV-2 for S0). These shape the GET responses for the landing
dashboard: per-stage pipeline counts (FR-2.1) and the basic joined Family Record
deal view (FR-2.2 — the full deal view lands in S1).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.family_record import DealView
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    LeadsNew,
    SeamStatus,
    Stage,
)


class PipelineResponse(BaseModel):
    """Per-stage pipeline tally + CRM-seam summary (FR-2.1, FR-2.6).

    ``counts`` is keyed by the §4.8 funnel stage (interest/apply/enroll/tuition),
    every stage present (zero-filled). ``total`` is the family total, == the sum
    of ``counts`` — handy for the dashboard's "of N families" copy. ``seam`` is
    the §4.7 Supabase↔HubSpot seam summary (synced/unsynced/conflict), surfaced
    read-only on the landing dashboard.
    """

    counts: dict[Stage, int]
    total: int
    seam: dict[SeamStatus, int]


class FamilyDetailResponse(BaseModel):
    """A spine row joined to its four source rows, plus the FR-2.2 deal view (§6).

    `GET /families/{id}` stays the "full joined Family Record" (§6): the spine
    and its four source rows are kept (the S0 contract). S1 adds ``deal_view`` —
    the flat operator-facing FR-2.2 projection from
    :func:`app.core.family_record.assemble_deal_view` over the joined rows
    (stall reason, funding tier, MAP score, attribution, derived seam status).
    """

    family: FamilyRecord
    lead: LeadsNew | None
    app_form: AppForm | None
    enrollment_forms: EnrollmentForms | None
    community_profile: CommunityProfile | None
    deal_view: DealView


class WorkQueueItem(BaseModel):
    """One ranked work-queue row (FR-2.5; §6 `GET /work-queue`).

    The deterministic deal card the queue UI renders: family identity plus the
    score and its two interpretable components (``recoverability``, ``value``)
    so an operator sees *why* a family ranks where it does. Computed entirely by
    the pure :mod:`app.core.work_queue` scorer — never by an LLM (INV-2).
    """

    family_id: UUID
    display_name: str
    current_stage: Stage
    score: float
    recoverability: float
    value: float

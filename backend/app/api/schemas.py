"""Pydantic response schemas for the read API (ARCHITECTURE.md §6).

All read-only (INV-2 for S0). These shape the GET responses for the landing
dashboard: per-stage pipeline counts (FR-2.1) and the basic joined Family Record
deal view (FR-2.2 — the full deal view lands in S1).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.schemas.enrollment_draft import DraftAction, EnrollmentDraftProposal
from app.core.eval_gate import ValidationResult
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
from app.observability.log_store import (
    DecisionAction,
    DecisionRecord,
    EvalRecord,
    ProposalRecord,
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


# --------------------------------------------------------------------------- #
# S2 AI action surface (FR-2.4; ARCH §5.2/§6; INV-2/INV-3/INV-4).
# --------------------------------------------------------------------------- #
class DraftRequest(BaseModel):
    """`POST /ai/enrollment/draft` body — which family + which channel (§6)."""

    family_id: UUID
    action: DraftAction


class DraftResponse(BaseModel):
    """The §5.2 draft outcome surfaced to the client (INV-3/INV-4 at the boundary).

    The proposal body is surfaced **only** when ``surfaced`` is True (the eval
    passed). On a block/degrade ``proposal`` is ``None`` (no usable body to act
    on — the UI offers the deterministic template fallback) but ``proposal_id``
    is always present so the client can call the decision endpoint, and
    ``failed_rules`` carries the gate's reasons for the audit-aware UI.
    """

    proposal_id: UUID
    surfaced: bool
    degraded: bool
    failed_rules: list[str] = Field(default_factory=list)
    proposal: EnrollmentDraftProposal | None = None
    validation: ValidationResult | None = None


class DecisionRequest(BaseModel):
    """`POST /proposals/{id}/decision` body — the human verdict (§6; §4.9).

    ``edited_payload`` carries the human's edits when ``action`` is ``edit``; it
    is ignored for approve/discard.
    """

    action: DecisionAction
    edited_payload: dict[str, object] | None = None


class DecisionResponse(BaseModel):
    """The decision result — the ONLY state-applying path (INV-2; NFR-6).

    On ``approve`` a SIMULATED send was recorded (``send_simulated`` True) and
    ``seam_status`` carries the recomputed §4.7 seam; on edit/discard there is no
    send and ``seam_status`` is ``None``.
    """

    proposal_id: UUID
    action: DecisionAction
    send_simulated: bool = False
    seam_status: SeamStatus | None = None


class AuditResponse(BaseModel):
    """The §10 audit view for one proposal — proposal + its evals + decisions (NFR-6)."""

    proposal: ProposalRecord
    evals: list[EvalRecord] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)

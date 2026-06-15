"""Read-only Family / pipeline endpoints (ARCHITECTURE.md §6; FR-2.1/2.2).

All GET, all deterministic, no AI (INV-2 for the S0 landing slice). Every route
reads through the :class:`FamilyRepository` seam (`deps.get_repository`), so the
store is swappable to Supabase with zero changes here (NFR-8).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_params, get_repository
from app.api.schemas import FamilyDetailResponse, PipelineResponse, WorkQueueItem
from app.core.family_record import assemble_deal_view
from app.core.params import Params
from app.core.work_queue import (
    WorkQueueFamily,
    rank_families,
    recoverability,
    responsiveness_from_engagement,
    score_family,
    value,
)
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import FamilyRepository, JoinedFamily

router = APIRouter(tags=["families"])

# The store seam, injected via Annotated so the call sits in the type, not a
# default-arg call (avoids ruff B008; the idiomatic FastAPI dependency style).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
# The typed §8 params seam, injected the same way (INV-11 — every tunable here).
ParamsDep = Annotated[Params, Depends(get_params)]


def _work_queue_family(joined: JoinedFamily, params: Params) -> WorkQueueFamily:
    """Project a joined family down to the scorer's pure input (FR-2.5; §5.1).

    Responsiveness is derived from the aggregate ``community_profile``
    engagement signals (A-5) — the spine carries no normalized responsiveness —
    via the pure :func:`responsiveness_from_engagement` helper. Everything else
    reads straight off the spine row.
    """
    signals = joined.community_profile.engagement_signals if joined.community_profile else {}
    return WorkQueueFamily(
        family_id=joined.family.family_id,
        current_stage=joined.family.current_stage,
        stalled_since=joined.family.stalled_since,
        responsiveness=responsiveness_from_engagement(signals, params),
        funding_type=joined.family.funding_type,
    )


@router.get("/pipeline", response_model=PipelineResponse)
def get_pipeline(repository: RepositoryDep) -> PipelineResponse:
    """Per-stage pipeline counts + CRM-seam summary (FR-2.1, FR-2.6)."""
    counts = repository.pipeline_counts()
    # Seam summary derived through the same store seam (every status zero-filled).
    seam = {status: len(repository.list_families(seam_status=status)) for status in SeamStatus}
    return PipelineResponse(counts=counts, total=sum(counts.values()), seam=seam)


@router.get("/families", response_model=list[FamilyRecord])
def list_families(
    repository: RepositoryDep,
    params: ParamsDep,
    stage: Stage | None = None,
    funding_state: FundingState | None = None,
    seam_status: SeamStatus | None = None,
    min_score: float | None = None,
) -> list[FamilyRecord]:
    """List Family Records, filtered by stage / funding_state / seam_status / score (FR-2.1).

    ``min_score`` (§6) keeps only families whose deterministic work-queue score
    (FR-2.5) is ≥ the threshold — the queue's score gate surfaced as a list
    filter. The spine carries no responsiveness, so scoring needs the join; when
    ``min_score`` is set the cohort is read via ``list_joined`` and scored
    through the same pure scorer as ``/work-queue`` (one source of truth), then
    the column-level filters are re-applied.
    """
    if min_score is None:
        return repository.list_families(
            stage=stage,
            funding_state=funding_state,
            seam_status=seam_status,
        )
    scored_ids = {
        joined.family.family_id
        for joined in repository.list_joined()
        if score_family(_work_queue_family(joined, params), params) >= min_score
    }
    return [
        family
        for family in repository.list_families(
            stage=stage,
            funding_state=funding_state,
            seam_status=seam_status,
        )
        if family.family_id in scored_ids
    ]


@router.get("/families/{family_id}", response_model=FamilyDetailResponse)
def get_family(family_id: UUID, repository: RepositoryDep) -> FamilyDetailResponse:
    """Full joined Family Record — spine + four source rows + FR-2.2 deal view (§6).

    Stays the "full joined Family Record" (§6): the spine and its four source
    rows are returned as before (the S0 contract), enriched with ``deal_view`` —
    the flat operator projection from :func:`assemble_deal_view` over the same
    joined rows. Pure projection, no AI (INV-2).
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    return FamilyDetailResponse(
        family=joined.family,
        lead=joined.lead,
        app_form=joined.app_form,
        enrollment_forms=joined.enrollment_forms,
        community_profile=joined.community_profile,
        deal_view=assemble_deal_view(joined),
    )


@router.get("/work-queue", response_model=list[WorkQueueItem])
def get_work_queue(repository: RepositoryDep, params: ParamsDep) -> list[WorkQueueItem]:
    """Ranked work queue, highest deterministic score first (FR-2.5; §6).

    Reads the cohort joined (for the aggregate responsiveness signal — A-5),
    projects each family to the scorer's pure input, and delegates ordering to
    :func:`app.core.work_queue.rank_families` — the router never re-implements
    ranking. Each row carries the score plus its ``recoverability`` / ``value``
    components so the UI can show why a family ranks where it does. No AI (INV-2).
    """
    joined_by_id: dict[UUID, JoinedFamily] = {
        joined.family.family_id: joined for joined in repository.list_joined()
    }
    queue_families = [_work_queue_family(joined, params) for joined in joined_by_id.values()]
    ranked = rank_families(queue_families, params)
    return [
        WorkQueueItem(
            family_id=family.family_id,
            display_name=joined_by_id[family.family_id].family.display_name,
            current_stage=family.current_stage,
            score=score_family(family, params),
            recoverability=recoverability(family, params),
            value=value(family, params),
        )
        for family in ranked
    ]

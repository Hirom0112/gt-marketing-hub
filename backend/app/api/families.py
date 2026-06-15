"""Read-only Family / pipeline endpoints (ARCHITECTURE.md §6; FR-2.1/2.2).

All GET, all deterministic, no AI (INV-2 for the S0 landing slice). Every route
reads through the :class:`FamilyRepository` seam (`deps.get_repository`), so the
store is swappable to Supabase with zero changes here (NFR-8).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_repository
from app.api.schemas import FamilyDetailResponse, PipelineResponse
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import FamilyRepository

router = APIRouter(tags=["families"])

# The store seam, injected via Annotated so the call sits in the type, not a
# default-arg call (avoids ruff B008; the idiomatic FastAPI dependency style).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]


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
    stage: Stage | None = None,
    funding_state: FundingState | None = None,
    seam_status: SeamStatus | None = None,
) -> list[FamilyRecord]:
    """List Family Records, optionally filtered by stage / funding_state / seam_status (FR-2.1)."""
    return repository.list_families(
        stage=stage,
        funding_state=funding_state,
        seam_status=seam_status,
    )


@router.get("/families/{family_id}", response_model=FamilyDetailResponse)
def get_family(family_id: UUID, repository: RepositoryDep) -> FamilyDetailResponse:
    """Full joined Family Record — spine + four source rows (FR-2.2, basic)."""
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    return FamilyDetailResponse(
        family=joined.family,
        lead=joined.lead,
        app_form=joined.app_form,
        enrollment_forms=joined.enrollment_forms,
        community_profile=joined.community_profile,
    )

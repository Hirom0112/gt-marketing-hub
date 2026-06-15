"""Deterministic enrollment write-action endpoints (S10 W3; ARCH §7.1; INV-2/9).

The "Seed to HubSpot" action — the one place a synthetic family is PUSHED across
the ``CRMAdapter`` seam from a deterministic, human-triggered route (a button
click), never from ``app/ai``. It is mode-agnostic: under ``CRM_MODE=simulate``
the recorder records the push (INV-9); under ``CRM_MODE=live`` the production
adapter writes a Contact + Deal into the real HubSpot portal behind the four
guards (ANALYSIS/hubspot-complement-plan.md §3). INV-2 holds: this route is the
deterministic core's composition layer — it imports the adapter seam, the AI
edge never does (the §8.4 import-walk test guards that for the live adapter).

  ``POST /enrollment/families/{family_id}/seed``
    1. load the family (404 if unknown);
    2. ``adapter.push_family(record)`` — the write-shaped seam op (§7.1);
    3. advance ``crm_synced_at`` and re-derive the §4.7 seam so it flips
       ``unsynced → synced`` (derive-and-return per A-7 — the read-only A-3 store
       is not mutated);
    4. return ``{family_id, simulated, deal_id, stage, seam_status}``; ``deal_id``
       is the adapter's ``recorded_id`` — under ``CRM_MODE=live`` the live HubSpot
       deal id, the cockpit's proof-of-capture.

This module is the composition layer (it imports ``app.adapters``); ``app/core/``
stays pure. No LLM call is ever made here — seeding is fully deterministic.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import get_crm_adapter_dep, get_repository
from app.api.schemas import SeedResponse
from app.core.seam import MirrorState, derive_seam_status
from app.data.repository import FamilyRepository

router = APIRouter(tags=["enrollment"])

# Dependency aliases (Annotated keeps the call in the type, not a default arg —
# ruff B008; the idiomatic FastAPI style matching the other routers).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]


@router.post("/enrollment/families/{family_id}/seed", response_model=SeedResponse)
def seed_family_to_crm(
    family_id: UUID,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
) -> SeedResponse:
    """Push a synthetic family across the CRM seam and re-derive the seam (S10 W3).

    404 if the family is unknown. Otherwise ``push_family`` writes (live) or
    records (simulated) the Contact + Deal; the §4.7 seam is then recomputed
    against the post-push state — ``crm_synced_at`` advanced to ``updated_at`` and
    the mirror reflecting the pushed stage — so it derives ``synced``
    (derive-and-return per A-7; the read-only store is not mutated). The returned
    ``deal_id`` is the adapter's ``recorded_id`` (the live HubSpot deal id under
    ``CRM_MODE=live``) — the cockpit's proof the family was captured.
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    record = joined.family

    # The write-shaped seam op (§7.1): live pushes a Contact+Deal, simulate
    # records the push. The SOLE caller on this deterministic route (INV-2).
    sync = crm_adapter.push_family(record)

    # Derive-and-return the post-push §4.7 seam (A-7): the push synced local state
    # into the CRM, so crm_synced_at advances to updated_at and the mirror now
    # holds the pushed stage. derive_seam_status then yields `synced`. The
    # read-only A-3 store is not mutated — the seam is derived for the response.
    synced_record = record.model_copy(update={"crm_synced_at": record.updated_at})
    mirror = MirrorState(stage=sync.stage, mirror_updated_at=record.updated_at)
    seam_status = derive_seam_status(synced_record, mirror)

    return SeedResponse(
        family_id=family_id,
        simulated=sync.simulated,
        deal_id=sync.recorded_id,
        contact_id=sync.contact_id,
        stage=sync.stage,
        seam_status=seam_status,
    )

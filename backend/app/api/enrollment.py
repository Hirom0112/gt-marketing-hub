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

from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import get_crm_adapter_dep, get_observability_log, get_repository
from app.api.schemas import (
    BulkAssignCounts,
    BulkAssignRequest,
    BulkAssignResponse,
    BulkDismissCounts,
    BulkDismissRequest,
    BulkDismissResponse,
    BulkSeedCaptured,
    BulkSeedCounts,
    BulkSeedRequest,
    BulkSeedResponse,
    SeedResponse,
)
from app.core import sales_agents
from app.core.seam import MirrorState, derive_seam_status
from app.data.repository import FamilyRepository
from app.observability.log_store import DecisionAction, ObservabilityLog

# The deterministic-assignment audit flow tag (NFR-6) — distinguishes these
# operator decisions from AI proposal flows on the shared spine. A constant, not
# a magic string (INV-11 spirit).
_ASSIGN_FLOW = "assignment"
_ASSIGN_SCHEMA_VERSION = "1"

router = APIRouter(tags=["enrollment"])

# Dependency aliases (Annotated keeps the call in the type, not a default arg —
# ruff B008; the idiomatic FastAPI style matching the other routers).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
# v1 has no auth; the operator is a fixed audit seam (A-3), mirroring ai_actions.
DEFAULT_HUMAN = "operator"


def _batch_id(prefix: str, family_ids: list[UUID]) -> str:
    """A deterministic ``batch_id`` tagging one bulk audit group (NFR-6; A-20).

    Derived (uuid5) from the prefix + the SORTED family ids so the same selection
    yields the same id — a stable correlation handle, not a second write path.
    """
    key = f"{prefix}:" + ",".join(sorted(str(fid) for fid in family_ids))
    return f"{prefix}-{uuid5(NAMESPACE_URL, key).hex}"


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


@router.post("/enrollment/families/bulk-seed", response_model=BulkSeedResponse)
def bulk_seed_families(
    request: BulkSeedRequest,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
) -> BulkSeedResponse:
    """Bulk-seed a selection — a THIN loop over the per-family seed path (A-20).

    NOT a new write path: each known family runs the SAME ``push_family`` through
    the SIMULATED CRM adapter (CRM_MODE=simulate — no live writes this run, A-17;
    INV-9). The seam is DERIVED from the post-push adapter mirror (A-7), never
    asserted ``synced``. Unknown family ids are skipped (a bulk selection is
    resilient — no 404 aborts the whole batch). One ``batch_id`` tags the audit
    group (NFR-6).
    """
    batch_id = _batch_id("bulk-seed", request.family_ids)
    captured: list[BulkSeedCaptured] = []

    for family_id in request.family_ids:
        joined = repository.get_family(family_id)
        if joined is None:
            continue  # resilient: skip unknown ids rather than abort the batch.

        record = joined.family
        sync = crm_adapter.push_family(record)
        # Derive-and-return the post-push §4.7 seam (A-7) from the adapter mirror —
        # not asserted, mode-agnostic (the simulated mirror reflects what we pushed).
        synced_record = record.model_copy(update={"crm_synced_at": record.updated_at})
        mirror = crm_adapter.read_mirror(family_id)
        seam_status = derive_seam_status(synced_record, mirror)
        captured.append(
            BulkSeedCaptured(
                family_id=family_id,
                deal_id=sync.recorded_id,
                seam_status=seam_status,
            )
        )

    return BulkSeedResponse(
        batch_id=batch_id,
        counts=BulkSeedCounts(captured=len(captured)),
        captured=captured,
    )


@router.post("/enrollment/families/bulk-dismiss", response_model=BulkDismissResponse)
def bulk_dismiss_families(
    request: BulkDismissRequest,
    log: LogDep,
) -> BulkDismissResponse:
    """Bulk-dismiss a selection — a THIN loop over the per-family dismiss write (A-20).

    Loops ``log_dismiss`` (the ONE new audit write; A-19) for each family with the
    shared, REQUIRED ``reason`` — a blank reason is rejected 422 by the request
    schema before any dismiss is logged. Each dismissed family then derives
    ``recovery_state=dismissed`` (until a later re-stall supersedes it). One
    ``batch_id`` tags the audit group (NFR-6). No second write path: this is the
    same family-keyed dismiss event the single path appends.
    """
    batch_id = _batch_id("bulk-dismiss", request.family_ids)
    dismissed: list[UUID] = []
    for family_id in request.family_ids:
        log.log_dismiss(family_id=family_id, human=DEFAULT_HUMAN, reason=request.reason)
        dismissed.append(family_id)

    return BulkDismissResponse(
        batch_id=batch_id,
        counts=BulkDismissCounts(dismissed=len(dismissed)),
        dismissed=dismissed,
    )


@router.post("/enrollment/families/bulk-assign", response_model=BulkAssignResponse)
def bulk_assign_families(
    request: BulkAssignRequest,
    repository: RepositoryDep,
    log: LogDep,
) -> BulkAssignResponse:
    """Assign a selection of families to one sales agent — the M4 write (A-30).

    A DETERMINISTIC core write, NOT an LLM call (INV-2): the deterministic core
    (``repository.assign_families``) stamps ``assigned_rep_id`` + ``assigned_at``
    on each KNOWN family (the owner-authority flip — the DB now owns deal
    ownership, A-30; ``app/core/seam.py``). A 1-element ``family_ids`` list is the
    single-assign case. ``agent_id`` is validated against the static
    ``sales_agents`` registry FIRST — an unknown agent is rejected 400 before any
    write (fail-closed). Unknown family ids are skipped (resilient bulk, like
    ``bulk-seed``).

    Each assignment is logged to the §10 audit spine (NFR-6 who/what/when): one
    ``proposal`` per family carrying the chosen ``agent_id`` (WHAT) plus a
    ``decision`` (WHO=operator, WHEN=now, action=approve). No eval is attached —
    this is a deterministic operator write, not an AI proposal-eval path. One
    ``batch_id`` tags the audit group.
    """
    if sales_agents.lookup(request.agent_id) is None:
        # Fail-closed: only a registered agent may own a deal (no write, no log).
        raise HTTPException(status_code=400, detail="unknown agent_id")

    assigned_at = datetime.now(UTC)
    assigned = repository.assign_families(request.family_ids, request.agent_id, assigned_at)

    # Log each assignment to the audit spine (NFR-6). Deterministic ⇒ no eval.
    for family_id in assigned:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=_ASSIGN_FLOW,
            schema_version=_ASSIGN_SCHEMA_VERSION,
            payload={"agent_id": str(request.agent_id)},
            family_id=family_id,
            created_at=assigned_at,
        )
        log.log_decision(
            proposal_id=proposal_id,
            human=DEFAULT_HUMAN,
            action=DecisionAction.APPROVE,
            created_at=assigned_at,
        )

    return BulkAssignResponse(
        batch_id=_batch_id("bulk-assign", request.family_ids),
        agent_id=request.agent_id,
        counts=BulkAssignCounts(assigned=len(assigned)),
        assigned=assigned,
    )

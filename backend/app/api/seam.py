"""Seam endpoints — non-synced list + human-gated reconcile (FR-1.3/2.6; ARCH §6).

The composition layer that wires the S3 seam core (``app.core.seam``) behind REST.
Thin by design: the deriver and the reconcile flow are pure/owned core (CLAUDE §1,
INV-2/INV-4); this router only orchestrates, shapes responses, maps HTTP errors,
and LOGS the human-gated reconcile to the §10 observability spine (NFR-6).

  ``GET  /seam``
    The non-synced cohort (FR-1.3/2.6): every family whose derived §4.7 seam status
    is not ``synced``, as ``family_id`` + ``seam_status``.

  ``POST /seam/{family_id}/reconcile``
    The human-approved reconcile (FR-2.6): :func:`propose_reconcile` then
    :func:`apply_reconcile` (the approved path), returning the recomputed seam
    status. The reconcile is LOGGED — a proposal then an approve decision (NFR-6,
    M-2). Fail-closed (INV-4): a flagged CONFLICT is NOT silently resolved —
    ``apply_reconcile`` returns ``applied=False`` and the seam stays ``conflict``.
    Derive-and-return per A-7: the read-only A-3 store is not mutated.

The seam status source is the REAL CRM mirror (R1): each endpoint calls
``crm_adapter.read_mirror(family_id)`` and derives the §4.7 status by comparing the
DB record to that mirror across every tracked field (stage, funding_state, owner).
The adapter is injected via :func:`app.api.deps.get_seam_crm_adapter_dep`, so v1
reads the SIMULATED (seeded) mirror by default and a live portal mirror only under
``CRM_MODE=live`` (INV-9) — the deriver/reconcile flow runs identically either way.

This module may import ``app.core`` / ``app.observability`` (it is the composition
root); ``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import (
    get_observability_log,
    get_repository,
    get_seam_crm_adapter_dep,
)
from app.core.seam import (
    ReconcileDirection,
    apply_reconcile,
    derive_seam_status,
    propose_reconcile,
)
from app.data.models import FamilyRecord, SeamStatus
from app.data.repository import FamilyRepository
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["seam"])

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/families.py + app/api/ai_actions.py).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_seam_crm_adapter_dep)]

# The audited §10 flow tag + schema version for a seam reconcile (the audit head).
RECONCILE_FLOW = "seam_reconcile"
RECONCILE_SCHEMA_VERSION = "1"
# The audited reviewer identity. v1 has no auth; the operator is a fixed seam (A-3).
DEFAULT_HUMAN = "operator"


def _local_advanced(record: FamilyRecord) -> bool:
    """The idempotency fence — true iff local ``updated_at`` is unpushed (§4.7).

    Reuses the seam freshness rule (``synced`` iff ``crm_synced_at >= updated_at``)
    inverted: a write/re-push is warranted only when local ``updated_at`` strictly
    advanced past the stored ``crm_synced_at`` (or nothing was ever synced). An
    already-synced record returns ``False`` so the reconcile never re-pushes —
    no write loops.
    """
    updated_at = record.updated_at
    if updated_at is None:
        return False
    synced_at = record.crm_synced_at
    return synced_at is None or synced_at < updated_at


class SeamRow(BaseModel):
    """One non-synced family in the §4.7 seam view (FR-1.3/2.6)."""

    family_id: UUID
    seam_status: SeamStatus


class ReconcileResponse(BaseModel):
    """The outcome of a seam reconcile (FR-2.6).

    ``applied`` is ``False`` for a flagged conflict (fail-closed, INV-4) or a
    no-op; ``seam_status`` is the seam after the (attempted) reconcile.
    """

    family_id: UUID
    applied: bool
    seam_status: SeamStatus


@router.get("/seam", response_model=list[SeamRow])
def list_non_synced(repository: RepositoryDep, crm_adapter: CRMAdapterDep) -> list[SeamRow]:
    """List families whose derived §4.7 seam status is not ``synced`` (FR-1.3/2.6).

    Derives each family's status from the REAL CRM mirror (R1): the adapter's
    ``read_mirror`` vs the DB record, across every tracked field. No fabricated
    mirror — the simulated (v1) adapter is seeded so this stays demoable.
    """
    rows: list[SeamRow] = []
    for record in repository.list_families():
        mirror = crm_adapter.read_mirror(record.family_id)
        status = derive_seam_status(record, mirror)
        if status is not SeamStatus.SYNCED:
            rows.append(SeamRow(family_id=record.family_id, seam_status=status))
    return rows


@router.post("/seam/{family_id}/reconcile", response_model=ReconcileResponse)
def reconcile_seam(
    family_id: UUID,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    log: LogDep,
) -> ReconcileResponse:
    """Reconcile one family's seam — propose, apply, PERSIST, log (FR-2.6; NFR-6).

    404 on an unknown family. Already-synced ⇒ a no-op (``applied=False``,
    ``synced``). Otherwise propose → apply: ``push_local`` syncs; a flagged
    ``conflict`` stays conflict (``applied=False``, INV-4 fail-closed).

    On an APPLIED ``push_local`` / ``accept_mirror`` the result is now PERSISTED
    through the store seam (TODO.md R1): the adopted field (on accept) and the
    advanced ``crm_synced_at`` are written back via the repository, then the
    family is re-pushed through the CRM adapter (simulated v1 — INV-9). An
    idempotency fence reuses the §4.7 freshness rule — the family is persisted/
    re-pushed only when local ``updated_at`` strictly advanced past the stored
    ``crm_synced_at`` (an already-synced record never re-pushes, so no write
    loops). A flagged ``conflict`` persists NOTHING — fail-closed, human-gated.
    The proposal + the approve decision are LOGGED to the §10 spine (NFR-6).
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    record = joined.family
    mirror = crm_adapter.read_mirror(family_id)

    proposal = propose_reconcile(record, mirror)
    if proposal is None:
        # Already synced — nothing to reconcile (a clean no-op, not an error).
        return ReconcileResponse(family_id=family_id, applied=False, seam_status=SeamStatus.SYNCED)

    result = apply_reconcile(record, proposal)

    # PERSIST the approved result through the store seam (TODO.md R1). Only an
    # APPLIED push_local/accept_mirror writes; a flagged conflict fails closed
    # (applied=False) and persists nothing. The idempotency fence reuses the §4.7
    # freshness rule: write only when local `updated_at` strictly advanced past
    # the stored `crm_synced_at` — an already-synced record never re-pushes.
    if result.applied and _local_advanced(record):
        if proposal.direction is ReconcileDirection.ACCEPT_MIRROR:
            # Adopt the mirror's tracked fields onto the stored record first, then
            # mark synced (the post-reconcile record already reflects both).
            repository.apply_field(family_id, "current_stage", result.record.current_stage)
            repository.apply_field(family_id, "funding_state", result.record.funding_state)
        synced_at = result.record.crm_synced_at
        if synced_at is not None:
            repository.mark_synced(family_id, synced_at)
        # Re-push the synced record through the CRM adapter (simulated v1 — INV-9).
        crm_adapter.push_family(result.record)

    # LOG the human-gated reconcile (NFR-6, M-2): the proposal, then the approve
    # decision. A flagged conflict is logged too — the audit records the flag even
    # though apply_reconcile fails closed (applied=False); the human approved the
    # ACTION, the core declined to silently resolve the conflict (INV-4).
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=RECONCILE_FLOW,
        schema_version=RECONCILE_SCHEMA_VERSION,
        payload=proposal.model_dump(mode="json"),
        family_id=family_id,
    )
    log.log_decision(
        proposal_id=proposal_id,
        human=DEFAULT_HUMAN,
        action=DecisionAction.APPROVE,
    )

    return ReconcileResponse(
        family_id=family_id,
        applied=result.applied,
        seam_status=result.seam_status,
    )

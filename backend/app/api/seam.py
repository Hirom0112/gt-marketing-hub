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

The seam status source is the family's seeded ``crm_seam_status`` column (the §4.7
derived seam; the simulated CRM mirror is push-rebuilt and empty on a fresh
adapter, so the seeded column is the authoritative per-family seam here). For the
reconcile we synthesize the :class:`MirrorState` that reproduces that seeded status
deterministically, so the pure core flow runs exactly as on a live mirror.

This module may import ``app.core`` / ``app.observability`` (it is the composition
root); ``app/core/`` stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_observability_log, get_repository
from app.core.seam import (
    MirrorState,
    apply_reconcile,
    derive_seam_status,
    propose_reconcile,
)
from app.data.models import FamilyRecord, SeamStatus, Stage
from app.data.repository import FamilyRepository
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["seam"])

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/families.py + app/api/ai_actions.py).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]

# The audited §10 flow tag + schema version for a seam reconcile (the audit head).
RECONCILE_FLOW = "seam_reconcile"
RECONCILE_SCHEMA_VERSION = "1"
# The audited reviewer identity. v1 has no auth; the operator is a fixed seam (A-3).
DEFAULT_HUMAN = "operator"


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


def _mirror_for_seam(record: FamilyRecord) -> MirrorState:
    """Synthesize the §4.7 mirror that reproduces a record's seeded seam status.

    The fresh simulated CRM mirror is empty (push-rebuilt), so we reconstruct the
    mirror the §4.7 deriver would read for the family's seeded ``crm_seam_status``,
    keeping the pure reconcile flow faithful:

    - ``conflict`` — the mirror diverges (a different tracked stage) with neither
      side clearly newer (equal instant) ⇒ :func:`derive_seam_status` ⇒ conflict.
    - ``unsynced`` — an empty mirror (nothing pushed) ⇒ unsynced.
    - ``synced``   — the mirror mirrors the local stage at the same instant.

    A post-condition test guards that ``derive_seam_status(record, mirror)`` equals
    the seeded status for every seeded family (no silent drift).
    """
    if record.crm_seam_status is SeamStatus.CONFLICT:
        # A tracked stage that differs from local, at an equal instant ⇒ no clear
        # winner ⇒ a genuine §4.7 conflict.
        diverging_stage = next(stage for stage in Stage if stage != record.current_stage)
        return MirrorState(stage=diverging_stage, mirror_updated_at=record.updated_at)
    if record.crm_seam_status is SeamStatus.UNSYNCED:
        # Nothing pushed ⇒ unsynced (local changes unpushed).
        return MirrorState(stage=None, mirror_updated_at=None)
    # SYNCED: mirror reflects local at the same instant.
    return MirrorState(stage=record.current_stage, mirror_updated_at=record.updated_at)


@router.get("/seam", response_model=list[SeamRow])
def list_non_synced(repository: RepositoryDep) -> list[SeamRow]:
    """List families whose derived §4.7 seam status is not ``synced`` (FR-1.3/2.6)."""
    rows: list[SeamRow] = []
    for record in repository.list_families():
        status = derive_seam_status(record, _mirror_for_seam(record))
        if status is not SeamStatus.SYNCED:
            rows.append(SeamRow(family_id=record.family_id, seam_status=status))
    return rows


@router.post("/seam/{family_id}/reconcile", response_model=ReconcileResponse)
def reconcile_seam(
    family_id: UUID,
    repository: RepositoryDep,
    log: LogDep,
) -> ReconcileResponse:
    """Reconcile one family's seam — propose, apply (approved), log (FR-2.6; NFR-6).

    404 on an unknown family. Already-synced ⇒ a no-op (``applied=False``,
    ``synced``). Otherwise propose → apply: ``push_local`` syncs; a flagged
    ``conflict`` stays conflict (``applied=False``, INV-4 fail-closed). The
    proposal + the approve decision are LOGGED to the §10 spine (NFR-6).
    Derive-and-return per A-7: the read-only store is not mutated.
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    record = joined.family
    mirror = _mirror_for_seam(record)

    proposal = propose_reconcile(record, mirror)
    if proposal is None:
        # Already synced — nothing to reconcile (a clean no-op, not an error).
        return ReconcileResponse(family_id=family_id, applied=False, seam_status=SeamStatus.SYNCED)

    result = apply_reconcile(record, proposal)

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

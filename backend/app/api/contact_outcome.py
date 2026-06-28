"""Close-loop WRITE endpoints — log a contact outcome + confirm presumed-lost.

The recovery deriver (``api/families.py``) READS the contact-outcome and lost
events off the audit spine; this router is how a rep WRITES them:

- ``POST /families/{id}/contact-outcome`` — the structured 'log a call outcome' a
  rep never had (only free-text notes, invisible to the deriver). An append-only
  spine event (INV-2 — a logged event, never a silent state write); the response
  echoes the family's freshly-derived ``recovery_state`` so the UI sees the effect
  (e.g. the 5th no-answer flipping it to ``presumed_lost``).
- ``POST /families/{id}/presumed-lost-confirm`` — the human-confirm gate
  (``nurture.presumed_lost.requires_human_confirm``). It writes a ``LostRecord``
  ONLY for a family the silence rule has already SURFACED as ``presumed_lost`` —
  fail-closed (409) otherwise, so the system never auto-drops a warm lead.

Both are owner-scoped through the SAME :func:`resolve_owner_scope` IDOR clamp every
owner-scoped route uses (INV-5): a foreign family is a 404, never written to. The
request/response models live HERE (not in ``api/schemas.py``) so this slice adds no
shared-file churn. Registered on its own router (kept off ``families.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import (
    Principal,
    get_observability_log,
    get_params,
    get_principal,
    get_repository,
    resolve_owner_scope,
)
from app.api.families import DEFAULT_HUMAN, _recovery_state_for
from app.core.params import Params
from app.core.recovery_state import RecoveryState
from app.data.repository import FamilyRepository, JoinedFamily
from app.observability.log_store import (
    ContactChannel,
    ContactDisposition,
    ObjectionReason,
    ObservabilityLog,
)

router = APIRouter(tags=["close-loop"])

RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
ParamsDep = Annotated[Params, Depends(get_params)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class ContactOutcomeRequest(BaseModel):
    """A rep's logged contact attempt (the 'log a call outcome' write)."""

    channel: ContactChannel
    disposition: ContactDisposition
    # A future-dated commitment captured on the call ("paying next week"); drives the
    # follow-up surface and suppresses a premature nudge. None = no promise made.
    promised_by: date | None = None
    # A structured objection the family raised on the attempt (Module 6); counted by
    # the weekly scorecard. None = no objection logged. Optional/back-compat.
    objection: ObjectionReason | None = None
    note: str = ""


class ContactOutcomeResponse(BaseModel):
    """The logged outcome, echoed with the family's freshly-derived recovery_state."""

    family_id: UUID
    channel: ContactChannel
    disposition: ContactDisposition
    promised_by: date | None
    objection: ObjectionReason | None
    note: str
    created_at: datetime
    recovery_state: RecoveryState


class ConfirmLostRequest(BaseModel):
    """The human's confirmation that a presumed-lost family is truly lost."""

    # Required so the audit always records WHY (mirrors dismiss): a blank /
    # whitespace-only reason is a 422 at the schema, never a no-reason drop.
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v


class ConfirmLostResponse(BaseModel):
    """The recorded LOST transition, with the now-derived recovery_state."""

    family_id: UUID
    recovery_state: RecoveryState
    reason: str


def _owned_family_or_404(
    family_id: UUID, repository: FamilyRepository, principal: Principal
) -> JoinedFamily:
    """Resolve a family the principal may write, else 404 (the IDOR clamp, INV-5).

    Mirrors ``families.get_family_assignments``: the scope is decided by the ROLE,
    never a client param. An operator may act only on a family in its OWN book; a
    foreign (or unknown) family is a 404 — existence itself is never leaked
    (deny-by-default). A leader/admin may act on any family.
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    owner_scope = resolve_owner_scope(principal, None)
    if isinstance(owner_scope, UUID) and joined.family.assigned_rep_id != owner_scope:
        raise HTTPException(status_code=404, detail="family not found")
    return joined


@router.post(
    "/families/{family_id}/contact-outcome",
    response_model=ContactOutcomeResponse,
    status_code=status.HTTP_201_CREATED,
)
def log_contact_outcome(
    family_id: UUID,
    request: ContactOutcomeRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
    principal: PrincipalDep,
) -> ContactOutcomeResponse:
    """Append a rep's contact-attempt outcome and return the re-derived state.

    The append-only close-loop event (INV-2): a logged fact the deriver reads, not
    a direct state write. After logging, the family's ``recovery_state`` is
    re-derived through the SAME composition root the read path uses
    (:func:`_recovery_state_for`) against one ``now`` — so the caller sees the
    effect of this outcome (e.g. the threshold no-answer flipping to
    ``presumed_lost``). Owner-scoped (404 on a foreign/unknown family).
    """
    joined = _owned_family_or_404(family_id, repository, principal)
    record = log.log_contact_outcome(
        family_id=family_id,
        channel=request.channel,
        disposition=request.disposition,
        human=DEFAULT_HUMAN,
        promised_by=request.promised_by,
        objection=request.objection,
        note=request.note,
    )
    now = datetime.now(UTC)
    state = _recovery_state_for(joined, log=log, now=now, params=params)
    return ContactOutcomeResponse(
        family_id=family_id,
        channel=record.channel,
        disposition=record.disposition,
        promised_by=record.promised_by,
        objection=record.objection,
        note=record.note,
        created_at=record.created_at,
        recovery_state=state,
    )


@router.post("/families/{family_id}/presumed-lost-confirm", response_model=ConfirmLostResponse)
def confirm_presumed_lost(
    family_id: UUID,
    request: ConfirmLostRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
    principal: PrincipalDep,
) -> ConfirmLostResponse:
    """Confirm a SURFACED presumed-lost family as LOST (the human-confirm gate).

    Fail-closed: the family must currently derive ``presumed_lost`` (the silence
    rule surfaced it) — otherwise this is a 409, so a rep can never drop a lead the
    system has not surfaced (``requires_human_confirm``; the machine never
    auto-drops). On confirm it appends a ``LostRecord`` (required reason, the audit
    of WHY — INV-2) and returns the now-``lost`` state. Reversible: a later re-stall
    supersedes it (the dismiss pattern). Owner-scoped (404 on a foreign family).
    """
    joined = _owned_family_or_404(family_id, repository, principal)
    now = datetime.now(UTC)
    current = _recovery_state_for(joined, log=log, now=now, params=params)
    if current is not RecoveryState.PRESUMED_LOST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="family is not presumed-lost; nothing to confirm (never auto-drop)",
        )
    log.log_lost(family_id=family_id, human=DEFAULT_HUMAN, reason=request.reason)
    confirmed = _recovery_state_for(joined, log=log, now=now, params=params)
    return ConfirmLostResponse(family_id=family_id, recovery_state=confirmed, reason=request.reason)

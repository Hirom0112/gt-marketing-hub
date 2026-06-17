"""Funding endpoints — TEFA view + GT-controlled signal advance (FR-2.7; ARCH §6).

The composition layer that wires the S3 funding core (``app.core.funding_gate``)
behind REST. It is deliberately thin: every number and every transition decision
lives in the pure core (CLAUDE §1, INV-2/INV-11); this router only orchestrates,
shapes the response, and maps HTTP errors. No AI, no magic numbers.

  ``GET  /families/{family_id}/funding``
    The funding view (FR-2.7): ``funding_state`` + tier + the installment schedule
    (via :func:`compute_installments`) + the tuition lock (via
    :func:`tuition_step_unlocked`). A SELF_PAY / non-TEFA family has no TEFA award,
    so :func:`compute_installments` raises ``ValueError`` — we surface the view with
    ``installments=None`` rather than 500. 404 on an unknown family.

  ``POST /families/{family_id}/funding/signal``
    The §5.4 funding-state advance on a GT-controlled signal (INV-10): the body's
    booleans map to the §5.4 target state, advanced one legal step via
    :func:`advance_funding_state`, then the recomputed view is returned. This is
    the GT-controlled path — GT-confirmed enrollment, a first-installment receipt,
    the family's self-report — NOT an Odyssey/TEFA API. An illegal advance is a
    422 (never a 500). Derive-and-return per A-7: the read-only A-3 store is not
    mutated; the advanced state is computed and surfaced.

This module may import ``app.core`` / ``app.data`` (it is the composition root);
``app/core/`` stays pure. No live external call is ever made here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_params, get_repository
from app.core.funding_gate import (
    advance_funding_state,
    compute_installments,
    tuition_step_unlocked,
)
from app.core.params import Params
from app.data.models import FundingState, FundingType
from app.data.repository import FamilyRepository

router = APIRouter(tags=["funding"])

# Dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/families.py).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
ParamsDep = Annotated[Params, Depends(get_params)]

# The §5.4 mapping from a GT-controlled signal boolean to the funding-state EVENT
# it asserts (INV-10). Ordered most-advanced first so the strongest asserted
# signal wins when several are set; ``advance_funding_state`` then enforces that
# the event is exactly the current state's one legal successor.
_SIGNAL_TO_EVENT: tuple[tuple[str, FundingState], ...] = (
    ("first_installment_received", FundingState.FIRST_INSTALLMENT_RECEIVED),
    ("gt_confirmed", FundingState.GT_CONFIRMED),
    # R2: the voucher-selection signal advances toward SELECTED_GT — the family
    # picked GT but has not yet reconfirmed/locked in. GT-controlled (INV-10),
    # ordered between gt_confirmed and self_report (it sits just past
    # AWARDED_SELFREPORT on the §5.4 path).
    ("family_selected", FundingState.SELECTED_GT),
    ("self_report", FundingState.AWARDED_SELFREPORT),
)


class FundingView(BaseModel):
    """The funding view for one family (FR-2.7).

    ``installments`` is the TEFA schedule as cent-precise decimal strings (Pydantic
    serializes ``Decimal`` → ``str`` by default), or ``None`` for a SELF_PAY /
    non-TEFA family with no TEFA award. ``tuition_unlocked`` is the §5.4 fail-closed
    gate (INV-10), reported from the funding state regardless of tier.
    """

    family_id: UUID
    funding_type: FundingType | None
    funding_state: FundingState
    installments: list[str] | None
    tuition_unlocked: bool


class FundingSignalRequest(BaseModel):
    """A GT-controlled funding signal (INV-10) — the §5.4 advance trigger.

    All booleans are GT-owned (GT-confirmed enrollment, a first-installment
    receipt, the family's self-report, the family's voucher selection); none is
    sourced from an external Odyssey / TEFA feed. Default ``False`` so a body may
    assert just the one signal it carries.
    """

    gt_confirmed: bool = False
    first_installment_received: bool = False
    self_report: bool = False
    # R2: the family indicated they picked GT for their voucher (not yet locked
    # in). GT-controlled (INV-10), advances toward SELECTED_GT.
    family_selected: bool = False


def _funding_view(
    family_id: UUID,
    funding_type: FundingType | None,
    funding_state: FundingState,
    params: Params,
) -> FundingView:
    """Assemble the funding view for a family at ``funding_state`` (FR-2.7).

    Installments come from :func:`compute_installments`; a non-TEFA tier raises
    ``ValueError`` (fail-closed), which we map to ``installments=None`` — never a
    500. The tuition lock is always reported from the state (INV-10).
    """
    installments: list[str] | None
    try:
        amounts = compute_installments(funding_type, params) if funding_type is not None else None
    except ValueError:
        # SELF_PAY / non-TEFA: no TEFA award. Surface the view without a schedule.
        amounts = None
    installments = [str(amount) for amount in amounts] if amounts is not None else None

    return FundingView(
        family_id=family_id,
        funding_type=funding_type,
        funding_state=funding_state,
        installments=installments,
        tuition_unlocked=tuition_step_unlocked(funding_state, params),
    )


@router.get("/families/{family_id}/funding", response_model=FundingView)
def get_funding(family_id: UUID, repository: RepositoryDep, params: ParamsDep) -> FundingView:
    """Funding view for one family — state + tier + installments + tuition lock (FR-2.7)."""
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    family = joined.family
    return _funding_view(family.family_id, family.funding_type, family.funding_state, params)


@router.post("/families/{family_id}/funding/signal", response_model=FundingView)
def post_funding_signal(
    family_id: UUID,
    request: FundingSignalRequest,
    repository: RepositoryDep,
    params: ParamsDep,
) -> FundingView:
    """Advance the §5.4 funding state on a GT-controlled signal (INV-10; FR-2.7).

    Maps the asserted signal to its §5.4 target event and advances one legal step
    via :func:`advance_funding_state`, then returns the recomputed view. 404 on an
    unknown family; 422 on an illegal advance / no asserted signal (never a 500).
    Derive-and-return per A-7: the read-only store is not mutated.
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    family = joined.family

    event = next(
        (state for field, state in _SIGNAL_TO_EVENT if getattr(request, field)),
        None,
    )
    if event is None:
        raise HTTPException(status_code=422, detail="no funding signal asserted")

    try:
        advanced = advance_funding_state(family.funding_state, event)
    except ValueError as exc:
        # Illegal §5.4 transition (skip / backwards / terminal): fail closed, no 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _funding_view(family.family_id, family.funding_type, advanced, params)

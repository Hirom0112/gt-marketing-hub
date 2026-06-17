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

Both views also carry the R2 VOUCHER STANDING (``program`` + ``next_action`` +
``due_by`` + ``days_remaining`` + ``at_risk`` + ``award_full_vs_prorated``), via
the pure :func:`app.core.voucher.voucher_standing` engine — so a UI can show where
the voucher stands, the next step, and by when. The standing reads every
window/rule from params (INV-11), uses GT-controlled signals only (INV-10), and is
fail-closed on an unknown program (a clean error, never a fabricated award).

This module may import ``app.core`` / ``app.data`` (it is the composition root);
``app/core/`` stays pure. No live external call is ever made here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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
from app.core.voucher import voucher_standing
from app.data.models import FundingState, FundingType
from app.data.repository import FamilyRepository

router = APIRouter(tags=["funding"])

# The default voucher program a family maps to (R2). The synthetic cohort is Texas,
# so every family maps to ``tx_tefa`` unless a program is explicitly requested. This
# is the single, explicit, overridable family→program mapping: there is no per-family
# program column yet, so :func:`_program_for_family` returns this default; a future
# column or query param feeds the same hook. An UNKNOWN program is NEVER coerced to
# this default — it surfaces as a clean error (fail-closed, INV-10), never a fake
# award/deadline.
_DEFAULT_VOUCHER_PROGRAM = "tx_tefa"

# SELF_PAY (and any None tier) carries no TEFA award; ``voucher_standing`` reads no
# amount, so the tier it receives only flavors the per-state next-action text. We
# pass a stable, amount-free placeholder so a SELF_PAY family still surfaces a
# truthful standing (its funding STATE drives the action), never a fabricated award.
_VOUCHER_TIER_FALLBACK = FundingType.SELF_PAY

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
    """The funding view for one family (FR-2.7) + its R2 voucher standing.

    ``installments`` is the TEFA schedule as cent-precise decimal strings (Pydantic
    serializes ``Decimal`` → ``str`` by default), or ``None`` for a SELF_PAY /
    non-TEFA family with no TEFA award. ``tuition_unlocked`` is the §5.4 fail-closed
    gate (INV-10), reported from the funding state regardless of tier.

    The voucher-standing fields (R2) compose the §5.4 state with the program's
    params-homed windows/rules so a UI can show where the voucher stands and the
    next step:

    * ``program`` — the resolved voucher program (default ``tx_tefa``; INV-11).
    * ``next_action`` — the single next step that moves the voucher forward.
    * ``due_by`` — the operative "by when" (the parent-select/reconfirm deadline),
      or ``None`` once there is no open reconfirm gap.
    * ``days_remaining`` — days from the injected ``today`` to ``due_by`` (or ``None``).
    * ``at_risk`` — selected/awarded but not reconfirmed with the deadline at hand.
    * ``award_full_vs_prorated`` — ``"full"`` on/before the cutoff, else ``"prorated"``.
    """

    family_id: UUID
    funding_type: FundingType | None
    funding_state: FundingState
    installments: list[str] | None
    tuition_unlocked: bool
    # R2 voucher standing (composed from app.core.voucher.voucher_standing).
    program: str
    next_action: str
    due_by: date | None
    days_remaining: int | None
    at_risk: bool
    award_full_vs_prorated: str


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


def _program_for_family(funding_type: FundingType | None) -> str:
    """Resolve a family's voucher program — the explicit, overridable mapping (R2).

    No per-family program column exists yet, so the synthetic Texas cohort maps to
    ``tx_tefa`` (the documented default). This is the single hook a future per-family
    column or request override feeds; it never silently coerces an UNKNOWN program —
    voucher_standing fails closed on a bad key (INV-10).
    """
    return _DEFAULT_VOUCHER_PROGRAM


def _today() -> date:
    """The injected reference date for the voucher windows (deterministic seam).

    Matches the API-layer clock used elsewhere (``app/api/ai_actions.py``,
    ``app/api/families.py``): ``datetime.now(UTC).date()``. The pure core never calls
    ``now()`` (INV-2 purity); this composition layer supplies ``today``.
    """
    return datetime.now(UTC).date()


def _funding_view(
    family_id: UUID,
    funding_type: FundingType | None,
    funding_state: FundingState,
    params: Params,
    *,
    today: date,
) -> FundingView:
    """Assemble the funding view for a family at ``funding_state`` (FR-2.7) + standing.

    Installments come from :func:`compute_installments`; a non-TEFA tier raises
    ``ValueError`` (fail-closed), which we map to ``installments=None`` — never a
    500. The tuition lock is always reported from the state (INV-10). The R2 voucher
    standing is composed from :func:`voucher_standing` over the resolved program's
    params-homed windows/rules as of ``today``; an unknown program raises ``KeyError``
    which :func:`get_funding` surfaces as a clean error (never a fabricated award).
    """
    installments: list[str] | None
    try:
        amounts = compute_installments(funding_type, params) if funding_type is not None else None
    except ValueError:
        # SELF_PAY / non-TEFA: no TEFA award. Surface the view without a schedule.
        amounts = None
    installments = [str(amount) for amount in amounts] if amounts is not None else None

    program = _program_for_family(funding_type)
    standing = voucher_standing(
        state=funding_state,
        # voucher_standing reads no amount; SELF_PAY/None passes the amount-free
        # fallback so the standing is still truthful (state-driven), never a fake award.
        funding_type=funding_type if funding_type is not None else _VOUCHER_TIER_FALLBACK,
        program_key=program,
        today=today,
        params=params,
    )

    return FundingView(
        family_id=family_id,
        funding_type=funding_type,
        funding_state=funding_state,
        installments=installments,
        tuition_unlocked=tuition_step_unlocked(funding_state, params),
        program=standing.program,
        next_action=standing.next_action,
        due_by=standing.due_by,
        days_remaining=standing.days_remaining,
        at_risk=standing.at_risk,
        award_full_vs_prorated=standing.award_full_vs_prorated,
    )


@router.get("/families/{family_id}/funding", response_model=FundingView)
def get_funding(family_id: UUID, repository: RepositoryDep, params: ParamsDep) -> FundingView:
    """Funding view — state + tier + installments + tuition lock + voucher standing (FR-2.7)."""
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")
    family = joined.family
    try:
        return _funding_view(
            family.family_id, family.funding_type, family.funding_state, params, today=_today()
        )
    except KeyError as exc:
        # Unknown voucher program (fail-closed, INV-10): a clean 422, never a fake award.
        raise HTTPException(status_code=422, detail=f"unknown voucher program: {exc}") from exc


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

    try:
        return _funding_view(
            family.family_id, family.funding_type, advanced, params, today=_today()
        )
    except KeyError as exc:
        # Unknown voucher program (fail-closed, INV-10): a clean 422, never a fake award.
        raise HTTPException(status_code=422, detail=f"unknown voucher program: {exc}") from exc

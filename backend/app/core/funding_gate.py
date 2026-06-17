"""TEFA funding math + funding-state machine — the deterministic core of S3.

Two pure pieces, both reading every number from params (CLAUDE.md INV-11):

  - `compute_installments(tier, params)` — the per-installment schedule for a
    TEFA award. Money is `Decimal`, quantized to cents; the last installment is
    `award − sum(prior)` so the schedule reconciles to the award with zero
    rounding drift (FR-2.7; ARCHITECTURE.md §8). Amounts and the split come from
    `params.funding`, never a literal here.

  - `advance_funding_state` + `tuition_step_unlocked` — the §5.4 funding-state
    machine. Advancement is strictly linear; any illegal transition is rejected.
    The tuition step is **fail-closed** (INV-10): locked until the funding
    signal proves first-installment receipt — a GT-controlled signal
    (GT-confirmed enrollment + first-installment receipt + family self-report),
    NOT an Odyssey API. The unlock threshold is read from params.

This module is part of the deterministic core and stays pure: no `app.ai`,
no `app.adapters`, no I/O (the core-purity test guards this; CLAUDE.md §3, INV-2).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.params import AwardAmounts, Params
from app.data.models import FundingState, FundingType

# Cent precision for all money quantization.
_CENTS = Decimal("0.01")

# Map each TEFA tier to its params-backed award amount. SELF_PAY is absent: it
# has no TEFA award, so `compute_installments` rejects it (fail-closed).
_TEFA_TIERS = (
    FundingType.TEFA_STANDARD,
    FundingType.TEFA_DISABILITY,
    FundingType.TEFA_HOMESCHOOL,
)

# The legal §5.4 funding lifecycle, in order. Advancement moves exactly one step
# forward along this path; anything else is illegal.
_LEGAL_PATH: tuple[FundingState, ...] = (
    FundingState.NONE,
    FundingState.APPLIED,
    FundingState.AWARDED_SELFREPORT,
    # The voucher selection/reconfirm gap (TODO.md R2), additively inserted:
    # SELECTED_GT = the family picked GT but has NOT yet reconfirmed/locked in;
    # RECONFIRMED = the parent completed the lock-in. The "lost on a deadline"
    # at-risk gap is the SELECTED_GT → RECONFIRMED step. Both are GT-controlled
    # signals (INV-10) — never an Odyssey/voucher API.
    FundingState.SELECTED_GT,
    FundingState.RECONFIRMED,
    FundingState.GT_CONFIRMED,
    FundingState.FIRST_INSTALLMENT_RECEIVED,
    FundingState.FUNDED,
)


def award_for_tier(tier: FundingType, amounts: AwardAmounts) -> Decimal:
    """The TEFA award for a tier, read from ``funding.award_amounts`` (INV-11).

    The single source of the per-tier award amount, shared by the installment
    schedule (``compute_installments``) and the live CRM adapter (which mirrors
    the award onto the HubSpot deal ``amount``). SELF_PAY (and any non-TEFA tier)
    has no TEFA award and is rejected — fail-closed, never a silent ``0``.

    Args:
        tier: A TEFA tier (``tefa_standard`` / ``tefa_disability`` / ``tefa_homeschool``).
        amounts: The ``funding.award_amounts`` params block.

    Returns:
        The award as a cent-quantized ``Decimal``.

    Raises:
        ValueError: if ``tier`` has no TEFA award (e.g. ``self_pay``).
    """
    by_tier = {
        FundingType.TEFA_STANDARD: amounts.tefa_standard,
        FundingType.TEFA_DISABILITY: amounts.tefa_disability,
        FundingType.TEFA_HOMESCHOOL: amounts.tefa_homeschool,
    }
    if tier not in by_tier:
        raise ValueError(f"no TEFA award for funding tier: {tier!r}")
    return Decimal(str(by_tier[tier])).quantize(_CENTS)


def _award_for(tier: FundingType, params: Params) -> Decimal:
    """The award amount for a TEFA tier, read from params (INV-11)."""
    return award_for_tier(tier, params.funding.award_amounts)


def compute_installments(tier: FundingType, params: Params) -> list[Decimal]:
    """Per-installment schedule for a TEFA award (FR-2.7; ARCHITECTURE.md §8).

    The award and the split fractions come from `params.funding` (INV-11). Each
    installment is `award × fraction`, quantized to cents, EXCEPT the last, which
    is `award − sum(prior)` so the schedule sums back to the award with zero
    rounding drift.

    Args:
        tier: A TEFA tier (`tefa_standard` / `tefa_disability` / `tefa_homeschool`).
        params: Loaded params; supplies award amounts and `installment_split`.

    Returns:
        The list of installment amounts as `Decimal`, quantized to cents, one
        per `funding.installment_split` entry; `sum(...) == award` exactly.

    Raises:
        ValueError: if `tier` has no TEFA award (e.g. `self_pay`) — fail-closed.
    """
    award = _award_for(tier, params)
    split = params.funding.installment_split

    installments: list[Decimal] = []
    running = Decimal("0.00")
    for i, fraction in enumerate(split):
        is_last = i == len(split) - 1
        if is_last:
            amount = award - running
        else:
            amount = (award * Decimal(str(fraction))).quantize(_CENTS, rounding=ROUND_HALF_UP)
        installments.append(amount)
        running += amount
    return installments


def advance_funding_state(current: FundingState, event: FundingState) -> FundingState:
    """Advance the funding state one step along the legal §5.4 path.

    Only a single forward step is legal: `current`'s successor in the lifecycle.
    A skip, a backwards move, a self-transition, or an unknown target is illegal
    and rejected — the state machine is fail-closed (INV-10).

    Args:
        current: The family's present funding state.
        event: The funding signal/event, expressed as the target state.

    Returns:
        The next funding state (== `event` on a legal advance).

    Raises:
        ValueError: on any illegal transition.
    """
    index = _LEGAL_PATH.index(current)
    if index == len(_LEGAL_PATH) - 1:
        raise ValueError(f"funding state {current!r} is terminal; cannot advance")
    expected_next = _LEGAL_PATH[index + 1]
    if event != expected_next:
        raise ValueError(
            f"illegal funding transition {current!r} → {event!r}; "
            f"only {current!r} → {expected_next!r} is legal"
        )
    return expected_next


def tuition_step_unlocked(state: FundingState, params: Params) -> bool:
    """Whether the tuition step is unlocked for a funding state (INV-10).

    Fail-closed: tuition stays locked until the funding state reaches the params
    threshold (`funding.tuition_unlock_state` = `first_installment_received`),
    which proves first-installment receipt via a GT-controlled signal. States
    at or after the threshold ⇒ unlocked; earlier states ⇒ locked.

    Args:
        state: The family's current funding state.
        params: Loaded params; supplies `funding.tuition_unlock_state`.

    Returns:
        True iff `state` is at or after the params unlock threshold.
    """
    threshold = FundingState(params.funding.tuition_unlock_state)
    return _LEGAL_PATH.index(state) >= _LEGAL_PATH.index(threshold)

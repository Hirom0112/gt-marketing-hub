"""Voucher RULES + DEADLINES / standing engine (TODO.md R2; FR-2.7).

`funding_gate.py` owns the money math and the legal §5.4 state path; this module
**composes** it with the per-program window/rule layer so the cockpit can answer
"where does this family's voucher actually stand, what's the next action, and by
when." A new state is a CONFIG ROW (`voucher_programs:` in params), not a code
change — `voucher_standing` reads every window/rule from params and is driven by
`tx_tefa`, `az_esa`, or any future program identically.

The engine is:

  - **pure** — no I/O, no `now()`; `today` is an injected `date` (the core-purity
    test guards the import boundary; CLAUDE.md §3, INV-2);
  - **params-driven** — every deadline/threshold/amount comes from params (INV-11),
    never a literal here;
  - **fail-closed** — an unknown program key raises (no default award, no default
    deadline; INV-10). It uses GT-controlled signals (the funding state), never an
    Odyssey API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.funding_gate import tuition_step_unlocked
from app.core.params import Params, VoucherProgram
from app.data.models import FundingState, FundingType

# The funding states that mean "the family picked GT / has an award in hand but
# has NOT yet completed the parent reconfirm/lock-in." A family sitting here as a
# deadline approaches is the "$X lost on a deadline" at-risk gap (R2). Read as the
# states strictly before RECONFIRMED on the §5.4 path; expressed by name (not an
# index literal) so a path change is caught by the funding-gate's own guard.
_PRE_RECONFIRM_STATES: frozenset[FundingState] = frozenset(
    {
        FundingState.NONE,
        FundingState.APPLIED,
        FundingState.AWARDED_SELFREPORT,
        FundingState.SELECTED_GT,
    }
)


@dataclass(frozen=True)
class VoucherStanding:
    """Where a family's voucher stands + the next action and its deadline (R2).

    Pure value object (no I/O); every field is derived deterministically from the
    family's funding state and the program's params-homed windows/rules.
    """

    current_state: FundingState
    program: str
    next_action: str
    due_by: date | None
    days_remaining: int | None
    at_risk: bool
    award_full_vs_prorated: str


def _program(program_key: str, params: Params) -> VoucherProgram:
    """The program config for ``program_key`` — fail-closed on a miss (INV-10).

    Raises ``KeyError`` rather than defaulting: there is no default award or
    default deadline. An unknown program must surface, never silently load a
    fallback (CLAUDE.md INV-10, §4.1).
    """
    return params.voucher_programs[program_key]


def _award_full_vs_prorated(today: date, program: VoucherProgram) -> str:
    """FULL on/before the full-award cutoff, else PRORATED (R2; ANALYSIS-confirmed).

    The cutoff is the last date a confirmation still earns the full award; after
    it, late/waitlist joiners prorate by enrollment date. Boundary is inclusive:
    today == cutoff is still full.
    """
    if today <= program.windows.full_award_cutoff:
        return "full"
    return "prorated"


def voucher_standing(
    state: FundingState,
    funding_type: FundingType,
    program_key: str,
    today: date,
    params: Params,
) -> VoucherStanding:
    """Compute a family's voucher standing for a program as of ``today`` (R2).

    Pure + params-driven + fail-closed. Composes the §5.4 funding state with the
    program's params-homed windows/rules to produce the next action, its
    deadline, days remaining, the at-risk flag, and the full-vs-prorated branch.

    Args:
        state: The family's current §5.4 funding state (a GT-controlled signal).
        funding_type: The family's funding tier (kept for the caller's context;
            award amounts stay in ``funding`` — this engine reads no amount).
        program_key: The voucher program (e.g. ``tx_tefa`` / ``az_esa``). A new
            program is a config row, not a code change.
        today: The injected reference date (deterministic; never ``now()``).
        params: Loaded params; supplies ``voucher_programs`` windows/rules and the
            ``funding.tuition_unlock_state`` threshold.

    Returns:
        A :class:`VoucherStanding`.

    Raises:
        KeyError: if ``program_key`` is unknown — fail-closed, no default (INV-10).
    """
    program = _program(program_key, params)
    windows = program.windows

    # The reconfirm step's deadline is the parent-select deadline; it is the
    # operative "by when" until the family has reconfirmed. Programs without a
    # reconfirm gap (reconfirm_required == False) carry no reconfirm deadline.
    needs_reconfirm = windows.reconfirm_required and state in _PRE_RECONFIRM_STATES
    due_by: date | None = windows.parent_select_deadline if needs_reconfirm else None
    days_remaining: int | None = (due_by - today).days if due_by is not None else None

    # At risk: selected/awarded but not yet reconfirmed, and the reconfirm
    # deadline is at hand (on or before it) — the "$X lost on a deadline" gap.
    at_risk = needs_reconfirm and days_remaining is not None and days_remaining >= 0

    next_action = _next_action(state, program, due_by, params, funding_type)

    return VoucherStanding(
        current_state=state,
        program=program_key,
        next_action=next_action,
        due_by=due_by,
        days_remaining=days_remaining,
        at_risk=at_risk,
        award_full_vs_prorated=_award_full_vs_prorated(today, program),
    )


def _next_action(
    state: FundingState,
    program: VoucherProgram,
    due_by: date | None,
    params: Params,
    funding_type: FundingType,
) -> str:
    """The family/rep instruction for a funding state (R2 — the per-state next step).

    Each state maps to the single next action that moves the voucher forward.
    The reconfirm action names the parent-select deadline (``due_by``) so the rep
    has the concrete "by when." Tuition-unlock uses the funding-gate's own
    params-homed threshold (INV-11) so the message can't drift from the gate.
    """
    if state == FundingState.NONE:
        return "No voucher signal yet — capture an application self-report."
    if state == FundingState.APPLIED:
        return "Awaiting award notice — confirm the award self-report when it lands."
    if state == FundingState.AWARDED_SELFREPORT:
        if due_by is not None:
            return f"Awarded — guide the family to select GT by {due_by}."
        return "Awarded — guide the family to select GT."
    if state == FundingState.SELECTED_GT:
        if program.windows.reconfirm_required and due_by is not None:
            return f"Reconfirm GT by {due_by} to lock in the award before the deadline."
        return "Selected GT — proceed to GT enrollment confirmation."
    if state == FundingState.RECONFIRMED:
        return "Reconfirmed — GT to confirm enrollment in the portal."
    if state == FundingState.GT_CONFIRMED:
        return "Enrollment confirmed — awaiting first installment receipt."
    if state == FundingState.FIRST_INSTALLMENT_RECEIVED:
        unlocked = tuition_step_unlocked(state, params)
        if unlocked:
            return "First installment received — tuition step unlocked; proceed to funded."
        return "First installment received — verifying tuition unlock."
    if state == FundingState.FUNDED:
        return "Funded — voucher fully in place; monitor remaining installments."
    # Fail-closed: an unmapped state has no safe default action.
    raise ValueError(f"no next-action mapping for funding state {state!r}")

"""Recovery state machine — the DERIVED {stalled,working,recovered,dismissed} (A-19).

The S12 recovery loop labels every family by where it sits in the
catch-and-forward cycle. Per ASSUMPTIONS A-19 that label is DERIVED, never stored
(A-3 read-only store, INV-2 deterministic-core-owns-writes): a mock that mutated
``f.state`` in click handlers is replaced by a pure deriver fed the audit-log
facts the API layer resolves.

Like ``core/contact_status.py``, this module is the PURE core: a total function
of its arguments. The two facts that need ``now`` + the append-only audit log —
``last_contact_at`` (A-14) and ``dismissed`` (the new S12 dismiss event) — are
resolved at the API composition root (``api/families.py``) and passed IN, so this
deriver never touches a clock, the log, ``app.ai``, or ``app.adapters`` (the
core-purity test guards it). Same inputs ⇒ same state.

Precedence (document order is the contract — A-19):

  1. **DISMISSED** — a dismiss event holds for the family (and no later re-stall
     supersedes it; that supersession is resolved by the log's ``is_dismissed``).
  2. **RECOVERED** (DETECTED, never a button) — the family moved on its own:
     ``current_stage`` advanced past the stall stage, OR the stuck six-form step
     cleared (forms existed and the first-unsigned signal is now None), OR
     ``funding_state >= first_installment_received`` (the §5.4 funding gate).
  3. **WORKING** — an approved outbound exists (``last_contact_at`` is not None).
  4. **STALLED** — default (none of the above).

Active board = ``{STALLED, WORKING}``; history = ``{RECOVERED, DISMISSED}`` —
see :func:`is_active`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from app.core.funding_gate import _LEGAL_PATH
from app.core.params import Params
from app.data.models import EnrollmentForms, FundingState, Stage
from app.data.repository import JoinedFamily

# §4.8 funnel order — index comparison decides "advanced past the stall stage".
_STAGE_ORDER: tuple[Stage, ...] = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION)

# §5.4 funding gate: a family at or past this funding_state has recovered the deal
# (first installment received ⇒ tuition unlocked). The funnel ORDER is the canonical
# `funding_gate._LEGAL_PATH` (ONE home, INV-11) — NOT a hand-maintained copy. (A prior
# local copy omitted SELECTED_GT/RECONFIRMED, so `.index()` crashed for those states.)
_FUNDING_RECOVERED_FLOOR = FundingState.FIRST_INSTALLMENT_RECEIVED

# Which recovery predicate fired, for the history-scope outcome story (A-19).
# Labels are in the SAME document order as the OR in `derive_recovery_state`:
# stage-advance, then forms-cleared, then first-installment (the §5.4 deposit).
RecoveredOutcome = Literal["stage_advanced", "forms_cleared", "deposit_received"]


class RecoveryState(StrEnum):
    """A family's position in the S12 recovery loop (A-19).

    - ``STALLED``: gone quiet, no contact yet — the default.
    - ``WORKING``: an approved outbound is out; we are on it.
    - ``RECOVERED``: DETECTED data movement (stage-advance / forms-cleared /
      first-installment) — never a button, never an LLM write.
    - ``DISMISSED``: manually set aside with a recorded reason (the one new write).
    - ``COLD``: stalled + uncontacted past ``nurture.cold_after_days`` — a more-urgent
      STALLED (still active). An annotation, not a removal.
    - ``PRESUMED_LOST``: accrued silence (``nurture.presumed_lost`` no-response attempts)
      — auto-SURFACED for a human to confirm LOST; stays active until they do.
    - ``LOST``: human-confirmed lost (a recorded event) — history; reversible on
      re-engagement (the dismiss pattern).
    - ``DORMANT``: long-parked after nurture is exhausted (``nurture.max_touches``) —
      history; kept, never deleted (families return).
    """

    STALLED = "stalled"
    WORKING = "working"
    RECOVERED = "recovered"
    DISMISSED = "dismissed"
    COLD = "cold"
    PRESUMED_LOST = "presumed_lost"
    LOST = "lost"
    DORMANT = "dormant"


def _forms_cleared(joined: JoinedFamily) -> bool:
    """True when the six-form gauntlet EXISTED and every form is now signed (A-19).

    The "stuck step cleared" recovery signal. Guards against a family that never
    had forms (``enrollment_forms is None`` or zero forms) reading as recovered —
    a null first-unsigned-form must mean *all signed*, not *never had any*.
    """
    forms = joined.enrollment_forms
    if forms is None or forms.forms_total <= 0:
        return False
    return forms.forms_signed >= forms.forms_total


def _stage_advanced(current_stage: Stage, stall_stage: Stage) -> bool:
    """True when ``current_stage`` sits later in the funnel than ``stall_stage``."""
    return _STAGE_ORDER.index(current_stage) > _STAGE_ORDER.index(stall_stage)


def _funding_recovered(funding_state: FundingState) -> bool:
    """True when funding has reached at least the first-installment gate (§5.4)."""
    return _LEGAL_PATH.index(funding_state) >= _LEGAL_PATH.index(_FUNDING_RECOVERED_FLOOR)


def recovered_outcome(joined: JoinedFamily, *, stall_stage: Stage) -> RecoveredOutcome | None:
    """WHICH recovery predicate fired for a (would-be) RECOVERED family (A-19).

    Pure mirror of the OR inside :func:`derive_recovery_state`, in the SAME
    document order (the precedence is the contract): the family is RECOVERED iff
    any of the three §5.x signals fired, and this exposes the *first* one that
    did so the history surface can tell the OUTCOME story per resolved family.

    The history-view redesign reads it for a RECOVERED row; it is ``None`` for a
    family that has not recovered (no signal fired). It does NOT consider the
    dismiss/contact precedence — a dismissed family that also looks recovered is
    DISMISSED upstream, so the caller only asks this for RECOVERED rows.

    Args:
        joined: The spine row joined to its source rows — supplies
            ``current_stage``, ``funding_state``, and the enrollment-forms progress.
        stall_stage: The funnel stage the family was stuck at (the baseline for the
            "advanced past" check), resolved at the API layer (mirrors the deriver).

    Returns:
        ``'stage_advanced'`` / ``'forms_cleared'`` / ``'deposit_received'`` for the
        first predicate that fired, or ``None`` if none did (not recovered).
    """
    family = joined.family
    if _stage_advanced(family.current_stage, stall_stage):
        return "stage_advanced"
    if _forms_cleared(joined):
        return "forms_cleared"
    if _funding_recovered(family.funding_state):
        return "deposit_received"
    return None


def derive_recovery_state(
    *,
    joined: JoinedFamily,
    last_contact_at: datetime | None,
    dismissed: bool,
    stall_stage: Stage,
    params: Params,
    cold: bool = False,
    presumed_lost: bool = False,
    lost: bool = False,
    dormant: bool = False,
) -> RecoveryState:
    """Derive a family's :class:`RecoveryState` (A-19) — pure, precedence-ordered.

    A total function of its arguments (no clock, no log, no I/O). The API layer
    resolves the log-derived facts (``last_contact_at`` via ``core/contact_log``,
    ``dismissed`` via the log's ``is_dismissed`` accounting for a superseding
    re-stall) and the ``stall_stage`` (mapped from the family's ``stall_reason``),
    then calls this — the same composition-root pattern as ``derive_contact_status``.

    Args:
        joined: The spine row joined to its source rows — supplies ``current_stage``,
            ``funding_state``, and the enrollment-forms progress.
        last_contact_at: The latest approved-outbound instant (A-14), or None.
        dismissed: Whether a dismiss event holds for the family (A-19), already
            netted against any superseding re-stall by the caller.
        stall_stage: The funnel stage the family was stuck at (the stall-stage
            baseline for the "advanced past" check), resolved at the API layer.
        params: Loaded params (§8); accepted for signature parity (the §5.4
            funding floor mirrors ``funding.tuition_unlock_state``).

    Returns:
        The family's :class:`RecoveryState`.
    """
    del params  # Signature parity; the recovery predicates read structural enums.

    # Precedence (document order is the contract). The later-lifecycle facts (cold/
    # presumed_lost/lost/dormant) are resolved at the API layer and passed IN, same
    # as `dismissed` — they default off so the deriver never fabricates them.
    if dismissed:
        return RecoveryState.DISMISSED
    if dormant:  # long-parked after nurture exhausted (history)
        return RecoveryState.DORMANT
    if lost:  # human-confirmed lost (history); holds over a recovered-looking signal
        return RecoveryState.LOST

    # RECOVERED iff any §5.x signal fired; `recovered_outcome` is the single source
    # of truth for that OR (and exposes WHICH one for the history surface).
    if recovered_outcome(joined, stall_stage=stall_stage) is not None:
        return RecoveryState.RECOVERED

    if presumed_lost:  # accrued silence — surfaced for confirm; dominates working
        return RecoveryState.PRESUMED_LOST
    if last_contact_at is not None:
        return RecoveryState.WORKING
    if cold:  # stalled + uncontacted past the cold threshold (a more-urgent stalled)
        return RecoveryState.COLD

    return RecoveryState.STALLED


def derive_student_recovery_state(
    *,
    current_stage: Stage,
    funding_state: FundingState,
    enrollment_forms: EnrollmentForms | None,
    stall_stage: Stage,
    last_contact_at: datetime | None = None,
    dismissed: bool = False,
) -> RecoveryState:
    """Per-CHILD recovery state (A-24) — same precedence as the family deriver.

    Each child runs its own funnel, so recovery is detected on the STUDENT's own
    signals: ``current_stage`` advanced past its stall stage, its six-form packet
    cleared, or its ``funding_state`` reached the §5.4 first-installment gate.
    Pure and total — reuses the same predicates as :func:`derive_recovery_state`.

    ``last_contact_at`` and ``dismissed`` are the per-child audit facts the API
    layer resolves keyed to (family_id, student_id) — a student-keyed approved
    outbound (A-14) and a per-child dismiss event (A-24) — and passes IN, exactly
    as the family deriver takes them. They default off so a caller with no log
    facts still gets the funnel-only ``RECOVERED``/``STALLED`` split.
    """
    if dismissed:
        return RecoveryState.DISMISSED

    forms_cleared = (
        enrollment_forms is not None
        and enrollment_forms.forms_total > 0
        and enrollment_forms.forms_signed >= enrollment_forms.forms_total
    )
    if (
        _stage_advanced(current_stage, stall_stage)
        or forms_cleared
        or _funding_recovered(funding_state)
    ):
        return RecoveryState.RECOVERED

    if last_contact_at is not None:
        return RecoveryState.WORKING

    return RecoveryState.STALLED


def is_active(state: RecoveryState) -> bool:
    """Whether a state belongs on the ACTIVE recovery board (A-19).

    Active = ``{STALLED, WORKING, COLD, PRESUMED_LOST}`` (still the rep's to work —
    COLD/PRESUMED_LOST are urgency annotations, not removals); history =
    ``{RECOVERED, DISMISSED, LOST, DORMANT}`` (closed out / parked).
    """
    return state in (
        RecoveryState.STALLED,
        RecoveryState.WORKING,
        RecoveryState.COLD,
        RecoveryState.PRESUMED_LOST,
    )

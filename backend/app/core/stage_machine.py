"""Deterministic stage machine — pure function of the source tables (§5.1).

Given a family's source rows (`app_form`, `enrollment_forms`) and its
`stalled_since` timestamp, this module derives the §4.8 `Stage` and assigns the
deterministic `stall_reason`. It is the funnel-state half of §5.1 (the
work-queue scorer is a separate module): no LLM, no adapter, no DB access — just
the §5.1 rule table over the typed models (CLAUDE.md §3 core purity, INV-2).

The §5.1 rule table:

  - ``interest`` — the default: no application has been submitted.
  - ``apply``    — ``app_form.submitted_at`` is set and enrollment has not
    started (no forms signed yet).
  - ``enroll``   — application submitted and ``forms_signed < forms_total``,
    including the case where every form is signed but the funding gate (§5.4)
    has not yet unlocked the tuition step.
  - ``tuition``  — every form is signed AND
    ``enrollment_forms.tuition_step_unlocked`` is true (the funding gate opened
    the tuition step).

Stall reasons (§4.8) are assigned by rule and only when the family has been
stalled longer than ``work_queue.stall_window_days`` — read from params, never
hardcoded (CLAUDE.md INV-11).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from app.core.params import Params
from app.data.models import AppForm, EnrollmentForms, Stage, StallReason


class FamilyInputs(BaseModel):
    """The source rows the stage machine reads — the deriver's pure input (§5.1).

    Each field is nullable because the related row may not exist yet: a family
    in ``interest`` has no `app_form`; one in ``apply`` may have no
    `enrollment_forms`. `stalled_since` comes from `family_record` (§4.1) and
    drives the stall-window comparison.
    """

    model_config = ConfigDict(frozen=True)

    app_form: AppForm | None = None
    enrollment_forms: EnrollmentForms | None = None
    stalled_since: datetime | None = None


def _application_submitted(inputs: FamilyInputs) -> bool:
    """True when the application exists and has been submitted (§4.3)."""
    return inputs.app_form is not None and inputs.app_form.submitted_at is not None


def derive_stage(family_inputs: FamilyInputs, params: Params) -> Stage:
    """Derive the §5.1 funnel `Stage` from the source tables.

    Pure: a deterministic function of `family_inputs` alone. `params` is accepted
    for a uniform deriver signature (and future param-driven stage rules); the
    current §5.1 transitions read no tunable, so it is intentionally unused here.

    Args:
        family_inputs: The family's `app_form` / `enrollment_forms` rows.
        params: Loaded params (§8); present for signature parity.

    Returns:
        The §4.8 `Stage` per the §5.1 rule table.
    """
    del params  # No tunable drives the stage transitions (§5.1); signature parity.

    if not _application_submitted(family_inputs):
        return Stage.INTEREST

    enrollment = family_inputs.enrollment_forms
    if enrollment is None or enrollment.forms_signed == 0:
        return Stage.APPLY

    forms_complete = enrollment.forms_signed >= enrollment.forms_total
    if forms_complete and enrollment.tuition_step_unlocked:
        return Stage.TUITION

    return Stage.ENROLL


def _is_stalled(stalled_since: datetime | None, window_days: int, now: datetime) -> bool:
    """True when `stalled_since` is older than the stall window (§5.1, INV-11)."""
    if stalled_since is None:
        return False
    return now - stalled_since > timedelta(days=window_days)


def derive_stall_reason(
    family_inputs: FamilyInputs,
    params: Params,
    *,
    now: datetime | None = None,
) -> StallReason | None:
    """Assign the §4.8 `stall_reason` by the §5.1 rule, or ``None`` if not stalled.

    A family is only flagged once it has been stalled longer than
    ``work_queue.stall_window_days`` (read from params, never hardcoded — INV-11).
    The reason is keyed off the family's current stage:

      - ``app_incomplete``  — application started but never submitted.
      - ``forms_partial``   — ``0 < forms_signed < forms_total``.
      - ``funding_pending`` — every form signed but tuition still locked.

    A family that has progressed (tuition unlocked) is not stalled.

    Args:
        family_inputs: The family's source rows plus `stalled_since`.
        params: Loaded params; supplies ``work_queue.stall_window_days``.
        now: Reference time for the window comparison; defaults to UTC now.
            Injectable so the rule is deterministic under test.

    Returns:
        The assigned `StallReason`, or ``None`` when the family is not stalled.
    """
    reference = now if now is not None else datetime.now(UTC)
    window_days = params.work_queue.stall_window_days
    if not _is_stalled(family_inputs.stalled_since, window_days, reference):
        return None

    # Application started but never submitted.
    if not _application_submitted(family_inputs):
        return StallReason.APP_INCOMPLETE

    enrollment = family_inputs.enrollment_forms
    if enrollment is None or enrollment.forms_signed == 0:
        # Submitted, enrollment not started: still moving, no enroll-stage stall.
        return None

    if enrollment.forms_signed < enrollment.forms_total:
        return StallReason.FORMS_PARTIAL

    # Every form signed: stalled only if the funding gate has not unlocked tuition.
    if not enrollment.tuition_step_unlocked:
        return StallReason.FUNDING_PENDING

    return None

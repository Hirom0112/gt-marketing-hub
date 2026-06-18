"""Stage-machine deriver tests (S0; ARCHITECTURE.md §5.1, §4.8, §4.3, §4.4).

The stage machine is a **pure function of the source tables** (§5.1): given the
`app_form`, `enrollment_forms`, and the family's `stalled_since`, it returns the
§4.8 `Stage` and assigns the deterministic `stall_reason`. No LLM, no adapter,
no DB — just the rule table in §5.1.

Branch coverage (§5.1):
  - `interest` by default (no application submitted);
  - `apply` once `app_form.submitted_at` is set and enrollment not started;
  - `enroll` while `forms_signed < forms_total`;
  - `tuition` only when forms complete AND the funding gate unlocked the tuition
    step (`enrollment_forms.tuition_step_unlocked = true`).

The stall window is read from params (`work_queue.stall_window_days`), never
hardcoded (CLAUDE.md INV-11): the tests pass the committed example file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.params import Params, load_params
from app.core.stage_machine import FamilyInputs, derive_stage
from app.data.models import AppForm, EnrollmentForms, Stage

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _app_form(*, submitted: bool) -> AppForm:
    """An `app_form`; `submitted` toggles `submitted_at` (§4.3)."""
    return AppForm(
        app_form_id=uuid4(),
        family_id=uuid4(),
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC) if submitted else None,
    )


def _enrollment(*, signed: int, total: int = 6, tuition_unlocked: bool = False) -> EnrollmentForms:
    """An `enrollment_forms` row (§4.4)."""
    return EnrollmentForms(
        enrollment_form_id=uuid4(),
        family_id=uuid4(),
        forms_total=total,
        forms_signed=signed,
        tuition_step_unlocked=tuition_unlocked,
    )


def test_stage_derived_from_source_tables() -> None:
    """`derive_stage` returns the §5.1 stage for every branch of the rule table.

    Pure function of the source tables: no application ⇒ ``interest``; submitted
    application with enrollment not started ⇒ ``apply``; partial forms ⇒
    ``enroll``; all forms signed but tuition still locked ⇒ ``enroll`` (the
    funding gate has not unlocked); all forms signed AND tuition unlocked ⇒
    ``tuition``.
    """
    params = _params()

    # interest — default: no application submitted (started or absent).
    assert derive_stage(FamilyInputs(), params) is Stage.INTEREST
    assert derive_stage(FamilyInputs(app_form=_app_form(submitted=False)), params) is Stage.INTEREST

    # apply — application submitted, enrollment not started.
    assert derive_stage(FamilyInputs(app_form=_app_form(submitted=True)), params) is Stage.APPLY

    # apply — submitted, enrollment row exists but zero forms signed.
    assert (
        derive_stage(
            FamilyInputs(
                app_form=_app_form(submitted=True),
                enrollment_forms=_enrollment(signed=0),
            ),
            params,
        )
        is Stage.APPLY
    )

    # enroll — submitted and forms in progress (0 < signed < total).
    assert (
        derive_stage(
            FamilyInputs(
                app_form=_app_form(submitted=True),
                enrollment_forms=_enrollment(signed=3),
            ),
            params,
        )
        is Stage.ENROLL
    )

    # enroll — all forms signed but the funding gate has NOT unlocked tuition.
    assert (
        derive_stage(
            FamilyInputs(
                app_form=_app_form(submitted=True),
                enrollment_forms=_enrollment(signed=6, tuition_unlocked=False),
            ),
            params,
        )
        is Stage.ENROLL
    )

    # tuition — forms complete AND the funding gate unlocked the tuition step.
    assert (
        derive_stage(
            FamilyInputs(
                app_form=_app_form(submitted=True),
                enrollment_forms=_enrollment(signed=6, tuition_unlocked=True),
            ),
            params,
        )
        is Stage.TUITION
    )

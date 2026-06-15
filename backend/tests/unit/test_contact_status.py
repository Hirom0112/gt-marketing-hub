"""Contact-status recency deriver tests (S9 W1; ANALYSIS/enrollment-gap-analysis.md).

`derive_contact_status` is the PURE deterministic color-system deriver: given a
family's `created_at`, its derived `last_contact_at` (from the audit log; A-14),
an injected `now`, and whether it is funded, it returns the §contact-recency
:class:`ContactStatus` the deal view / board color-code on. No I/O, no
`datetime.now` — `now` is injected (core purity). Thresholds come from
`params.enrollment.contact` (INV-11): a hardcoded threshold must fail the
drift test.

Rules (LOCKED): funded ⇒ CLOSED; else contacted ⇒ FOLLOWED_UP; else
(uncontacted) `age_days = (now - created_at).days` with `age >= overdue_days`
⇒ OVERDUE, else FRESH. The 4th-day rule: age 3 ⇒ FRESH, age 4 ⇒ OVERDUE
(overdue_days=4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.contact_status import ContactStatus, derive_contact_status

from app.core.params import Params, load_params

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _created(age_days: int) -> datetime:
    """A `created_at` that is exactly `age_days` whole days before NOW."""
    return NOW - timedelta(days=age_days)


def test_fresh_when_uncontacted_and_young() -> None:
    """Uncontacted family with age <= grey_window_days ⇒ FRESH (grey)."""
    params = _params()
    status = derive_contact_status(
        created_at=_created(2),
        last_contact_at=None,
        now=NOW,
        funded=False,
        params=params,
    )
    assert status is ContactStatus.FRESH


def test_overdue_when_uncontacted_at_threshold() -> None:
    """Uncontacted family with age >= overdue_days ⇒ OVERDUE (red).

    The 4th-day rule: overdue_days=4, so age 4 flips to OVERDUE (age 3 is still
    FRESH — proven by `test_fresh_at_grey_window_boundary`).
    """
    params = _params()
    status = derive_contact_status(
        created_at=_created(4),
        last_contact_at=None,
        now=NOW,
        funded=False,
        params=params,
    )
    assert status is ContactStatus.OVERDUE


def test_fresh_at_grey_window_boundary() -> None:
    """Age exactly grey_window_days (3) is still FRESH; age 4 is OVERDUE."""
    params = _params()
    assert (
        derive_contact_status(
            created_at=_created(3),
            last_contact_at=None,
            now=NOW,
            funded=False,
            params=params,
        )
        is ContactStatus.FRESH
    )


def test_followed_up_when_contacted_not_funded() -> None:
    """A contacted family that is not yet won ⇒ FOLLOWED_UP (light-green)."""
    params = _params()
    status = derive_contact_status(
        created_at=_created(10),  # old, but contacted ⇒ not overdue
        last_contact_at=NOW - timedelta(days=1),
        now=NOW,
        funded=False,
        params=params,
    )
    assert status is ContactStatus.FOLLOWED_UP


def test_closed_when_funded() -> None:
    """A funded (won) family ⇒ CLOSED, regardless of contact/age."""
    params = _params()
    status = derive_contact_status(
        created_at=_created(99),
        last_contact_at=None,  # funded wins even with no contact logged
        now=NOW,
        funded=True,
        params=params,
    )
    assert status is ContactStatus.CLOSED


def test_threshold_read_from_params_not_hardcoded() -> None:
    """Drift guard: the overdue threshold comes from params, never hardcoded.

    With overdue_days lowered to 2, an age-2 uncontacted family — FRESH under
    the committed params — must flip to OVERDUE. A hardcoded `>= 4` would fail
    this (INV-11).
    """
    params = _params()
    drifted = params.model_copy(
        update={
            "enrollment": params.enrollment.model_copy(
                update={
                    "contact": params.enrollment.contact.model_copy(
                        update={"grey_window_days": 1, "overdue_days": 2}
                    )
                }
            )
        }
    )
    status = derive_contact_status(
        created_at=_created(2),
        last_contact_at=None,
        now=NOW,
        funded=False,
        params=drifted,
    )
    assert status is ContactStatus.OVERDUE

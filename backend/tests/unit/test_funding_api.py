"""Funding + seam-signal endpoint tests (FR-2.6/2.7; ARCH §6; INV-10).

Acceptance tests for the S3 funding API — the deterministic core (TEFA math +
the §5.4 funding-state machine) surfaced over REST:

  ``GET  /families/{id}/funding``        — funding view: state + tier + installments.
  ``POST /families/{id}/funding/signal`` — advance the §5.4 state on a GT-controlled
                                           signal (INV-10), recompute the view.

Every number asserted here comes from the same pure core the tests in
``test_funding_math.py`` / ``test_funding_state.py`` pin — these tests prove the
core is wired behind HTTP faithfully, not that the math is re-derived in the API.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.funding_gate import (
    advance_funding_state,
    compute_installments,
    tuition_step_unlocked,
)
from app.core.params import Params
from app.data.models import FundingState, FundingType
from app.data.repository import InMemoryFamilyRepository
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + no stray dependency overrides per test."""
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def _repo() -> InMemoryFamilyRepository:
    return deps.get_repository()  # type: ignore[return-value]


def _params() -> Params:
    return deps.get_params()


def _family_of_type(funding_type: FundingType):
    """A seeded family with the given funding tier (first match)."""
    for family in _repo().list_families():
        if family.funding_type == funding_type:
            return family
    raise AssertionError(f"no seeded family with funding_type={funding_type}")


def _family_in_state(funding_state: FundingState, funding_type: FundingType):
    """A seeded TEFA family currently in the given funding state."""
    for family in _repo().list_families():
        if family.funding_type == funding_type and family.funding_state == funding_state:
            return family
    raise AssertionError(f"no seeded family state={funding_state} type={funding_type}")


def test_funding_view_returns_state_tier_and_installments() -> None:
    """A TEFA-standard family shows its state, tier and the 25/25/50 schedule."""
    family = _family_of_type(FundingType.TEFA_STANDARD)
    params = _params()
    expected = compute_installments(FundingType.TEFA_STANDARD, params)

    resp = client.get(f"/families/{family.family_id}/funding")
    assert resp.status_code == 200
    body = resp.json()

    assert body["family_id"] == str(family.family_id)
    assert body["funding_type"] == FundingType.TEFA_STANDARD.value
    assert body["funding_state"] == family.funding_state.value
    # Installments serialize as strings (Decimal→str), matching compute_installments.
    assert body["installments"] == [str(amount) for amount in expected]
    assert body["tuition_unlocked"] == tuition_step_unlocked(family.funding_state, params)


def test_funding_view_tefa_standard_schedule_is_25_25_50() -> None:
    """The standard award splits 25/25/50 to [2618.50, 2618.50, 5237.00]."""
    family = _family_of_type(FundingType.TEFA_STANDARD)
    resp = client.get(f"/families/{family.family_id}/funding")
    assert resp.status_code == 200
    assert resp.json()["installments"] == ["2618.50", "2618.50", "5237.00"]


def test_funding_view_tuition_locked_until_first_installment() -> None:
    """Tuition stays locked for a pre-installment state, unlocked at/after it."""
    params = _params()
    locked = _family_in_state(FundingState.AWARDED_SELFREPORT, FundingType.TEFA_STANDARD)
    resp = client.get(f"/families/{locked.family_id}/funding")
    assert resp.json()["tuition_unlocked"] is False
    assert tuition_step_unlocked(FundingState.AWARDED_SELFREPORT, params) is False


def test_funding_signal_advances_state_and_unlocks_tuition() -> None:
    """A first-installment signal advances the state and flips tuition unlocked."""
    family = _family_in_state(FundingState.GT_CONFIRMED, FundingType.TEFA_STANDARD)
    advanced = advance_funding_state(
        FundingState.GT_CONFIRMED, FundingState.FIRST_INSTALLMENT_RECEIVED
    )

    resp = client.post(
        f"/families/{family.family_id}/funding/signal",
        json={"first_installment_received": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funding_state"] == advanced.value
    assert body["funding_state"] == FundingState.FIRST_INSTALLMENT_RECEIVED.value
    assert body["tuition_unlocked"] is True


def test_family_selected_signal_advances_to_selected_gt() -> None:
    """The R2 `family_selected` GT-controlled signal advances AWARDED_SELFREPORT → SELECTED_GT."""
    family = _family_in_state(FundingState.AWARDED_SELFREPORT, FundingType.TEFA_STANDARD)
    advanced = advance_funding_state(FundingState.AWARDED_SELFREPORT, FundingState.SELECTED_GT)

    resp = client.post(
        f"/families/{family.family_id}/funding/signal",
        json={"family_selected": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funding_state"] == advanced.value
    assert body["funding_state"] == FundingState.SELECTED_GT.value
    # Still locked: the selection gap sits far below the first-installment floor.
    assert body["tuition_unlocked"] is False


def test_funding_signal_illegal_advance_does_not_crash() -> None:
    """An illegal advance is rejected (409/422), never a 500."""
    # A family already at the threshold: a first-installment signal would be a
    # skip/no-op illegal transition from its current state.
    family = _family_in_state(FundingState.FIRST_INSTALLMENT_RECEIVED, FundingType.TEFA_STANDARD)
    resp = client.post(
        f"/families/{family.family_id}/funding/signal",
        json={"first_installment_received": True},
    )
    assert resp.status_code in (409, 422)


def test_funding_view_unknown_family_404() -> None:
    """An unknown family id is a clean 404, not a 500."""
    resp = client.get(f"/families/{uuid4()}/funding")
    assert resp.status_code == 404


def test_funding_view_self_pay_has_no_installments_no_500() -> None:
    """A SELF_PAY family returns a funding view with null installments, no 500."""
    family = _family_of_type(FundingType.SELF_PAY)
    resp = client.get(f"/families/{family.family_id}/funding")
    assert resp.status_code == 200
    body = resp.json()
    assert body["funding_type"] == FundingType.SELF_PAY.value
    assert body["installments"] is None
    # Tuition lock still reported deterministically from the state.
    assert isinstance(body["tuition_unlocked"], bool)


def test_funding_signal_unknown_family_404() -> None:
    """A signal for an unknown family is a clean 404."""
    resp = client.post(
        f"/families/{uuid4()}/funding/signal",
        json={"first_installment_received": True},
    )
    assert resp.status_code == 404

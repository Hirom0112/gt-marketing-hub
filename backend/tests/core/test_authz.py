"""Authorization-predicate tests (B1; CLAUDE.md §4.1, §7, INV-2/INV-11).

``core/authz.permits`` is the single authorization decision the API route guards
and the Decision-Queue ``can_decide`` (B2) read. It evaluates a (role,
permission) pair against the committed ``rbac`` matrix (permission → roles,
default-deny). These tests read the matrix from params, so a param drift fails
the build (INV-11): an unknown permission grants nobody, and an unknown role
holds nothing.
"""

from __future__ import annotations

from pathlib import Path

from app.core.authz import permits
from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_operator_lacks_decision_queue_view() -> None:
    """An operator does NOT hold the leadership decision-queue permission."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("operator", "decision_queue.view", params=params) is False


def test_leader_holds_decision_queue_view() -> None:
    """A leader holds the leadership decision-queue permission."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("leader", "decision_queue.view", params=params) is True


def test_admin_holds_decision_queue_view() -> None:
    """An admin holds the leadership decision-queue permission."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("admin", "decision_queue.view", params=params) is True


def test_operator_holds_families_view() -> None:
    """An operator HAS families.view (every role can view family records)."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("operator", "families.view", params=params) is True


def test_unknown_permission_is_default_deny() -> None:
    """An unknown permission grants nobody (default-deny)."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("leader", "nonexistent.permission", params=params) is False


def test_unknown_role_holds_nothing() -> None:
    """A role absent from the matrix is granted nothing (fail-closed predicate)."""
    params = load_params(EXAMPLE_PARAMS)
    assert permits("intruder", "families.view", params=params) is False

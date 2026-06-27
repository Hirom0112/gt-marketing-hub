"""rbac params-block tests (B1; CLAUDE.md §4.1, §7, INV-11).

The ``rbac`` block is the single canonical home (INV-11) for the three-role
permission matrix the authz core and the API ``require_role`` read. Roles are
``admin`` / ``leader`` / ``operator``; ``permissions`` maps a named permission
to the list of roles that hold it (permission → roles), so a ``permits`` lookup
is ``role in permissions.get(perm, [])``. ``load_params`` parses the block into
the typed :class:`Rbac` model; a dangling role or an empty/incomplete roles list
fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import Rbac, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_rbac_block_loads() -> None:
    """The rbac block loads into a typed model with the three canonical roles."""
    rbac = load_params(EXAMPLE_PARAMS).rbac

    assert isinstance(rbac, Rbac)
    assert "admin" in rbac.roles
    assert "leader" in rbac.roles
    assert "operator" in rbac.roles

    # decision_queue.view is a leader+admin permission — NOT held by operator.
    decision_queue_view = rbac.permissions["decision_queue.view"]
    assert "admin" in decision_queue_view
    assert "leader" in decision_queue_view
    assert "operator" not in decision_queue_view


def test_rbac_rejects_dangling_role() -> None:
    """A permission referencing a role absent from ``roles`` fails the build."""
    with pytest.raises(ValidationError):
        Rbac(
            roles=["admin", "leader", "operator"],
            permissions={"decision_queue.view": ["admin", "ghost"]},
            demo_token_ttl_seconds=3600,
        )


def test_rbac_requires_canonical_roles() -> None:
    """An empty roles list, or one missing a canonical role, is rejected."""
    with pytest.raises(ValidationError):
        Rbac(roles=[], permissions={}, demo_token_ttl_seconds=3600)

    with pytest.raises(ValidationError):
        Rbac(roles=["admin", "leader"], permissions={}, demo_token_ttl_seconds=3600)

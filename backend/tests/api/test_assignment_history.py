"""Assignment-history endpoint tests (LEAD_ASSIGNMENT.md §10/§10a; LA-23).

Acceptance tests for ``GET /families/{family_id}/assignments`` — the per-family
ownership-history timeline the deal view drills into. It returns the immutable
``lead_assignment`` rows (from→to/reason) for ONE family, owner-scoped server-side
through the same :func:`resolve_owner_scope` IDOR clamp every owner-scoped read
uses (INV-5): an agent may read only a family in its OWN book; an admin may read
any. The rows are append-only (a reassignment never overwrites — it appends a
second from→to fact), so the timeline is the durable audit record (NFR-6).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.sales_agents import SALES_AGENTS
from app.data.repository import UNASSIGNED, FamilyRepository, InMemoryFamilyRepository
from app.data.synthetic import generate_demo_cohort
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt
from tests.conftest import install_test_principal_override

client = TestClient(app)

_A = SALES_AGENTS[0].agent_id  # FL closer
_B = SALES_AGENTS[1].agent_id  # CA qualifier
_fresh_repo: list[FamilyRepository] = []


def _admin_headers() -> dict[str, str]:
    """A signed admin JWT (B1 verified principal)."""
    return {"Authorization": f"Bearer {mint_jwt(role='admin', secret=TEST_JWT_SECRET)}"}


def _operator_headers(agent_id: UUID) -> dict[str, str]:
    """A signed operator JWT for ``agent_id`` (B1 verified principal)."""
    token = mint_jwt(role="operator", agent_id=agent_id, secret=TEST_JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    # Re-assert the conftest token-aware principal shim wiped by the clear() above.
    install_test_principal_override()
    repo = InMemoryFamilyRepository(generate_demo_cohort(params=deps._params), params=deps._params)
    _fresh_repo[:] = [repo]
    app.dependency_overrides[deps.get_repository] = lambda: repo
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    _fresh_repo.clear()


def _repo() -> InMemoryFamilyRepository:
    repo = _fresh_repo[0]
    assert isinstance(repo, InMemoryFamilyRepository)
    return repo


def _route_an_fl_lead() -> UUID:
    """Auto-route the FL intake lead (→ Agent A) and return its family_id."""
    repo = _repo()
    fl_lead = next(f for f in repo.list_families(owner=UNASSIGNED) if f.state == "FL")
    resp = client.post(
        "/enrollment/leads/auto-assign", json={"family_ids": [str(fl_lead.family_id)]}
    )
    assert resp.status_code == 200, resp.text
    return fl_lead.family_id


def test_history_returns_the_append_only_rows() -> None:
    fid = _route_an_fl_lead()
    resp = client.get(f"/families/{fid}/assignments", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    (row,) = rows
    # The from→to/reason fact: out of intake (from None) to the FL closer, explained.
    assert row["family_id"] == str(fid)
    assert row["from_rep_id"] is None
    assert row["to_rep_id"] == str(_A)
    assert row["reason"], "every history row carries a human-readable reason"
    assert row["assigned_by"] == "router"


def test_history_grows_append_only_on_reassignment() -> None:
    """A reassignment appends a SECOND from→to fact — it never overwrites the first."""
    fid = _route_an_fl_lead()
    repo = _repo()
    # Reassign FL → B by hand through the same history seam the SLA sweep uses.
    repo.assign_families([fid], _B, datetime.now(UTC))
    repo.append_assignment_event(
        family_id=fid,
        from_rep_id=_A,
        to_rep_id=_B,
        routed_role="qualifier",
        assigned_by="router",
        reason="sla-reassign: unworked past timer",
    )
    rows = client.get(f"/families/{fid}/assignments", headers=_admin_headers()).json()
    assert len(rows) == 2
    assert [r["to_rep_id"] for r in rows] == [str(_A), str(_B)]


def test_history_is_owner_scoped_admin_sees_any() -> None:
    fid = _route_an_fl_lead()
    # Admin (default) reads the FL family's history fine.
    resp = client.get(f"/families/{fid}/assignments", headers=_admin_headers())
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_history_is_owner_scoped_owning_agent_sees_own() -> None:
    fid = _route_an_fl_lead()  # now owned by Agent A
    resp = client.get(
        f"/families/{fid}/assignments",
        headers=_operator_headers(_A),
    )
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_history_is_owner_scoped_foreign_agent_blocked() -> None:
    """A rep that does NOT own the family cannot read its ownership history (IDOR
    defense, INV-5): the foreign book is 404, never leaked."""
    fid = _route_an_fl_lead()  # owned by Agent A
    resp = client.get(
        f"/families/{fid}/assignments",
        headers=_operator_headers(_B),
    )
    assert resp.status_code == 404


def test_history_unknown_family_is_404() -> None:
    resp = client.get(f"/families/{uuid4()}/assignments", headers=_admin_headers())
    assert resp.status_code == 404


def test_seeded_owned_family_serves_its_baseline_history() -> None:
    """LA-23 — an ALREADY-OWNED demo family (no live routing) still serves a seeded
    baseline history fact through the endpoint, so the timeline shows provenance the
    moment an operator taps in — for both the admin and the OWNING agent."""
    repo = _repo()
    owned = next(f for f in repo.list_families() if f.assigned_rep_id is not None)

    # Admin sees the seeded baseline row.
    rows = client.get(f"/families/{owned.family_id}/assignments", headers=_admin_headers()).json()
    assert len(rows) == 1
    assert rows[0]["from_rep_id"] is None
    assert rows[0]["to_rep_id"] == str(owned.assigned_rep_id)
    assert rows[0]["assigned_by"] == "seed" and rows[0]["reason"]

    # The OWNING agent sees its own deal's history too (owner-scoped, not just admin).
    own = client.get(
        f"/families/{owned.family_id}/assignments",
        headers=_operator_headers(owned.assigned_rep_id),
    )
    assert own.status_code == 200 and len(own.json()) == 1

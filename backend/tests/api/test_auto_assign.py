"""Auto-assign endpoint integration tests (LEAD_ASSIGNMENT.md §2; LA-20).

Acceptance tests for ``POST /enrollment/leads/auto-assign`` — the deterministic
router wired into the write path: it routes the UNASSIGNED intake pool by the §2
precedence, persists ``assigned_rep_id`` (promoting a resolved self-reported
owner), appends an immutable ``lead_assignment`` history row, persists the pool
cursor, and logs the reason to the audit spine (NFR-6). Existing owners are never
reassigned (the duplicate-lead guard); a HELD lead is fail-closed.

Driven over the curated 12-household demo cohort (territory-consistent: closer=FL,
qualifier=CA) — its three UNASSIGNED leads exercise FL→closer, CA→qualifier, and
the TX territory-fallback path.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.sales_agents import SALES_AGENTS
from app.data.repository import UNASSIGNED, FamilyRepository, InMemoryFamilyRepository
from app.data.synthetic import generate_demo_cohort
from app.main import app

client = TestClient(app)

_A = SALES_AGENTS[0].agent_id  # FL closer
_B = SALES_AGENTS[1].agent_id  # CA qualifier
_fresh_repo: list[FamilyRepository] = []


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
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


def test_auto_assign_routes_unassigned_pool_with_reasons() -> None:
    repo = _repo()
    pool_before = repo.list_families(owner=UNASSIGNED)
    assert pool_before, "the demo cohort has an unassigned intake pool"

    resp = client.post("/enrollment/leads/auto-assign", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Every unassigned lead got a decision; the demo pool routes cleanly (no holds).
    assert body["counts"]["assigned"] == len(pool_before)
    assert body["counts"]["held"] == 0
    by_state = {}
    for r in body["results"]:
        assert r["reason"], "every decision carries a human-readable reason"
        fam = repo.get_family(UUID(r["family_id"]))
        assert fam is not None
        by_state[fam.family.state] = r

    # FL → the closer (Agent A); CA → the qualifier (Agent B); TX → fallback pool.
    assert (
        by_state["FL"]["agent_id"] == str(_A) and "territory: state=FL" in by_state["FL"]["reason"]
    )
    assert (
        by_state["CA"]["agent_id"] == str(_B) and "territory: state=CA" in by_state["CA"]["reason"]
    )
    assert by_state["TX"]["rule"] == "fallback" and "uncovered" in by_state["TX"]["reason"]

    # The writes landed + the append-only history recorded each one with its reason.
    for r in body["results"]:
        fam = repo.get_family(UUID(r["family_id"]))
        assert fam is not None and fam.family.assigned_rep_id is not None
        history = repo.list_assignments(fam.family.family_id)
        assert len(history) == 1
        assert history[0].to_rep_id is not None and history[0].reason == r["reason"]
        assert history[0].assigned_by == "router"


def test_auto_assign_logs_reason_to_audit_spine() -> None:
    client.post("/enrollment/leads/auto-assign", json={})
    log = deps.get_observability_log()
    proposals = [p for p in log.list_proposals() if p.flow == "assignment"]
    assert proposals, "each assignment is logged to the audit spine (NFR-6)"
    # The WHY is logged, not just the WHO.
    assert any("reason" in p.payload and p.payload["reason"] for p in proposals)


def test_existing_owner_is_never_reassigned() -> None:
    repo = _repo()
    # An already-owned family (the demo seeds several). Routing it explicitly must
    # return owner-match and leave its owner unchanged (the duplicate-lead guard).
    owned = next(f for f in repo.list_families() if f.assigned_rep_id is not None)
    prior_owner = owned.assigned_rep_id
    # The owned demo family carries its seeded baseline history fact (LA-23); the
    # owner-match no-op must not append ON TOP of it.
    history_before = len(repo.list_assignments(owned.family_id))

    resp = client.post("/enrollment/leads/auto-assign", json={"family_ids": [str(owned.family_id)]})
    assert resp.status_code == 200, resp.text
    (result,) = resp.json()["results"]
    assert result["owner_match"] is True
    assert result["agent_id"] == str(prior_owner)
    # No NEW history row is appended for a no-op owner-match (nothing changed).
    assert len(repo.list_assignments(owned.family_id)) == history_before
    reread = repo.get_family(owned.family_id)
    assert reread is not None and reread.family.assigned_rep_id == prior_owner


def test_duplicate_lead_does_not_double_assign() -> None:
    repo = _repo()
    fl_lead = next(f for f in repo.list_families(owner=UNASSIGNED) if f.state == "FL")
    # First run assigns it (FL → A).
    client.post("/enrollment/leads/auto-assign", json={"family_ids": [str(fl_lead.family_id)]})
    first = repo.get_family(fl_lead.family_id)
    assert first is not None and first.family.assigned_rep_id == _A

    # Second run on the SAME (now-owned) lead is an owner-match, not a re-route —
    # the owner is unchanged and no second history row is appended.
    resp = client.post(
        "/enrollment/leads/auto-assign", json={"family_ids": [str(fl_lead.family_id)]}
    )
    (result,) = resp.json()["results"]
    assert result["owner_match"] is True and result["agent_id"] == str(_A)
    assert len(repo.list_assignments(fl_lead.family_id)) == 1  # only the first write


def test_work_queue_row_exposes_assignment_contract() -> None:
    """The triage/work-queue row carries the assignment contract (assigned_rep_id +
    assigned_at) the rep-calendar workstream reads (LEAD_ASSIGNMENT.md §10a)."""
    rows = client.get(
        "/work-queue", params={"scope": "all"}, headers={"X-Demo-Role": "admin"}
    ).json()
    assert rows, "the demo cohort produces work-queue rows"
    for row in rows:
        assert "assigned_rep_id" in row and "assigned_at" in row
    # ≥1 assigned demo family surfaces a non-null owner + assignment date.
    owned = [r for r in rows if r["assigned_rep_id"] is not None]
    assert owned and all(r["assigned_at"] is not None for r in owned)

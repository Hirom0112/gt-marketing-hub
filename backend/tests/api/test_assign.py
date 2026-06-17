"""M4 bulk-assign endpoint tests (TODO.md M4; ARCH §7.1; INV-2/NFR-6).

Acceptance tests for ``POST /enrollment/families/bulk-assign`` — the
DETERMINISTIC write that routes families to a sales agent: it writes
``assigned_rep_id`` + ``assigned_at`` to each named family (the deterministic
core owns the write — INV-2; this is NOT an LLM/proposal-eval path) and logs a
decision to the audit spine (NFR-6: who/what/when). An unknown ``agent_id`` is
rejected 4xx (fail-closed against the static agent registry). A 1-element list
covers the single-assign case.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.sales_agents import SALES_AGENTS
from app.data.repository import FamilyRepository
from app.main import app

client = TestClient(app)

# A FRESH in-memory store per test so the assignment WRITE (which mutates
# assigned_rep_id) never leaks into the shared module singleton or another test.
_fresh_repo: list[FamilyRepository] = []


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + a fresh repo override, no stray overrides per test."""
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    repo = deps._build_in_memory_repository(deps._params)
    _fresh_repo[:] = [repo]
    app.dependency_overrides[deps.get_repository] = lambda: repo
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    _fresh_repo.clear()


def _repo() -> FamilyRepository:
    return _fresh_repo[0]


def test_bulk_assign_writes_and_logs() -> None:
    repo = _repo()
    # Two real seeded families (a 2-element batch; a 1-element list also works).
    families = repo.list_families()[:2]
    assert len(families) == 2, "fixture cohort must have >=2 families"
    family_ids = [str(f.family_id) for f in families]
    agent = SALES_AGENTS[0]

    response = client.post(
        "/enrollment/families/bulk-assign",
        json={"family_ids": family_ids, "agent_id": str(agent.agent_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["assigned"] == 2
    assert set(body["assigned"]) == set(family_ids)

    # The write landed: re-reading each family proves assigned_rep_id + assigned_at.
    for f in families:
        reread = repo.get_family(f.family_id)
        assert reread is not None
        assert reread.family.assigned_rep_id == agent.agent_id
        assert reread.family.assigned_at is not None

    # The decision is logged to the audit spine (NFR-6): one proposal per family,
    # each with a recorded human decision (who/what/when). It is a DETERMINISTIC
    # write, not an LLM call — so there is NO eval attached (no proposal-eval path).
    log = deps.get_observability_log()
    proposals = [p for p in log.list_proposals() if p.flow == "assignment"]
    assert len(proposals) == 2
    logged_families = {p.family_id for p in proposals}
    assert logged_families == {f.family_id for f in families}
    for p in proposals:
        # The proposal carries WHAT (the assigned agent) and is a plain
        # deterministic write — no eval gate ran on it.
        assert p.payload.get("agent_id") == str(agent.agent_id)
        audit = log.get_audit(p.proposal_id)
        assert audit is not None
        assert audit.evals == []  # no eval ⇒ not an AI/proposal-eval path (INV-2).
        assert len(audit.decisions) == 1  # WHO/WHEN: the human decision.
        assert audit.decisions[0].human  # a non-empty operator identity.


def test_bulk_assign_single_family() -> None:
    """A 1-element list is the single-assign case (no separate route needed)."""
    repo = _repo()
    family = repo.list_families()[0]
    agent = SALES_AGENTS[0]

    response = client.post(
        "/enrollment/families/bulk-assign",
        json={"family_ids": [str(family.family_id)], "agent_id": str(agent.agent_id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["assigned"] == 1
    reread = repo.get_family(family.family_id)
    assert reread is not None
    assert reread.family.assigned_rep_id == agent.agent_id


def test_bulk_assign_unknown_agent_is_rejected() -> None:
    """An ``agent_id`` not in the static registry is rejected 4xx (fail-closed)."""
    repo = _repo()
    family = repo.list_families()[0]

    response = client.post(
        "/enrollment/families/bulk-assign",
        json={"family_ids": [str(family.family_id)], "agent_id": str(uuid4())},
    )
    assert 400 <= response.status_code < 500, response.text

    # Fail-closed: nothing was assigned and nothing was logged to the spine.
    reread = repo.get_family(family.family_id)
    assert reread is not None
    assert reread.family.assigned_rep_id is None
    log = deps.get_observability_log()
    assert [p for p in log.list_proposals() if p.flow == "assignment"] == []

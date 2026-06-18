"""SLA-sweep endpoint tests (LEAD_ASSIGNMENT.md §9; LA-15/LA-16).

``POST /enrollment/leads/sla-sweep`` reassigns leads left UNWORKED past the SLA
timer. The ``owned_breach`` policy governs the action: ``alert`` (default) flags
without moving (the "one source of truth" stance); ``auto_reassign`` reroutes AWAY
from the breached rep, appends a from→to history row, re-stamps the timer, and
logs the reason. Anti-ping-pong: after ``max_reassignments`` hops the lead is
escalated to intake. ``as_of`` injects a deterministic clock.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.params import Params
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.data.synthetic import generate_demo_cohort
from app.main import app

client = TestClient(app)

# Well past every demo family's (this-week) assigned_at, so all are breached.
_FUTURE = datetime(2026, 7, 1, tzinfo=UTC).isoformat()
_fresh_repo: list[FamilyRepository] = []


def _auto_reassign_params() -> Params:
    base = deps._params
    sla = base.assignment.sla.model_copy(update={"owned_breach": "auto_reassign"})
    asg = base.assignment.model_copy(update={"sla": sla})
    return base.model_copy(update={"assignment": asg})


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


def test_alert_mode_flags_but_does_not_reassign() -> None:
    repo = _repo()
    owners_before = {f.family_id: f.assigned_rep_id for f in repo.list_families()}

    resp = client.post("/enrollment/leads/sla-sweep", json={"as_of": _FUTURE})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Default owned_breach=alert: breached leads are ALERTED, none reassigned.
    assert body["counts"]["alerted"] > 0
    assert body["counts"]["reassigned"] == 0 and body["counts"]["escalated"] == 0
    assert all(r["action"] == "alerted" and "sla-alert" in r["reason"] for r in body["results"])
    # Ownership is UNCHANGED — alert never silently moves a lead (one source of truth).
    for f in repo.list_families():
        assert f.assigned_rep_id == owners_before[f.family_id]


def test_auto_reassign_reroutes_away_from_breached_rep() -> None:
    repo = _repo()
    app.dependency_overrides[deps.get_params] = _auto_reassign_params

    resp = client.post("/enrollment/leads/sla-sweep", json={"as_of": _FUTURE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["reassigned"] > 0

    reassigned = [r for r in body["results"] if r["action"] == "reassigned"]
    for r in reassigned:
        # The new owner differs from the breached one (rerouted AWAY), and the
        # write + from→to history landed with the sla-reassign reason.
        assert r["to_rep_id"] != r["from_rep_id"]
        assert "sla-reassign" in r["reason"]
        from uuid import UUID

        history = repo.list_assignments(UUID(r["family_id"]))
        latest = history[-1]
        assert str(latest.from_rep_id) == r["from_rep_id"]
        assert str(latest.to_rep_id) == r["to_rep_id"]


def test_worked_lead_is_not_swept() -> None:
    repo = _repo()
    app.dependency_overrides[deps.get_params] = _auto_reassign_params
    log = deps.get_observability_log()

    # Pick an assigned family and log a CONTACT (a non-assignment outbound approve)
    # at as_of — so it reads as worked-since-assignment and must NOT breach.
    owned = next(f for f in repo.list_families() if f.assigned_rep_id is not None)
    from uuid import uuid4

    from app.observability.log_store import DecisionAction

    pid = uuid4()
    log.log_proposal(
        proposal_id=pid,
        flow="nudge",  # an outbound-contact flow, NOT 'assignment'
        schema_version="1",
        payload={},
        family_id=owned.family_id,
        created_at=datetime(2026, 6, 30, tzinfo=UTC),
    )
    log.log_decision(
        proposal_id=pid,
        human="operator",
        action=DecisionAction.APPROVE,
        created_at=datetime(2026, 6, 30, tzinfo=UTC),
    )

    body = client.post("/enrollment/leads/sla-sweep", json={"as_of": _FUTURE}).json()
    swept_ids = {r["family_id"] for r in body["results"]}
    assert str(owned.family_id) not in swept_ids, "a worked lead is not swept"


def test_escalates_to_intake_after_max_reassignments() -> None:
    repo = _repo()
    app.dependency_overrides[deps.get_params] = _auto_reassign_params

    # Pre-seed a prior SLA hop on an assigned family (max_reassignments=1 in params)
    # so the next breach exceeds the cap → escalate to intake, not another rotation.
    owned = next(f for f in repo.list_families() if f.assigned_rep_id is not None)
    repo.append_assignment_event(
        family_id=owned.family_id,
        from_rep_id=None,
        to_rep_id=owned.assigned_rep_id,
        routed_role="closer",
        assigned_by="sla-sweep",
        reason="sla-reassign: a prior hop",
    )

    body = client.post("/enrollment/leads/sla-sweep", json={"as_of": _FUTURE}).json()
    result = next(r for r in body["results"] if r["family_id"] == str(owned.family_id))
    assert result["action"] == "escalated" and result["to_rep_id"] is None
    # The lead returned to the intake pool (assigned_rep_id NULL).
    reread = repo.get_family(owned.family_id)
    assert reread is not None and reread.family.assigned_rep_id is None

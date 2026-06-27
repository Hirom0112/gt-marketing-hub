"""M3 per-agent roll-up — the admin-lens roster aggregation test (PLAN M3 R1).

``GET /enrollment/agents`` is the admin lens's per-agent roster: each agent's
``queue_size`` / ``stall_rate`` / ``close_rate`` / ``load`` plus an ``unowned``
bucket (the intake pool). The HARD constraint (PLAN M3 R1; MULTI_AGENT_COCKPIT §4):
the roll-up is a PURE AGGREGATION over the SAME derivations ``/work-queue`` already
computes, grouped by ``assigned_rep_id`` — **no new scoring math**.

This test proves "no new scoring math" by deriving every expected metric from the
EXISTING helpers the work-queue route reuses — ``_recovery_state_for`` (the
work-queue's recovery deriver) and ``recovered_outcome`` (the existing close
signal in ``app/core/recovery_state.py``) — NOT from a new formula. If the
endpoint invented its own recoverability/close math, these assertions break.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from app.api import deps
from app.api.families import _recovery_state_for, _stall_stage
from app.core.recovery_state import RecoveryState, is_active, recovered_outcome
from app.data.repository import UNASSIGNED, InMemoryFamilyRepository, JoinedFamily, OwnerScope
from app.data.synthetic import SyntheticDataset, generate
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

client = TestClient(app)

# The two canonical seeded demo agents (0013_sales_agents.sql; stable per-rank uuids).
AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1, closer (Riley Carter)
AGENT_2 = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2, setter (Jordan Avery)


def _admin_headers() -> dict[str, str]:
    """A signed admin JWT (B1 verified principal)."""
    return {"Authorization": f"Bearer {mint_jwt(role='admin', secret=TEST_JWT_SECRET)}"}


def teardown_function() -> None:
    app.dependency_overrides.pop(deps.get_repository, None)
    deps.reset_observability_log()


def _seed_assigned_repo() -> tuple[InMemoryFamilyRepository, list[UUID], list[UUID], list[UUID]]:
    """Build an in-memory repo split across the two demo agents + an unassigned pool.

    Reuses the synthetic cohort and STAMPS ``assigned_rep_id``: the first N rows to
    agent #1, the next M to agent #2, the rest unassigned (the intake pool). A known
    cohort with a mix of stalled/recovered/working derivations falls out of the
    generator already; the test asserts the roll-up equals the EXISTING-helper
    aggregation over exactly these rows.
    """
    base = generate(n=24, seed=42)
    families = list(base.families)
    a1_ids: list[UUID] = []
    a2_ids: list[UUID] = []
    unassigned_ids: list[UUID] = []
    for i, fam in enumerate(families):
        if i < 8:
            families[i] = fam.model_copy(update={"assigned_rep_id": AGENT_1})
            a1_ids.append(fam.family_id)
        elif i < 14:
            families[i] = fam.model_copy(update={"assigned_rep_id": AGENT_2})
            a2_ids.append(fam.family_id)
        else:
            families[i] = fam.model_copy(update={"assigned_rep_id": None})
            unassigned_ids.append(fam.family_id)
    dataset = SyntheticDataset(
        families=families,
        leads=list(base.leads),
        app_forms=list(base.app_forms),
        enrollment_forms=list(base.enrollment_forms),
        community_profiles=list(base.community_profiles),
        students=list(base.students),
        student_app_forms=list(base.student_app_forms),
        student_enrollment_forms=list(base.student_enrollment_forms),
    )
    repo = InMemoryFamilyRepository(dataset)
    return repo, a1_ids, a2_ids, unassigned_ids


def _expected_metrics(
    repo: InMemoryFamilyRepository,
    owner: OwnerScope,
    *,
    now: datetime,
    cap: int,
) -> tuple[int, float, float, float]:
    """Derive (queue_size, stall_rate, close_rate, load) for an owner the SAME way.

    Re-creates the roll-up over the EXISTING work-queue derivations — the active
    pre-filter (``stalled_since is not None``), the work-queue recovery deriver
    (``_recovery_state_for``), and the existing close signal (``recovered_outcome``).
    No new formula: this IS the contract the endpoint must satisfy.
    """
    log = deps.get_observability_log()
    params = deps.get_params()
    # The work-queue active candidate set: families that were ever stalled (the
    # exact `scope=active` pre-filter in get_work_queue).
    candidates: list[JoinedFamily] = [
        j for j in repo.list_joined(owner=owner) if j.family.stalled_since is not None
    ]
    states = [_recovery_state_for(j, log=log, now=now, params=params) for j in candidates]
    active = [s for s in states if is_active(s)]
    queue_size = len(active)
    stalled = sum(1 for s in active if s is RecoveryState.STALLED)
    stall_rate = (stalled / queue_size) if queue_size else 0.0
    recovered = sum(
        1 for j in candidates if recovered_outcome(j, stall_stage=_stall_stage(j)) is not None
    )
    close_rate = (recovered / len(candidates)) if candidates else 0.0
    load = queue_size / cap
    return queue_size, stall_rate, close_rate, load


def test_per_agent_metrics() -> None:
    """GET /enrollment/agents rolls each agent up over the EXISTING work-queue math.

    Asserts per-agent {queue_size, stall_rate, close_rate, load} and the unowned
    bucket EQUAL the values derived by re-running the existing helpers
    (`_recovery_state_for` / `recovered_outcome`) over the same fixture — the
    "no new scoring math" property (PLAN M3 R1). Also asserts the unowned bucket
    count equals K (the seeded intake-pool size).
    """
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()

    params = deps.get_params()
    cap = params.assignment.per_tier_load_cap

    # The endpoint reads `now` once per request; the derivations are stable across
    # the small window between the two reads, so deriving expected at a fixed `now`
    # matches the route's own derivation for this synthetic, time-anchored cohort.
    now = datetime.now(UTC)
    exp_a1 = _expected_metrics(repo, AGENT_1, now=now, cap=cap)
    exp_a2 = _expected_metrics(repo, AGENT_2, now=now, cap=cap)
    exp_unowned = _expected_metrics(repo, UNASSIGNED, now=now, cap=cap)

    resp = client.get("/enrollment/agents", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()

    agents = {a["agent_id"]: a for a in body["agents"]}
    assert str(AGENT_1) in agents, "the roster must include every demo agent"
    assert str(AGENT_2) in agents

    a1 = agents[str(AGENT_1)]
    assert a1["queue_size"] == exp_a1[0]
    assert a1["stall_rate"] == round(exp_a1[1], 4)
    assert a1["close_rate"] == round(exp_a1[2], 4)
    assert a1["load"] == round(exp_a1[3], 4)
    # Identity comes from the registry, not a recomputation.
    assert a1["synthetic_name"] == "Riley Carter"
    assert a1["tier"] == "closer"

    a2 = agents[str(AGENT_2)]
    assert a2["queue_size"] == exp_a2[0]
    assert a2["stall_rate"] == round(exp_a2[1], 4)
    assert a2["close_rate"] == round(exp_a2[2], 4)
    assert a2["load"] == round(exp_a2[3], 4)
    assert a2["synthetic_name"] == "Jordan Avery"
    assert a2["tier"] == "setter"

    # The unowned bucket — the intake pool (`assigned_rep_id IS NULL`, owner=none).
    unowned = body["unowned"]
    assert unowned["queue_size"] == exp_unowned[0]
    assert unowned["stall_rate"] == round(exp_unowned[1], 4)
    assert unowned["close_rate"] == round(exp_unowned[2], 4)
    assert unowned["load"] == round(exp_unowned[3], 4)

    # The unowned bucket counts exactly the K seeded intake-pool families that flow
    # through the active work-queue derivation (queue_size is that active count).
    log = deps.get_observability_log()
    active_unowned = [
        j
        for j in repo.list_joined(owner=UNASSIGNED)
        if j.family.stalled_since is not None
        and is_active(_recovery_state_for(j, log=log, now=now, params=params))
    ]
    assert unowned["queue_size"] == len(active_unowned)
    assert set(j.family.family_id for j in active_unowned) <= set(unassigned_ids)

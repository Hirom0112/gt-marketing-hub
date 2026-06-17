"""M1 server-side role scoping — the IDOR-atonement test (MULTI_AGENT_COCKPIT §2.6/§4/§6).

The single most important security property in this product: a sales rep MUST NOT
be able to read another rep's families, enforced SERVER-SIDE (not just hidden in
the UI). This is the deny-by-default owner scoping that atones for the original
IDOR (CLAUDE INV-5; D-RLS-4).

The demo principal is the app-layer stand-in for ``auth.uid()``: a FastAPI
dependency reading ``X-Demo-Role`` + ``X-Demo-Agent-Id`` → ``{role, agent_id,
tier}``. The ROLE — never the client-supplied ``owner`` query param — decides the
effective scope:

- ``role=agent`` (+ agent_id) ⇒ effective scope is ALWAYS owner==self. An agent
  that passes ``owner=all`` or another agent's id STILL gets only its own rows
  (the server CLAMPS it). This is the IDOR defense.
- ``role=admin`` ⇒ may use ``owner=<id>|all|none`` (sees everyone by default; can
  slice any agent; can view the unowned pool with ``owner=none``).

The principal reads ONLY the two headers and never touches ``service_role``.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app.api import deps
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset, generate
from app.main import app

client = TestClient(app)

# The two canonical seeded demo agents (0013_sales_agents.sql; the migration's
# stable per-rank uuid literals — rank→agent is a static lookup).
AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1, closer (Riley Carter)
AGENT_2 = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2, setter (Jordan Avery)

_HEADER_ROLE = "X-Demo-Role"
_HEADER_AGENT_ID = "X-Demo-Agent-Id"


def teardown_function() -> None:
    app.dependency_overrides.pop(deps.get_repository, None)


def _seed_assigned_repo() -> tuple[InMemoryFamilyRepository, list[UUID], list[UUID], list[UUID]]:
    """Build an in-memory repo whose families are split across the two demo agents.

    Reuses the synthetic generator's cohort and STAMPS ``assigned_rep_id`` onto the
    rows: the first few to agent #1, the next few to agent #2, and the remainder
    left unassigned (``assigned_rep_id is None`` — the intake pool). Returns the
    repo plus the three id buckets so the test can assert membership/absence.
    """
    base = generate(n=24, seed=42)
    families = list(base.families)
    a1_ids: list[UUID] = []
    a2_ids: list[UUID] = []
    unassigned_ids: list[UUID] = []
    for i, fam in enumerate(families):
        if i < 3:
            families[i] = fam.model_copy(update={"assigned_rep_id": AGENT_1})
            a1_ids.append(fam.family_id)
        elif i < 6:
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


def _work_queue_ids(*, headers: dict[str, str], params: str = "") -> set[UUID]:
    """GET /work-queue?scope=all (the unfiltered cohort) and return the family ids.

    ``scope=all`` so the assertion is about OWNER scoping, not the active-recovery
    derivation — every owned/unowned row is eligible, and only the role clamp can
    remove a foreign row.
    """
    url = "/work-queue?scope=all"
    if params:
        url = f"{url}&{params}"
    resp = client.get(url, headers=headers)
    assert resp.status_code == 200, resp.text
    return {UUID(row["family_id"]) for row in resp.json()}


def test_rep_cannot_read_foreign_rows() -> None:
    """An agent principal sees ONLY its own rows — even when it asks for more (IDOR)."""
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    agent1_headers = {_HEADER_ROLE: "agent", _HEADER_AGENT_ID: str(AGENT_1)}

    # Baseline: agent #1 sees only its own families.
    ids = _work_queue_ids(headers=agent1_headers)
    assert set(a1_ids) <= ids, "agent #1 must see all of its own rows"
    for fid in a2_ids:
        assert fid not in ids, "agent #1 must NOT see agent #2's rows (IDOR defense)"
    for fid in unassigned_ids:
        assert fid not in ids, "agent #1 must NOT see the unassigned pool"

    # The clamp: an agent that asks for owner=all STILL gets only its own rows.
    ids_all = _work_queue_ids(headers=agent1_headers, params="owner=all")
    assert ids_all == ids, "owner=all from an agent must be clamped to self"

    # The clamp: an agent that asks for ANOTHER agent's id STILL gets only its own.
    ids_other = _work_queue_ids(headers=agent1_headers, params=f"owner={AGENT_2}")
    assert ids_other == ids, "owner=<other agent> from an agent must be clamped to self"
    for fid in a2_ids:
        assert fid not in ids_other, "the clamp must keep agent #2's rows absent"


def test_admin_sees_all_and_can_slice_any_owner() -> None:
    """An admin sees everyone by default, can slice one agent, and can view the pool."""
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    admin_headers = {_HEADER_ROLE: "admin"}  # no agent id

    # Default (no owner param) ⇒ all rows.
    ids_all = _work_queue_ids(headers=admin_headers)
    for fid in a1_ids + a2_ids + unassigned_ids:
        assert fid in ids_all, "admin default must see every row"

    # owner=<agent #2> ⇒ only agent #2's rows.
    ids_a2 = _work_queue_ids(headers=admin_headers, params=f"owner={AGENT_2}")
    assert set(a2_ids) <= ids_a2
    for fid in a1_ids + unassigned_ids:
        assert fid not in ids_a2, "owner=<agent #2> must exclude others + the pool"

    # owner=none ⇒ only the unassigned pool.
    ids_none = _work_queue_ids(headers=admin_headers, params="owner=none")
    assert set(unassigned_ids) <= ids_none
    for fid in a1_ids + a2_ids:
        assert fid not in ids_none, "owner=none must return only the unassigned pool"


def test_demo_principal_resolves_role_agent_and_tier() -> None:
    """get_demo_principal maps the headers → {role, agent_id, tier}; agent #1 = closer."""
    from app.api.deps import get_demo_principal

    principal = get_demo_principal(demo_role="agent", demo_agent_id=str(AGENT_1))
    assert principal.role == "agent"
    assert principal.agent_id == AGENT_1
    assert principal.tier == "closer"  # rank 1 ≤ closer_rank_max=1

    setter = get_demo_principal(demo_role="agent", demo_agent_id=str(AGENT_2))
    assert setter.tier == "setter"  # rank 2 > closer_rank_max=1

    admin = get_demo_principal(demo_role="admin", demo_agent_id=None)
    assert admin.role == "admin"
    assert admin.agent_id is None


def test_demo_principal_never_touches_service_role() -> None:
    """The principal carries no DB-role/service_role attribute, and never references it.

    Lightweight guard (the real proof is the foreign-read=0 behavior above): the
    demo principal is an APP-LAYER scope, never an RLS-bypass DB role (D-RLS-4).
    """
    from app.api.deps import DemoPrincipal, get_demo_principal

    principal = get_demo_principal(demo_role="agent", demo_agent_id=str(AGENT_1))
    assert not hasattr(principal, "service_role")
    assert not hasattr(principal, "db_role")
    assert set(DemoPrincipal.model_fields) == {"role", "agent_id", "tier"}

    # The dependency CODE (not its docstring/comments — which legitimately state
    # the invariant) must never reference service_role: no service_role_key, no
    # `.service_role` attribute access, no build_supabase_repository / live store.
    src = Path(deps.__file__).read_text(encoding="utf-8")
    func_start = src.index("def get_demo_principal")
    func_end = src.index("def resolve_owner_scope", func_start)
    code_lines = [
        line
        for line in src[func_start:func_end].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    # Drop the docstring block (the triple-quoted region) so prose mentions of the
    # invariant don't trip the guard — only executable lines remain.
    code = "\n".join(code_lines)
    in_doc = code.split('"""')
    executable = "".join(in_doc[0::2])  # the segments OUTSIDE the docstring quotes
    assert "service_role" not in executable, "get_demo_principal code must not touch service_role"
    assert "build_supabase_repository" not in executable

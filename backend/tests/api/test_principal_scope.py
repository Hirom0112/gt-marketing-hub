"""Verified-principal owner scoping — the IDOR-atonement test (MULTI_AGENT_COCKPIT §2.6/§4/§6).

The single most important security property in this product: a sales rep MUST NOT be
able to read another rep's families, enforced SERVER-SIDE (not just hidden in the
UI). This is the deny-by-default owner scoping that atones for the original IDOR
(CLAUDE INV-5; D-RLS-4).

The B1 rewrite (fixes the audit's top finding, S1): scope is keyed off the VERIFIED
principal — a signed Supabase JWT whose role lives ONLY in ``app_metadata.role`` —
NOT a client-spelled header anyone could forge. The role decides the effective scope:

- ``role=operator`` (+ agent_id) ⇒ effective scope is ALWAYS owner==self. An operator
  that passes ``owner=all`` or another agent's id STILL gets only its own rows (the
  server CLAMPS it). This is the IDOR defense.
- ``role=leader`` / ``role=admin`` ⇒ may use ``owner=<id>|all|none`` (sees everyone by
  default; can slice any agent; can view the unowned pool with ``owner=none``).

The S1 regression (``test_no_token_denied_not_admin``) proves the deleted spoofable
header is gone for good: with the conftest admin-on-no-token convenience removed, an
unauthenticated request to an owner-scoped route is DENIED (401), never admin.
"""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from app.api import deps
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset, generate
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

client = TestClient(app)

# The two canonical seeded demo agents (0013_sales_agents.sql; the migration's
# stable per-rank uuid literals — rank→agent is a static lookup).
AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1, closer (Riley Carter)
AGENT_2 = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2, setter (Jordan Avery)


def teardown_function() -> None:
    app.dependency_overrides.pop(deps.get_repository, None)


def _operator_headers(agent_id: UUID) -> dict[str, str]:
    """A signed operator JWT for ``agent_id`` (the verified successor to the deleted
    client-supplied role header)."""
    token = mint_jwt(role="operator", agent_id=agent_id, secret=TEST_JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


def _role_headers(role: str) -> dict[str, str]:
    """A signed JWT for a cross-agent role (``leader`` / ``admin``)."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


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


def test_operator_cannot_read_foreign_rows() -> None:
    """An operator principal sees ONLY its own rows — even when it asks for more (IDOR)."""
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    agent1_headers = _operator_headers(AGENT_1)

    # Baseline: operator #1 sees only its own families.
    ids = _work_queue_ids(headers=agent1_headers)
    assert set(a1_ids) <= ids, "operator #1 must see all of its own rows"
    for fid in a2_ids:
        assert fid not in ids, "operator #1 must NOT see agent #2's rows (IDOR defense)"
    for fid in unassigned_ids:
        assert fid not in ids, "operator #1 must NOT see the unassigned pool"

    # The clamp: an operator that asks for owner=all STILL gets only its own rows.
    ids_all = _work_queue_ids(headers=agent1_headers, params="owner=all")
    assert ids_all == ids, "owner=all from an operator must be clamped to self"

    # The clamp: an operator that asks for ANOTHER agent's id STILL gets only its own
    # (the crown-jewel IDOR assertion: a rep cannot read a foreign rep's book).
    ids_other = _work_queue_ids(headers=agent1_headers, params=f"owner={AGENT_2}")
    assert ids_other == ids, "owner=<other agent> from an operator must be clamped to self"
    for fid in a2_ids:
        assert fid not in ids_other, "the clamp must keep agent #2's rows absent"


def test_admin_sees_all_and_can_slice_any_owner() -> None:
    """An admin sees everyone by default, can slice one agent, and can view the pool."""
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    admin_headers = _role_headers("admin")

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


def test_leader_has_cross_agent_view_like_admin() -> None:
    """A ``leader`` principal honors ``requested_owner`` (the cross-agent leadership lens)."""
    repo, a1_ids, a2_ids, unassigned_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    leader_headers = _role_headers("leader")

    # Default ⇒ every row (like admin).
    ids_all = _work_queue_ids(headers=leader_headers)
    for fid in a1_ids + a2_ids + unassigned_ids:
        assert fid in ids_all, "leader default must see every row"

    # And a leader may slice one agent's book.
    ids_a1 = _work_queue_ids(headers=leader_headers, params=f"owner={AGENT_1}")
    assert set(a1_ids) <= ids_a1
    for fid in a2_ids:
        assert fid not in ids_a1, "leader owner=<agent #1> must exclude agent #2"


def test_get_principal_maps_operator_tier() -> None:
    """get_principal maps app_metadata.role/agent_id and resolves the operator tier."""
    from app.api.deps import get_principal
    from app.core.settings import Settings

    settings = Settings(supabase_jwt_secret=TEST_JWT_SECRET)
    token = mint_jwt(role="operator", agent_id=AGENT_1, secret=TEST_JWT_SECRET)
    principal = get_principal(settings=settings, authorization=f"Bearer {token}")
    assert principal.role == "operator"
    assert principal.agent_id == AGENT_1
    assert principal.tier == "closer"  # rank 1 ≤ closer_rank_max=1

    setter_token = mint_jwt(role="operator", agent_id=AGENT_2, secret=TEST_JWT_SECRET)
    setter = get_principal(settings=settings, authorization=f"Bearer {setter_token}")
    assert setter.tier == "setter"  # rank 2 > closer_rank_max=1


def test_principal_never_carries_service_role() -> None:
    """The verified principal is an APP-LAYER scope, never an RLS-bypass DB role (D-RLS-4)."""
    from app.api.deps import Principal

    principal = Principal(role="operator", agent_id=AGENT_1)
    assert not hasattr(principal, "service_role")
    assert not hasattr(principal, "db_role")
    assert set(Principal.model_fields) == {"role", "user_id", "agent_id", "tier"}


def test_no_token_denied_not_admin() -> None:
    """S1 regression: with the test admin-on-no-token shim removed, NO Authorization ⇒
    401 (the production default-DENY) — NEVER admin/200. This is the proof the deleted
    spoofable header can no longer make an unauthenticated request a privileged one.
    """
    repo, _a1, _a2, _pool = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    # Remove ONLY the conftest get_principal convenience so the REAL default-deny runs
    # (get_settings_dep stays overridden with the test secret ⇒ verification IS
    # configured, so a no-token request is a "missing bearer token" 401, the meaningful
    # deny — not a "not configured" one).
    app.dependency_overrides.pop(deps.get_principal, None)

    resp = client.get("/work-queue?scope=all")  # no Authorization header
    assert resp.status_code == 401, (
        f"a no-token request to an owner-scoped route must be DENIED (401), not admin: {resp.text}"
    )

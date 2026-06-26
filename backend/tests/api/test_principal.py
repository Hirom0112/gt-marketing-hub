"""Verified-JWT principal tests (B1; fixes S1) — the deny-by-default identity gate.

The S1 audit finding: the demo principal trusts a CLIENT-SPELLED ``X-Demo-Role``
header, so anyone can claim ``admin``. The replacement reads a SIGNED Supabase JWT
(``Authorization: Bearer``), verifies it against ``SUPABASE_JWT_SECRET``, and trusts
the role ONLY from ``app_metadata.role`` — NEVER ``user_metadata`` (which is
client-writable in Supabase; RESEARCH_v2 §II.5). This unit is additive: it does NOT
touch ``get_demo_principal`` (T4b migrates consumers).

HTTP-code mapping under test:
- 401 — token-level failure: missing/blank header, malformed/forged/expired token,
  or no verifying secret configured (fail-closed; NEVER default-allow).
- 403 — a VALID, unexpired token whose role is absent from ``app_metadata`` (e.g.
  present only in ``user_metadata``), is not one of the three roles, or is not
  among the roles ``require_role`` permits.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.deps import Principal, get_principal, require_role
from app.core.settings import Settings
from tests.api._jwt import mint_jwt

_SECRET = "test-supabase-jwt-secret-deadbeef"
# The rank-1 closer agent (0013_sales_agents.sql) — used for the operator tier path.
_AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")

# The leader/admin guard, built at MODULE level so FastAPI can resolve it from the
# route's (string, PEP 563) annotation — a closure-local guard is invisible to
# `get_type_hints` and the route param would degrade to a query param.
_LEADER_GUARD = require_role("leader", "admin")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A tiny app with a leader/admin-gated route, the test JWT secret injected.

    ``get_settings_dep`` is overridden so ``get_principal`` verifies against the
    test secret without touching the real environment.
    """
    test_app = FastAPI()

    @test_app.get("/leader-only")
    def leader_only(principal: Annotated[Principal, Depends(_LEADER_GUARD)]) -> dict:
        return {"role": principal.role}

    test_app.dependency_overrides[deps.get_settings_dep] = lambda: Settings(
        supabase_jwt_secret=_SECRET
    )
    with TestClient(test_app) as test_client:
        yield test_client


def test_valid_leader_jwt_allowed(client: TestClient) -> None:
    """A signed JWT with app_metadata.role=leader is ALLOWED (the trusted path)."""
    token = mint_jwt(role="leader", secret=_SECRET)
    resp = client.get("/leader-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "leader"


def test_role_only_in_user_metadata_denied(client: TestClient) -> None:
    """A role present ONLY in user_metadata (client-writable) is IGNORED ⇒ 403."""
    token = mint_jwt(role="leader", secret=_SECRET, metadata_key="user_metadata")
    resp = client.get("/leader-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text


def test_forged_signature_unauthorized(client: TestClient) -> None:
    """A token signed with the wrong secret → 401 (forged)."""
    token = mint_jwt(role="leader", secret="not-the-real-secret")
    resp = client.get("/leader-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text


def test_expired_token_unauthorized(client: TestClient) -> None:
    """A token whose exp is in the past → 401 (expired)."""
    token = mint_jwt(role="leader", secret=_SECRET, exp_delta=-3600)
    resp = client.get("/leader-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text


def test_missing_authorization_header_unauthorized(client: TestClient) -> None:
    """No Authorization header → 401 (default-DENY, NOT default-admin — the S1 fix)."""
    resp = client.get("/leader-only")
    assert resp.status_code == 401, resp.text


def test_require_role_rejects_operator(client: TestClient) -> None:
    """A valid Operator JWT against a leader/admin-gated route → 403 (role not permitted)."""
    token = mint_jwt(role="operator", secret=_SECRET, agent_id=_AGENT_1)
    resp = client.get("/leader-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text


def test_get_principal_maps_operator_tier() -> None:
    """get_principal maps sub→user_id, app_metadata.agent_id→agent_id, resolves tier."""
    settings = Settings(supabase_jwt_secret=_SECRET)
    sub = "11111111-1111-4111-8111-111111111111"
    token = mint_jwt(role="operator", secret=_SECRET, agent_id=_AGENT_1, sub=sub)
    principal = get_principal(settings=settings, authorization=f"Bearer {token}")
    assert principal.role == "operator"
    assert principal.user_id == UUID(sub)
    assert principal.agent_id == _AGENT_1
    assert principal.tier == "closer"  # rank 1 ≤ closer_rank_max=1


def test_get_principal_fail_closed_without_secret() -> None:
    """With no configured secret, get_principal fails closed (401) — never default-allow."""
    from fastapi import HTTPException

    settings = Settings(supabase_jwt_secret=None)
    token = mint_jwt(role="admin", secret=_SECRET)
    with pytest.raises(HTTPException) as exc:
        get_principal(settings=settings, authorization=f"Bearer {token}")
    assert exc.value.status_code == 401

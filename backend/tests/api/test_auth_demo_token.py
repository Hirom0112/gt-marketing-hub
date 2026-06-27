"""Demo-login token endpoint tests (B1 task 5a) — the demo-auth bridge.

``POST /auth/demo-token`` is the DEMO equivalent of a Supabase login: it mints a
REAL signed HS256 JWT for a chosen seat so the frontend can authenticate against
the verified-principal gate (``get_principal``). It is the analogue of the INV-9
simulated adapters — issued ONLY in ``AUTH_MODE=demo`` over synthetic data
(INV-1), and ABSENT (404) in a live deployment where real Supabase issues tokens.

The round-trip under test is the whole point: a token this endpoint mints is
ACCEPTED, unchanged, by the real :func:`get_principal` on an owner-scoped route —
same secret, role read from ``app_metadata`` — proving the bridge actually
authenticates rather than just returning a string. This is NOT the spoofable S1
header: S1 was an UNVERIFIED, client-spelled role defaulting to admin; this issues
VERIFIED signed tokens, only in demo mode, and the backend verifies every request.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.auth import router as auth_router
from app.api.deps import Principal, get_principal
from app.core.settings import Settings
from tests.api._jwt import TEST_JWT_SECRET

# A real rank-1 closer agent (0013_sales_agents.sql) for the operator-scoping case.
_AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")


def _make_client(*, auth_mode: str = "demo", secret: str | None = TEST_JWT_SECRET) -> TestClient:
    """A tiny app mounting the auth router + a probe route guarded by the REAL principal.

    ``get_settings_dep`` is overridden to the requested ``auth_mode`` + secret so
    the mint endpoint and the verifying probe share one settings snapshot — the
    round-trip runs the production verifier, not the conftest shim (which targets
    only the main app).
    """
    test_app = FastAPI()
    test_app.include_router(auth_router)

    @test_app.get("/_probe")
    def probe(principal: Annotated[Principal, Depends(get_principal)]) -> dict:
        return {
            "role": principal.role,
            "agent_id": None if principal.agent_id is None else str(principal.agent_id),
        }

    test_app.dependency_overrides[deps.get_settings_dep] = lambda: Settings(
        auth_mode=auth_mode,  # type: ignore[arg-type]
        supabase_jwt_secret=secret,
    )
    return TestClient(test_app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _make_client() as test_client:
        yield test_client


def test_leader_token_round_trips_as_leader(client: TestClient) -> None:
    """A minted leader token is ACCEPTED by the real principal gate as a leader."""
    resp = client.post("/auth/demo-token", json={"role": "leader"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    token = body["access_token"]
    assert token  # a non-empty signed JWS

    # The round-trip: the minted token authenticates against the verified principal.
    probe = client.get("/_probe", headers={"Authorization": f"Bearer {token}"})
    assert probe.status_code == 200, probe.text
    assert probe.json()["role"] == "leader"


def test_operator_token_is_scoped_to_its_agent(client: TestClient) -> None:
    """An operator seat carries its agent_id into app_metadata ⇒ scoped principal."""
    resp = client.post("/auth/demo-token", json={"role": "operator", "agent_id": str(_AGENT_1)})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]

    probe = client.get("/_probe", headers={"Authorization": f"Bearer {token}"})
    assert probe.status_code == 200, probe.text
    body = probe.json()
    assert body["role"] == "operator"
    assert body["agent_id"] == str(_AGENT_1)


def test_unknown_role_rejected(client: TestClient) -> None:
    """A role outside params.rbac.roles → 422 (not a valid seat)."""
    resp = client.post("/auth/demo-token", json={"role": "superuser"})
    assert resp.status_code == 422, resp.text


def test_live_mode_endpoint_is_absent(client: TestClient) -> None:
    """In AUTH_MODE=live the endpoint does NOT exist (404) — real Supabase issues tokens."""
    with _make_client(auth_mode="live") as live_client:
        resp = live_client.post("/auth/demo-token", json={"role": "leader"})
        assert resp.status_code == 404, resp.text


def test_no_secret_configured_cannot_sign(client: TestClient) -> None:
    """Demo mode but no JWT secret ⇒ 503 (can't sign) — fail loud, never a blank token."""
    with _make_client(secret=None) as no_secret_client:
        resp = no_secret_client.post("/auth/demo-token", json={"role": "leader"})
        assert resp.status_code == 503, resp.text

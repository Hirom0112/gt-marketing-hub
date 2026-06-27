"""Decision-Queue API tests (B2) — the leader-gate + open-submit + decide path.

The headline invariant is the split gate (spec Module 11): VIEWING the queue
requires a ``leader``/``admin`` JWT (the admin has full module access), but
DECIDING on an item is ``leader``-only — an admin may view yet is 403 on the act
route, and an ``operator`` is 403 on both. SUBMITTING a decision (the "any module /
anyone flags an item" path) is open to ANY authenticated principal — an operator
may enqueue. A no-token request inherits the default-deny 401 from ``get_principal``.

These tests hit the REAL main app (with ``decisions_router`` registered), overriding
only :func:`app.api.deps.get_decisions_store` to a fresh in-memory store per test.
The autouse conftest principal shim verifies Bearer tokens against the test secret,
so a minted operator/leader/admin JWT drives the real role gate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.data.decisions_store import InMemoryDecisionsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture
def store() -> InMemoryDecisionsStore:
    """A fresh in-memory decisions store per test (full isolation)."""
    return InMemoryDecisionsStore()


@pytest.fixture
def client(store: InMemoryDecisionsStore) -> Iterator[TestClient]:
    """The main app with the decisions store overridden to the per-test in-memory one."""
    from app.main import app

    app.dependency_overrides[deps.get_decisions_store] = lambda: store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_decisions_store, None)


# --------------------------------------------------------------------------- gate
def test_get_decisions_operator_forbidden(client: TestClient) -> None:
    """Operator JWT → GET /decisions → 403 (the headline leader-gate)."""
    resp = client.get("/decisions", headers=_auth("operator"))
    assert resp.status_code == 403, resp.text


def test_get_decisions_leader_ok(client: TestClient) -> None:
    """Leader JWT → GET /decisions → 200 (the leadership view)."""
    resp = client.get("/decisions", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_get_decisions_admin_ok(client: TestClient) -> None:
    """Admin JWT → GET /decisions → 200 (admin has full module access — VIEW only;
    the decide path is leader-only, asserted by test_action_admin_forbidden)."""
    resp = client.get("/decisions", headers=_auth("admin"))
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------- open submit
def test_submit_open_to_operator_then_visible_to_leader(client: TestClient) -> None:
    """An OPERATOR may submit (anyone flags); the open item then appears for the leader."""
    submit = client.post(
        "/decisions",
        headers=_auth("operator"),
        json={"source": "nurture", "payload": {"family": "synthetic-123"}},
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["state"] == "open"
    assert body["source"] == "nurture"
    decision_id = body["id"]

    queue = client.get("/decisions", headers=_auth("leader"))
    assert queue.status_code == 200, queue.text
    ids = [row["id"] for row in queue.json()]
    assert decision_id in ids


# -------------------------------------------------------------------------- decide
def test_action_approve_decides(client: TestClient) -> None:
    """Leader approves an open decision → it transitions to ``decided``."""
    decision_id = client.post(
        "/decisions",
        headers=_auth("operator"),
        json={"source": "budget", "payload": {"q": "shift spend?"}},
    ).json()["id"]

    resp = client.post(
        f"/decisions/{decision_id}/action",
        headers=_auth("leader"),
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "decided"


def test_action_need_info_without_comment_unprocessable(client: TestClient) -> None:
    """need_info with no comment → 422 (the state machine requires a comment)."""
    decision_id = client.post(
        "/decisions",
        headers=_auth("operator"),
        json={"source": "field", "payload": {}},
    ).json()["id"]

    resp = client.post(
        f"/decisions/{decision_id}/action",
        headers=_auth("leader"),
        json={"action": "need_info"},
    )
    assert resp.status_code == 422, resp.text


def test_action_operator_forbidden(client: TestClient) -> None:
    """An operator hitting the decide route → 403 (leader-gated action)."""
    decision_id = client.post(
        "/decisions",
        headers=_auth("operator"),
        json={"source": "seam", "payload": {}},
    ).json()["id"]

    resp = client.post(
        f"/decisions/{decision_id}/action",
        headers=_auth("operator"),
        json={"action": "approve"},
    )
    assert resp.status_code == 403, resp.text


def test_action_admin_forbidden(client: TestClient) -> None:
    """An ADMIN hitting the decide route → 403. Spec Module 11 reserves decision-
    making to leadership; the admin may VIEW the queue but never decide."""
    decision_id = client.post(
        "/decisions",
        headers=_auth("admin"),
        json={"source": "budget", "payload": {}},
    ).json()["id"]

    resp = client.post(
        f"/decisions/{decision_id}/action",
        headers=_auth("admin"),
        json={"action": "approve"},
    )
    assert resp.status_code == 403, resp.text


def test_action_unknown_decision_not_found(client: TestClient) -> None:
    """A decide on a non-existent decision id → 404."""
    resp = client.post(
        "/decisions/00000000-0000-4000-8000-000000000000/action",
        headers=_auth("leader"),
        json={"action": "approve"},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------- default-deny
def test_no_token_unauthorized(client: TestClient) -> None:
    """No Authorization header → 401 (default-deny inherited from get_principal)."""
    from app.main import app

    # Pop the autouse admin-on-no-token shim so the REAL default-deny path runs.
    app.dependency_overrides.pop(deps.get_principal, None)
    try:
        resp = client.get("/decisions")
        assert resp.status_code == 401, resp.text
    finally:
        from tests.conftest import install_test_principal_override

        install_test_principal_override()

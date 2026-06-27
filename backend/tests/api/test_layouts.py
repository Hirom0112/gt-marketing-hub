"""Composable-Home layout API tests (B3) — per-user GET/PUT scoped to the principal.

The headline invariant is SCOPING: the layout is keyed off the VERIFIED principal's
``user_id`` (the JWT ``sub``), with NO ``owner`` query param — so user A's saved
layout is invisible to user B (the IDOR property at the app layer; RLS on the 0029
table is the DB backstop). The other cases prove the pure merge is wired in: a new
user gets the starter pack, and a saved placement whose widget id is unknown is
dropped on read.

These tests hit the REAL main app (with ``layouts_router`` registered), overriding
only :func:`app.api.deps.get_layouts_store` to a fresh in-memory store per test. The
autouse conftest principal shim verifies Bearer tokens against the test secret, so a
minted JWT with a chosen ``sub`` drives a real, distinct verified principal.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.widget_registry import STARTER_IDS
from app.data.layouts_store import InMemoryLayoutsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

# Two distinct auth users (the JWT ``sub`` → ``principal.user_id``). Stable literals so
# the scoping assertion is about WHICH principal, not random ids.
USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")


def _auth(sub: UUID) -> dict[str, str]:
    """An ``Authorization: Bearer`` header for a signed JWT whose ``sub`` is ``user``."""
    token = mint_jwt(role="operator", sub=sub, secret=TEST_JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def store() -> InMemoryLayoutsStore:
    """A fresh in-memory layouts store per test (full isolation)."""
    return InMemoryLayoutsStore()


@pytest.fixture
def client(store: InMemoryLayoutsStore) -> Iterator[TestClient]:
    """The main app with the layouts store overridden to the per-test in-memory one."""
    from app.main import app

    app.dependency_overrides[deps.get_layouts_store] = lambda: store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_layouts_store, None)


def _ids(layout: list[dict]) -> set[str]:
    """The widget ids ("i") present in a layout response."""
    return {p["i"] for p in layout}


def test_layout_scoped_to_principal(client: TestClient) -> None:
    """User A PUTs a layout; A's GET returns it; user B sees only its OWN starter pack."""
    # A saves a layout containing a single known widget (work_queue is a starter id).
    a_layout = [{"i": "work_queue", "x": 0, "y": 0, "w": 4, "h": 2}]
    put = client.put("/home/layout", headers=_auth(USER_A), json={"layout": a_layout})
    assert put.status_code == 200, put.text

    # A's own GET reflects A's saved placement (the work_queue cell A persisted).
    a_get = client.get("/home/layout", headers=_auth(USER_A))
    assert a_get.status_code == 200, a_get.text
    a_cell = next(p for p in a_get.json() if p["i"] == "work_queue")
    assert (a_cell["x"], a_cell["y"]) == (0, 0), "A must see its own saved placement"

    # B (a DIFFERENT sub) never saved — it gets its OWN starter pack, NOT A's layout.
    b_get = client.get("/home/layout", headers=_auth(USER_B))
    assert b_get.status_code == 200, b_get.text
    assert _ids(b_get.json()) == set(STARTER_IDS), "B must see only its own starter pack"


def test_new_user_gets_starter_pack(client: TestClient) -> None:
    """A user with no saved layout → GET returns the full starter pack."""
    resp = client.get("/home/layout", headers=_auth(USER_A))
    assert resp.status_code == 200, resp.text
    assert set(STARTER_IDS) <= _ids(resp.json()), "every starter widget must be present"
    # A brand-new user's layout IS exactly the starter pack (nothing saved to add).
    assert _ids(resp.json()) == set(STARTER_IDS)


def test_unknown_widget_id_dropped_on_read(client: TestClient) -> None:
    """A PUT containing an UNKNOWN widget id → the merged GET drops it (the crash-guard)."""
    bogus = [
        {"i": "work_queue", "x": 0, "y": 0, "w": 4, "h": 2},
        {"i": "not_a_real_widget", "x": 4, "y": 0, "w": 4, "h": 2},
    ]
    put = client.put("/home/layout", headers=_auth(USER_A), json={"layout": bogus})
    assert put.status_code == 200, put.text
    assert "not_a_real_widget" not in _ids(put.json()), "PUT response must drop the unknown id"

    got = client.get("/home/layout", headers=_auth(USER_A))
    assert got.status_code == 200, got.text
    assert "not_a_real_widget" not in _ids(got.json()), "GET must drop the unknown id"
    assert "work_queue" in _ids(got.json()), "the known survivor stays"


def test_no_token_denied(client: TestClient) -> None:
    """No Authorization header → 401 (the production default-deny inherited from get_principal)."""
    from app.main import app

    # Pop ONLY the conftest get_principal convenience so the REAL default-deny runs
    # (get_settings_dep stays overridden with the test secret ⇒ a no-token request is
    # the meaningful "missing bearer token" 401).
    app.dependency_overrides.pop(deps.get_principal, None)
    try:
        resp = client.get("/home/layout")  # no Authorization header
        assert resp.status_code == 401, resp.text
    finally:
        # Restore the shim for the rest of the session (autouse re-installs per test,
        # but be tidy within this test's client lifecycle).
        from tests.conftest import install_test_principal_override

        install_test_principal_override()

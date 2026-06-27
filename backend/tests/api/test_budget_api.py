"""Budget Tracker API tests (B4) — the GET roll-up + the variance→Decision feeder.

Two headline invariants:

- The EDIT gate: ``POST /budget/entry`` is admin/leader-gated (an ``operator`` is
  403); VIEWING the tracker (``GET /budget``) is open to any authenticated principal.
- The cross-module link: an actual entry that pushes a workstream >10% over its
  planned allocation emits EXACTLY ONE open ``budget_variance`` Decision-Queue item;
  a repeated overrun for the same workstream is idempotent (no duplicate open item).

These tests hit the REAL main app (with ``budget_router`` registered), overriding
:func:`app.api.deps.get_budget_store` → a fresh params-seeded in-memory store and
:func:`app.api.deps.get_decisions_store` → a fresh in-memory store per test. The
autouse conftest principal shim verifies Bearer tokens against the test secret, so a
minted operator/leader/admin JWT drives the real role gate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.program import Program
from app.data.budget_store import InMemoryBudgetStore
from app.data.decisions_store import InMemoryDecisionsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

# The active program the routes resolve via get_active_program (deps._active_program).
_PROGRAM = Program.FALL_ENROLLMENT


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture
def budget_store() -> InMemoryBudgetStore:
    """A fresh params-seeded in-memory budget store per test (full isolation)."""
    return InMemoryBudgetStore(params=deps._params)


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    """A fresh in-memory decisions store per test (the variance feeder's sink)."""
    return InMemoryDecisionsStore()


@pytest.fixture
def client(
    budget_store: InMemoryBudgetStore, decisions_store: InMemoryDecisionsStore
) -> Iterator[TestClient]:
    """The main app with the budget + decisions stores overridden to per-test ones."""
    from app.main import app

    app.dependency_overrides[deps.get_budget_store] = lambda: budget_store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_budget_store, None)
        app.dependency_overrides.pop(deps.get_decisions_store, None)


def _budget_variance_open(store: InMemoryDecisionsStore, workstream: str) -> list:
    """The OPEN budget_variance decisions for ``workstream`` (the idempotency probe)."""
    return [
        d
        for d in store.list_open(_PROGRAM)
        if d.source == "budget_variance" and d.payload.get("workstream") == workstream
    ]


# ----------------------------------------------------------------- variance feeder
def test_variance_emits_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    """A >10% overrun emits EXACTLY ONE open budget_variance decision; repeats idempotent."""
    # ops planned = 25000; an actual of 30000 ⇒ variance 0.20 > 0.10 ⇒ flagged.
    resp = client.post(
        "/budget/entry",
        headers=_auth("admin"),
        json={"workstream": "ops", "kind": "actual", "amount_usd": 30000},
    )
    assert resp.status_code == 200, resp.text
    assert len(_budget_variance_open(decisions_store, "ops")) == 1

    # A SECOND overrun for the SAME workstream must NOT add a second open decision.
    resp2 = client.post(
        "/budget/entry",
        headers=_auth("leader"),
        json={"workstream": "ops", "kind": "actual", "amount_usd": 5000},
    )
    assert resp2.status_code == 200, resp2.text
    assert len(_budget_variance_open(decisions_store, "ops")) == 1


def test_under_threshold_emits_no_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    """A 9% overrun (<= 10% threshold) emits NO decision (at/under does not flag)."""
    # grassroots planned = 210000; 228900 is exactly 9% over ⇒ variance 0.09 ≤ 0.10.
    resp = client.post(
        "/budget/entry",
        headers=_auth("admin"),
        json={"workstream": "grassroots", "kind": "actual", "amount_usd": 228900},
    )
    assert resp.status_code == 200, resp.text
    assert _budget_variance_open(decisions_store, "grassroots") == []


# -------------------------------------------------------------------- the edit gate
def test_post_entry_operator_forbidden(client: TestClient) -> None:
    """Operator JWT → POST /budget/entry → 403 (admin/leader-gated edit)."""
    resp = client.post(
        "/budget/entry",
        headers=_auth("operator"),
        json={"workstream": "ops", "kind": "actual", "amount_usd": 30000},
    )
    assert resp.status_code == 403, resp.text


def test_post_entry_leader_ok(client: TestClient) -> None:
    """Leader JWT → POST /budget/entry → 200 (leadership may edit the ledger)."""
    resp = client.post(
        "/budget/entry",
        headers=_auth("leader"),
        json={"workstream": "content", "kind": "actual", "amount_usd": 1000},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------- the roll-up
def test_get_budget_rollup(client: TestClient) -> None:
    """GET /budget returns the four workstreams summing planned to 365000, with roll-up."""
    resp = client.get("/budget", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    rows = body["workstreams"]
    assert len(rows) == 4
    assert {r["workstream"] for r in rows} == {"grassroots", "content", "guerrilla", "ops"}
    assert sum(r["planned"] for r in rows) == 365000

    assert body["rollup"]["total_planned"] == 365000
    assert body["rollup"]["total_usd"] == 365000
    assert "flagged" in body
    assert "burn" in body and len(body["burn"]) == 4


# ----------------------------------------------------------------------- default-deny
def test_no_token_unauthorized(client: TestClient) -> None:
    """No Authorization header → 401 (default-deny inherited from get_principal)."""
    from app.main import app

    app.dependency_overrides.pop(deps.get_principal, None)
    try:
        resp = client.get("/budget")
        assert resp.status_code == 401, resp.text
    finally:
        from tests.conftest import install_test_principal_override

        install_test_principal_override()

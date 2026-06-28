"""KPI-goals API tests (Module 6 Phase 3) — the leadership-editable scorecard targets.

The headline invariants the brief asks for:

- ``GET /scorecard/goals`` returns the SEEDED spec defaults (any authenticated seat);
- ``PUT /scorecard/goals`` as a LEADER updates a target AND records a change event;
- ``PUT`` as an OPERATOR is 403 (the leadership write gate);
- an unknown KPI key is rejected (422);
- the weekly scorecard's ``target`` for an edited KPI reflects the new value (targets
  flow from the store into the scorecard, no second source).

These hit the REAL main app. The KPI-goals store is the process singleton behind
:func:`app.api.deps.get_goals_store`; an autouse fixture calls
:func:`app.api.deps.reset_goals_store` so each test starts from the canonical seed with
no cross-test leakage. The conftest principal shim verifies Bearer tokens against the
test secret, so a minted operator/leader JWT drives the real role gate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.data.goals_store import DEFAULT_GOALS
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

client = TestClient(app)


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture(autouse=True)
def _fresh_goals_store() -> Iterator[None]:
    """Reset the KPI-goals singleton to a fresh seeded store around each test."""
    deps.reset_goals_store()
    try:
        yield
    finally:
        deps.reset_goals_store()


def test_get_goals_returns_seeded_defaults() -> None:
    """GET → 200 with the nine seeded spec-default targets (any authenticated seat)."""
    resp = client.get("/scorecard/goals", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["goals"] == DEFAULT_GOALS
    # No edits yet ⇒ an empty change log.
    assert body["events"] == []


def test_put_goal_as_leader_updates_and_logs() -> None:
    """A leader sets a target → it updates AND a change event is recorded (old→new)."""
    resp = client.put(
        "/scorecard/goals",
        headers=_auth("leader"),
        json={"goals": {"deposits": 200.0}, "note": "Fall push"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["goals"]["deposits"] == 200.0
    # The change log captured the transition + the note.
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["key"] == "deposits"
    assert event["old_target"] == DEFAULT_GOALS["deposits"]
    assert event["new_target"] == 200.0
    assert event["note"] == "Fall push"
    assert event["changed_by"]


def test_put_goal_admin_allowed() -> None:
    """An admin shares the leadership write lens (require_role('leader','admin'))."""
    resp = client.put(
        "/scorecard/goals",
        headers=_auth("admin"),
        json={"goals": {"followup_sla": 0.95}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["goals"]["followup_sla"] == 0.95


def test_put_goal_operator_forbidden() -> None:
    """An operator hitting the edit route → 403 (the leadership write gate)."""
    resp = client.put(
        "/scorecard/goals",
        headers=_auth("operator"),
        json={"goals": {"deposits": 200.0}},
    )
    assert resp.status_code == 403, resp.text


def test_put_unknown_key_rejected() -> None:
    """An unknown KPI key → 422 (fail-closed; nothing is written)."""
    resp = client.put(
        "/scorecard/goals",
        headers=_auth("leader"),
        json={"goals": {"not_a_kpi": 1.0}},
    )
    assert resp.status_code == 422, resp.text
    # Nothing was written — the seed is intact.
    after = client.get("/scorecard/goals", headers=_auth("leader")).json()
    assert after["goals"] == DEFAULT_GOALS


def test_scorecard_target_reflects_edited_goal() -> None:
    """The weekly scorecard's target for an edited KPI reflects the new store value."""
    # Baseline: the deposits target is the seed default.
    weekly = client.get("/scorecard/weekly", headers=_auth("leader")).json()
    deposits = next(m for m in weekly["metrics"] if m["key"] == "deposits")
    assert deposits["target"] == DEFAULT_GOALS["deposits"]

    # A leader edits the deposits target.
    put = client.put(
        "/scorecard/goals",
        headers=_auth("leader"),
        json={"goals": {"deposits": 250.0}},
    )
    assert put.status_code == 200, put.text

    # The scorecard now surfaces the edited target (targets flow from the store).
    weekly2 = client.get("/scorecard/weekly", headers=_auth("leader")).json()
    deposits2 = next(m for m in weekly2["metrics"] if m["key"] == "deposits")
    assert deposits2["target"] == 250.0


def test_put_no_token_unauthorized() -> None:
    """No bearer token → 401 (default-deny). The edit route still needs a verified seat."""
    from app.core.settings import Settings
    from tests.conftest import install_test_principal_override

    app.dependency_overrides.pop(deps.get_principal, None)
    app.dependency_overrides[deps.get_settings_dep] = lambda: Settings(
        supabase_jwt_secret=TEST_JWT_SECRET
    )
    try:
        resp = client.put("/scorecard/goals", json={"goals": {"deposits": 1.0}})
        assert resp.status_code == 401, resp.text
    finally:
        install_test_principal_override()


def test_supabase_upsert_targets_unique_key() -> None:
    """SupabaseGoalsStore upserts on (program_id, key) — repeat sets must NOT 409.

    Regression guard: the goal row's PK is a random uuid, so a merge-duplicates POST
    needs ``on_conflict=program_id,key`` or PostgREST falls back to INSERT and a second
    edit of the same KPI violates the UNIQUE constraint. A fake client records the POST
    URL so we assert the conflict target is sent (the in-memory store can't catch this).
    """
    from app.core.program import Program
    from app.data.goals_store import SupabaseGoalsStore

    posts: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, body: object) -> None:
            self._body = body

        def json(self) -> object:
            return self._body

    class _FakeClient:
        def get(self, url: str, params: dict, headers: dict) -> _Resp:
            return _Resp([])  # no edited rows yet → seed defaults

        def post(self, url: str, headers: dict, json: dict) -> _Resp:
            posts.append(url)
            # goal upsert returns a representation row; the event append returns []
            return _Resp([json] if "dashboard_goal?" in url or "dashboard_goal " not in url else [])

    store = SupabaseGoalsStore(
        base_url="https://x.test", service_role_key="k", client=_FakeClient()
    )
    store.set_goal(Program.FALL_ENROLLMENT, "deposits", 200.0, changed_by="u", note="t")

    goal_post = next(u for u in posts if "/dashboard_goal" in u and "event" not in u)
    assert "on_conflict=program_id,key" in goal_post, goal_post

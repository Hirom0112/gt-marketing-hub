"""Field & Events API tests (Module 8) — reads, owner-gated writes, calendar blend.

Headline invariants:

- The READ paths (overview / list+filters / calendar) are open to ANY authenticated
  seat and shape the seeded demo store sensibly. The clock-independent rollup figures
  (totals/rates/top type) are asserted here; the windowed counts are covered by the
  pure-core unit test with an injected ``now``.
- The WRITE paths (POST create / PATCH update) are OWNER-gated: an operator who OWNS the
  ``events`` workstream may write, a FOREIGN operator (mapped elsewhere) is 403, a
  leader may write. ``owner`` is stamped server-side (never the body).
- The calendar BLENDS GT field events (``source=field``, writable) with READ-ONLY
  ambassador events (``source=ambassador``, ``read_only=True``) — both seeded in-memory,
  no live calls.

These hit the REAL main app (with ``field_events_router`` registered), overriding the
field-events store → a fresh SEEDED in-memory store, and the grassroots store → a fresh
SEEDED in-memory store (for the calendar blend). The autouse conftest principal shim
verifies Bearer tokens against the test secret.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api import field_events as field_events_api
from app.core.program import Program
from app.data.field_events_store import InMemoryFieldEventsStore
from app.data.grassroots_store import InMemoryGrassrootsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT
# A deterministic operator agent id used for the FOREIGN-operator deny case.
_FOREIGN_AGENT = "22222222-2222-4222-8222-222222222222"
# The seed assigns ids UUID(int=0xF1E1_0000 + i); index 3 is the robotics festival.
_SEED_EVENT_3 = UUID(int=0xF1E1_0000 + 3)


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_agent(role: str, agent_id: str) -> dict[str, str]:
    """A signed ``role`` JWT with an explicit ``agent_id`` (drives the owner gate)."""
    return {
        "Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, agent_id=agent_id)}"
    }


@pytest.fixture
def field_events_store() -> InMemoryFieldEventsStore:
    """A fresh SEEDED in-memory field-events store per test (the demo events)."""
    store = InMemoryFieldEventsStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def grassroots_store() -> InMemoryGrassrootsStore:
    """A fresh SEEDED in-memory grassroots store per test (the calendar's read-only feed)."""
    store = InMemoryGrassrootsStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def client(
    field_events_store: InMemoryFieldEventsStore,
    grassroots_store: InMemoryGrassrootsStore,
) -> Iterator[TestClient]:
    """The main app with the field-events + grassroots stores overridden per test."""
    from app.main import app

    app.dependency_overrides[deps.get_field_events_store] = lambda: field_events_store
    app.dependency_overrides[deps.get_grassroots_store] = lambda: grassroots_store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_field_events_store, None)
        app.dependency_overrides.pop(deps.get_grassroots_store, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    """GET /field/events/overview → the 8a rollup (clock-independent figures asserted)."""
    resp = client.get("/field/events/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_rsvps"] == 230
    assert body["total_attendance"] == 96
    assert body["rsvp_to_attendance_pct"] == 42
    assert body["consults_booked_total"] == 28
    assert body["event_to_consult_pct"] == 12
    assert body["event_to_consult_manual"] is True
    assert body["top_event_type_by_attendance"] == {"event_type": "ama", "attendance": 41}
    # The windowed counts are clock-dependent at the edge — just present + non-negative.
    assert body["upcoming_count"] >= 0
    assert body["completed_this_month"] >= 0


def test_list_all(client: TestClient) -> None:
    """GET /field/events → all seeded events (any seat)."""
    resp = client.get("/field/events", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 7
    assert {r["owner"] for r in rows} == {"events"}


def test_list_filtered_by_type_and_status(client: TestClient) -> None:
    """GET /field/events?type=&status= applies the pure tracker filter."""
    resp = client.get("/field/events", headers=_auth("operator"), params={"type": "festival"})
    assert resp.status_code == 200, resp.text
    assert {r["event_type"] for r in resp.json()} == {"festival"}

    resp = client.get("/field/events", headers=_auth("operator"), params={"status": "completed"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 3
    assert all(r["status"] == "completed" for r in rows)


def test_calendar_blends_both_sources(client: TestClient) -> None:
    """GET /field/events/calendar → field + ambassador items, correctly tagged."""
    resp = client.get("/field/events/calendar", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    by_source: dict[str, list[dict]] = {"field": [], "ambassador": []}
    for it in items:
        by_source[it["source"]].append(it)
    # 7 seeded field events + 4 seeded ambassador events.
    assert len(by_source["field"]) == 7
    assert len(by_source["ambassador"]) == 4
    # Field items are writable; ambassador items are READ-ONLY with no status.
    assert all(it["read_only"] is False for it in by_source["field"])
    assert all(it["read_only"] is True for it in by_source["ambassador"])
    assert all(it["status"] is None for it in by_source["ambassador"])
    assert all(it["status"] is not None for it in by_source["field"])


# ------------------------------------------------------------------- owner-gated writes
def test_create_operator_owns_events_ok(client: TestClient) -> None:
    """An operator who OWNS 'events' may create a field event; owner is stamped server-side."""
    resp = client.post(
        "/field/events",
        headers=_auth("operator"),
        json={
            "event_name": "Spring shadow day",
            "event_type": "shadow_day",
            "venue": "Austin metro",
            "event_date": "2026-09-10",
            "rsvp_count": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_name"] == "Spring shadow day"
    assert body["owner"] == "events"
    assert body["status"] == "planning"


def test_create_foreign_operator_forbidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FOREIGN operator (mapped to another workstream) is 403 on a write."""
    monkeypatch.setitem(field_events_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.post(
        "/field/events",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"event_name": "Blocked event", "event_type": "webinar", "event_date": "2026-09-10"},
    )
    assert resp.status_code == 403, resp.text


def test_create_leader_ok(client: TestClient) -> None:
    """A leader may create any field event."""
    resp = client.post(
        "/field/events",
        headers=_auth("leader"),
        json={"event_name": "Leader event", "event_type": "ama", "event_date": "2026-09-10"},
    )
    assert resp.status_code == 200, resp.text


def test_create_unknown_event_type_422(client: TestClient) -> None:
    """An event_type outside the params labels is a clean 422 (INV-2)."""
    resp = client.post(
        "/field/events",
        headers=_auth("leader"),
        json={"event_name": "Bad type", "event_type": "rave", "event_date": "2026-09-10"},
    )
    assert resp.status_code == 422, resp.text


def test_patch_logs_attendance(client: TestClient) -> None:
    """PATCH /field/events/{id} logs attendance/consults + flips status — owner-gated."""
    resp = client.patch(
        f"/field/events/{_SEED_EVENT_3}",
        headers=_auth("operator"),
        json={"attendance_count": 30, "consults_booked": 5, "status": "completed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attendance_count"] == 30
    assert body["consults_booked"] == 5
    assert body["status"] == "completed"


def test_patch_foreign_operator_forbidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FOREIGN operator cannot update a field event (owner-gated) → 403."""
    monkeypatch.setitem(field_events_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.patch(
        f"/field/events/{_SEED_EVENT_3}",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"attendance_count": 1},
    )
    assert resp.status_code == 403, resp.text


def test_patch_unknown_event_404(client: TestClient) -> None:
    """PATCH on an unknown event_id is a 404."""
    resp = client.patch(
        f"/field/events/{UUID(int=0xDEAD)}",
        headers=_auth("leader"),
        json={"attendance_count": 1},
    )
    assert resp.status_code == 404, resp.text

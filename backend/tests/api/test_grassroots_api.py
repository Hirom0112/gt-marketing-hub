"""Grassroots-Engine API tests (Module 2) — reads, owner-gated writes, cross-links.

Headline invariants:

- The READ paths (overview / ambassadors / market-map / sprints / events) are open to
  ANY authenticated seat and shape the seeded demo store sensibly.
- The WRITE paths are OWNER-gated: an operator who OWNS ``grassroots`` may write, a
  FOREIGN operator (mapped to another workstream) is 403, a leader may write.
- The three cross-module links fire: hot-family enqueues a ``grassroots_hot_family``
  Decision-Queue item, testimonial stubs a DRAFT content asset, and events are readable
  (the Field & Events READ-ONLY source).

These hit the REAL main app (with ``grassroots_router`` registered), overriding the
grassroots store → a fresh SEEDED in-memory store, the decisions store → a fresh
in-memory one, and the content library → a fresh temp-file sqlite library per test. The
autouse conftest principal shim verifies Bearer tokens against the test secret.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api import grassroots as grassroots_api
from app.core.program import Program
from app.data.decisions_store import InMemoryDecisionsStore
from app.data.grassroots_store import InMemoryGrassrootsStore
from app.marketing.library import SqliteContentLibrary
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT
# A deterministic operator agent id used for the FOREIGN-operator deny case.
_FOREIGN_AGENT = "22222222-2222-4222-8222-222222222222"


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_agent(role: str, agent_id: str) -> dict[str, str]:
    """A signed ``role`` JWT with an explicit ``agent_id`` (drives the owner gate)."""
    return {
        "Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, agent_id=agent_id)}"
    }


@pytest.fixture
def grassroots_store() -> InMemoryGrassrootsStore:
    """A fresh SEEDED in-memory grassroots store per test (the demo roster)."""
    store = InMemoryGrassrootsStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    """A fresh in-memory decisions store per test (the hot-family feeder's sink)."""
    return InMemoryDecisionsStore()


@pytest.fixture
def content_library(tmp_path) -> SqliteContentLibrary:
    """A fresh temp-file content library per test (the testimonial stub's sink)."""
    return SqliteContentLibrary(tmp_path / "grassroots_test_library.sqlite3")


@pytest.fixture
def client(
    grassroots_store: InMemoryGrassrootsStore,
    decisions_store: InMemoryDecisionsStore,
    content_library: SqliteContentLibrary,
) -> Iterator[TestClient]:
    """The main app with the grassroots/decisions/content stores overridden per test."""
    from app.main import app

    app.dependency_overrides[deps.get_grassroots_store] = lambda: grassroots_store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    app.dependency_overrides[deps.get_content_library_dep] = lambda: content_library
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_grassroots_store, None)
        app.dependency_overrides.pop(deps.get_decisions_store, None)
        app.dependency_overrides.pop(deps.get_content_library_dep, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    """GET /grassroots/overview → goal bars + pipeline + headline (any seat)."""
    resp = client.get("/grassroots/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {g["key"] for g in body["goals"]}
    assert keys == {"active_ambassadors", "warm_intros", "p2p_calls", "influenced_enrollments"}
    # The seed is calibrated to read sensibly but NOT maxed.
    bars = {g["key"]: g for g in body["goals"]}
    assert bars["active_ambassadors"]["value"] == 18
    assert bars["active_ambassadors"]["target"] == 25
    assert bars["warm_intros"]["value"] == 150
    assert bars["influenced_enrollments"]["value"] == 22
    assert body["pipeline"]["active"] == 13
    assert body["pipeline"]["champion"] == 5
    assert body["headline"]["ambassadors_total"] == 30


def test_list_ambassadors_with_provenance(client: TestClient) -> None:
    """GET /grassroots/ambassadors → roster; some rows carry reconcile provenance."""
    resp = client.get("/grassroots/ambassadors", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 30
    # The seeded rows that reuse the reconcile-fixture emails carry a provenance badge.
    provs = {r["provenance"] for r in rows if r["provenance"] is not None}
    assert "both" in provs


def test_market_map(client: TestClient) -> None:
    """GET /grassroots/market-map → nodes + per-category coverage summary."""
    resp = client.get("/grassroots/market-map", headers=_auth("admin"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["nodes"]) == 7
    cats = {c["category"]: c for c in body["summary"]}
    # A cold-only category reads 0% coverage; an active one reads 100%.
    assert cats["Debate leagues"]["coverage_pct"] == 0
    assert cats["Parent groups"]["coverage_pct"] == 100


def test_sprints_have_health(client: TestClient) -> None:
    """GET /grassroots/sprints → each sprint carries a derived health token."""
    resp = client.get("/grassroots/sprints", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert all(r["health"] in {"on_pace", "behind", "closed"} for r in rows)


def test_events_read_source(client: TestClient) -> None:
    """GET /grassroots/events → the parent-led events (Field & Events READ source)."""
    resp = client.get("/grassroots/events", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 4
    assert {r["event_type"] for r in rows} <= {"coffee_chat", "qa", "school_visit", "virtual"}


# --------------------------------------------------------------------- the owner gate
def test_log_p2p_owner_operator_ok(
    client: TestClient, grassroots_store: InMemoryGrassrootsStore
) -> None:
    """An operator who OWNS grassroots may log a p2p call (increment by one)."""
    amb = grassroots_store.list_ambassadors(_PROGRAM)[0]
    before = amb.p2p_calls
    resp = client.post(
        f"/grassroots/ambassador/{amb.ambassador_id}/log-p2p",
        headers=_auth("operator"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["p2p_calls"] == before + 1


def test_log_p2p_foreign_operator_forbidden(
    client: TestClient,
    grassroots_store: InMemoryGrassrootsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FOREIGN operator (mapped to another workstream) is 403 on a write."""
    # Map this agent to a non-grassroots workstream so the owner gate denies it.
    monkeypatch.setitem(grassroots_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    amb = grassroots_store.list_ambassadors(_PROGRAM)[0]
    resp = client.post(
        f"/grassroots/ambassador/{amb.ambassador_id}/log-p2p",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
    )
    assert resp.status_code == 403, resp.text


def test_log_p2p_leader_ok(client: TestClient, grassroots_store: InMemoryGrassrootsStore) -> None:
    """A leader may write (leadership may write any workstream)."""
    amb = grassroots_store.list_ambassadors(_PROGRAM)[0]
    resp = client.post(
        f"/grassroots/ambassador/{amb.ambassador_id}/log-p2p",
        headers=_auth("leader"),
    )
    assert resp.status_code == 200, resp.text


def test_log_p2p_unknown_ambassador_404(client: TestClient) -> None:
    """An unknown ambassador → 404 (the store raises KeyError; the route maps it)."""
    missing = "99999999-9999-4999-8999-999999999999"
    resp = client.post(f"/grassroots/ambassador/{missing}/log-p2p", headers=_auth("leader"))
    assert resp.status_code == 404, resp.text


def test_create_market_node_and_sprint_and_event(client: TestClient) -> None:
    """The three remaining writes succeed for a leader and round-trip into the reads."""
    node = client.post(
        "/grassroots/market-map/node",
        headers=_auth("leader"),
        json={"category": "New parent group", "status": "outreach", "leads_generated": 2},
    )
    assert node.status_code == 200, node.text
    assert node.json()["category"] == "New parent group"

    sprint = client.post(
        "/grassroots/sprint",
        headers=_auth("leader"),
        json={
            "name": "Spring sprint",
            "window_start": "2026-06-01",
            "window_end": "2026-06-29",
            "families_identified": 10,
            "conversions": 4,
        },
    )
    assert sprint.status_code == 200, sprint.text
    assert sprint.json()["health"] in {"on_pace", "behind", "closed"}

    event = client.post(
        "/grassroots/event",
        headers=_auth("leader"),
        json={"event_name": "New coffee chat", "event_type": "coffee_chat", "date": "2026-07-01"},
    )
    assert event.status_code == 200, event.text
    # The new event is now visible on the Field & Events read source.
    listing = client.get("/grassroots/events", headers=_auth("operator"))
    assert any(e["event_name"] == "New coffee chat" for e in listing.json())


def test_create_sprint_bad_window_422(client: TestClient) -> None:
    """A window_end before window_start is a clean 422."""
    resp = client.post(
        "/grassroots/sprint",
        headers=_auth("leader"),
        json={"name": "Bad", "window_start": "2026-06-29", "window_end": "2026-06-01"},
    )
    assert resp.status_code == 422, resp.text


# ----------------------------------------------------------------- cross-module links
def test_hot_family_enqueues_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    """POST /grassroots/hot-family → ONE open grassroots_hot_family decision (Module 11)."""
    resp = client.post(
        "/grassroots/hot-family",
        headers=_auth("leader"),
        json={
            "family_label": "Hot family A7",
            "reason": "High-value, at-risk, deadline near",
            "recommendation": "Leadership call this week",
            "priority": "urgent",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "grassroots_hot_family"
    assert body["workstream"] == "grassroots"
    open_items = [
        d for d in decisions_store.list_open(_PROGRAM) if d.source == "grassroots_hot_family"
    ]
    assert len(open_items) == 1
    # raised_by is stamped from the verified principal, never the body.
    assert open_items[0].raised_by


def test_hot_family_owner_gated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign operator cannot escalate a hot family (owner-gated) → 403."""
    monkeypatch.setitem(grassroots_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.post(
        "/grassroots/hot-family",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"family_label": "Hot family A7"},
    )
    assert resp.status_code == 403, resp.text


def test_testimonial_stubs_content_asset(
    client: TestClient, content_library: SqliteContentLibrary
) -> None:
    """POST /grassroots/testimonial → a DRAFT content stub tagged grassroots_testimonial."""
    resp = client.post(
        "/grassroots/testimonial",
        headers=_auth("leader"),
        json={
            "title": "A parent's story",
            "quote": "GT School changed how my child learns.",
            "attribution_label": "Austin parent (synthetic)",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lifecycle"] == "draft"
    assert body["source"] == "grassroots_testimonial"
    assert "grassroots_testimonial" in body["tags"]
    # The stub is persisted in the content library (a DRAFT — not yet in search).
    stored = content_library.get(body["asset_id"])
    assert stored is not None
    assert stored.source_ref == "grassroots_testimonial"
    # A DRAFT does NOT surface in the kept+validated search (the keep path is required).
    assert content_library.search() == []


def test_testimonial_owner_gated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign operator cannot stub a testimonial (owner-gated) → 403."""
    monkeypatch.setitem(grassroots_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.post(
        "/grassroots/testimonial",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"title": "x", "quote": "y"},
    )
    assert resp.status_code == 403, resp.text

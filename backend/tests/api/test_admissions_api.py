"""Admissions API tests (Module 9) — reads, owner-gated writes, the two cross-links.

Headline invariants:

- The READ paths (overview / objections+filters / voice / feedback / bridge) are open to
  ANY authenticated seat and shape the seeded demo store sensibly. The voice sentiment is
  labelled honestly (``source_mode='placeholder'``, never live — INV-6/9).
- CROSS-LINK 1: POST /admissions/objections/{id}/brief creates a Content calendar DRAFT
  (owner=admissions) AND records a content_bridge row — owner-gated (admissions).
- CROSS-LINK 2: POST /admissions/feedback with actionable=True enqueues ONE open
  `admissions` Decision-Queue item and stores its decision_id on the feedback item.
- The PATCH action/close path is LEADER/ADMIN only (an operator is 403).

These hit the REAL main app (with admissions_router registered), overriding the admissions
store → a fresh SEEDED in-memory store, plus the content-metrics + decisions stores → fresh
in-memory stores. The autouse conftest principal shim verifies Bearer tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api import admissions as admissions_api
from app.api import deps
from app.core.program import Program
from app.data.admissions_store import InMemoryAdmissionsStore
from app.data.content_metrics_store import InMemoryContentMetricsStore
from app.data.decisions_store import InMemoryDecisionsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT
_FOREIGN_AGENT = "22222222-2222-4222-8222-222222222222"
_COST_OBJECTION = UUID(int=0xAD91_0000)  # the seeded cost objection
_OPEN_FEEDBACK = UUID(int=0xAD93_0000 + 1)  # the seeded open persona_mismatch item


def _auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_agent(role: str, agent_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, agent_id=agent_id)}"
    }


@pytest.fixture
def admissions_store() -> InMemoryAdmissionsStore:
    store = InMemoryAdmissionsStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def content_store() -> InMemoryContentMetricsStore:
    return InMemoryContentMetricsStore()  # clean (no seed needed for the cross-link assert)


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    return InMemoryDecisionsStore()


@pytest.fixture
def client(
    admissions_store: InMemoryAdmissionsStore,
    content_store: InMemoryContentMetricsStore,
    decisions_store: InMemoryDecisionsStore,
) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[deps.get_admissions_store] = lambda: admissions_store
    app.dependency_overrides[deps.get_content_metrics_store] = lambda: content_store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        for dep in (
            deps.get_admissions_store,
            deps.get_content_metrics_store,
            deps.get_decisions_store,
        ):
            app.dependency_overrides.pop(dep, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    resp = client.get("/admissions/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["weekly_stats"]) == 5
    assert [o["theme"] for o in body["top_objections"]] == [
        "cost",
        "accreditation",
        "gifted_enough",
    ]
    assert body["objection_trend"]["cost"] == "up"
    assert body["feedback_open_count"] == 2
    assert body["bridge_hit_rate"]["hit_rate_pct"] == 50
    assert body["objection_to_resolution_days"] == 5.0


def test_objections_filter_and_sort(client: TestClient) -> None:
    resp = client.get("/admissions/objections", headers=_auth("operator"), params={"theme": "cost"})
    assert resp.status_code == 200, resp.text
    assert {o["theme"] for o in resp.json()} == {"cost"}

    resp = client.get(
        "/admissions/objections", headers=_auth("operator"), params={"source": "form"}
    )
    assert {o["theme"] for o in resp.json()} == {"accreditation", "tech_requirements"}

    resp = client.get("/admissions/objections", headers=_auth("operator"))
    freqs = [o["week_count"] for o in resp.json()]
    assert freqs == sorted(freqs, reverse=True)  # sort=frequency default


def test_voice(client: TestClient) -> None:
    resp = client.get("/admissions/voice", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["quotes"]) == 8
    assert body["quote_of_week"] is not None
    assert body["quote_of_week"]["is_quote_of_week"] is True
    assert body["quote_sentiment"]["total"] == 8
    # Honest source label — placeholder, never a live feed (INV-6/9).
    assert body["sentiment_source_mode"] == "placeholder"
    assert body["feed_sentiment"]["total"] > 0


def test_feedback_list(client: TestClient) -> None:
    resp = client.get("/admissions/feedback", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 6
    # 4 actioned/closed (with actioned_at), 3 within the 7d SLA.
    assert body["closure_rate"]["actioned"] == 4
    assert body["closure_rate"]["within_sla"] == 3
    assert body["closure_rate"]["closure_rate_pct"] == 75


def test_bridge(client: TestClient) -> None:
    resp = client.get("/admissions/bridge", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hit_rate"]["total"] == 4
    produced = [b for b in body["bridges"] if b["produced"]]
    assert all(b["frequency_decreased"] for b in produced)


# ------------------------------------------------- CROSS-LINK 1: objection → content brief
def test_objection_to_brief_creates_draft_and_bridge(
    client: TestClient,
    content_store: InMemoryContentMetricsStore,
    admissions_store: InMemoryAdmissionsStore,
) -> None:
    bridges_before = len(admissions_store.list_content_bridges(_PROGRAM))
    resp = client.post(
        f"/admissions/objections/{_COST_OBJECTION}/brief", headers=_auth("operator"), json={}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["theme"] == "cost"
    # A Content DRAFT calendar entry was created, owned by admissions.
    calendar = content_store.list_calendar(_PROGRAM)
    assert len(calendar) == 1
    assert calendar[0].owner == "admissions"
    assert calendar[0].status == "draft"
    # A bridge row was recorded linking the brief.
    assert len(admissions_store.list_content_bridges(_PROGRAM)) == bridges_before + 1


def test_objection_to_brief_foreign_operator_forbidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(admissions_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.post(
        f"/admissions/objections/{_COST_OBJECTION}/brief",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={},
    )
    assert resp.status_code == 403, resp.text


def test_objection_to_brief_unknown_404(client: TestClient) -> None:
    resp = client.post(
        f"/admissions/objections/{UUID(int=0xDEAD)}/brief", headers=_auth("leader"), json={}
    )
    assert resp.status_code == 404, resp.text


# ------------------------------------------------ CROSS-LINK 2: feedback → Decision Queue
def test_create_actionable_feedback_enqueues_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    resp = client.post(
        "/admissions/feedback",
        headers=_auth("operator"),
        json={"summary": "ESA churn risk", "category": "urgent", "actionable": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision_id"] is not None
    open_decisions = decisions_store.list_open(_PROGRAM)
    assert len(open_decisions) == 1
    assert open_decisions[0].workstream == "admissions"


def test_create_nonactionable_feedback_no_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    resp = client.post(
        "/admissions/feedback",
        headers=_auth("operator"),
        json={"summary": "minor note", "category": "messaging_gap", "actionable": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision_id"] is None
    assert len(decisions_store.list_open(_PROGRAM)) == 0


def test_create_feedback_bad_category_422(client: TestClient) -> None:
    resp = client.post(
        "/admissions/feedback",
        headers=_auth("leader"),
        json={"summary": "x", "category": "not_a_category"},
    )
    assert resp.status_code == 422, resp.text


def test_create_feedback_foreign_operator_forbidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(admissions_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "content")
    resp = client.post(
        "/admissions/feedback",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"summary": "x", "category": "urgent"},
    )
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------- PATCH (leadership only)
def test_patch_feedback_leader_actions_item(client: TestClient) -> None:
    resp = client.patch(
        f"/admissions/feedback/{_OPEN_FEEDBACK}", headers=_auth("leader"), json={"action": "action"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "actioned"
    assert body["actioned_at"] is not None


def test_patch_feedback_operator_forbidden(client: TestClient) -> None:
    resp = client.patch(
        f"/admissions/feedback/{_OPEN_FEEDBACK}",
        headers=_auth("operator"),
        json={"action": "close"},
    )
    assert resp.status_code == 403, resp.text


def test_patch_feedback_bad_action_422(client: TestClient) -> None:
    resp = client.patch(
        f"/admissions/feedback/{_OPEN_FEEDBACK}", headers=_auth("leader"), json={"action": "nope"}
    )
    assert resp.status_code == 422, resp.text


def test_patch_feedback_unknown_404(client: TestClient) -> None:
    resp = client.patch(
        f"/admissions/feedback/{UUID(int=0xDEAD)}", headers=_auth("admin"), json={"action": "close"}
    )
    assert resp.status_code == 404, resp.text

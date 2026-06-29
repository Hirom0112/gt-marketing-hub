"""Nurture & Lifecycle API tests (Module 5) — 6 sub-views, owner-gated writes, cross-links.

Headline invariants:

- The READ paths (overview / segments / pipeline / sequences / sms / sla / kpi-feed /
  attribution) are open to ANY authenticated seat and shape the seeded/aggregate sources
  sensibly. Clock-independent figures (SLA compliance %, sequence health, segment sizes)
  are asserted; windowed counts are just present + non-negative.
- The WRITE path (POST /nurture/segments/build) is OWNER-gated: a leader may write; the
  demo operator (owns 'grassroots') is 403; an unknown tier is a clean 422.
- The 4 cross-links work: hot-family → a 'nurture' Decision-Queue item, objection →
  a Content calendar DRAFT, the KPI feed shape, and the attribution feed.

These hit the REAL main app (with nurture_router registered), overriding the nurture
store + content store with fresh SEEDED in-memory stores and the CRM adapter with a
SimulatedCRMAdapter. The autouse conftest principal shim verifies Bearer tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.core.program import Program
from app.data.content_metrics_store import InMemoryContentMetricsStore
from app.data.decisions_store import InMemoryDecisionsStore
from app.data.nurture_store import InMemoryNurtureStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT
_FOREIGN_AGENT = "22222222-2222-4222-8222-222222222222"
_SEED_THREAD_0 = UUID(int=0x4E53_0000 + 0)


def _auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_agent(role: str, agent_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, agent_id=agent_id)}"
    }


@pytest.fixture
def nurture_store() -> InMemoryNurtureStore:
    store = InMemoryNurtureStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def content_store() -> InMemoryContentMetricsStore:
    store = InMemoryContentMetricsStore(params=deps._params)
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    return InMemoryDecisionsStore()


@pytest.fixture
def client(
    nurture_store: InMemoryNurtureStore,
    content_store: InMemoryContentMetricsStore,
    decisions_store: InMemoryDecisionsStore,
) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[deps.get_nurture_store] = lambda: nurture_store
    app.dependency_overrides[deps.get_content_metrics_store] = lambda: content_store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    app.dependency_overrides[deps.get_crm_adapter_dep] = SimulatedCRMAdapter
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        for dep in (
            deps.get_nurture_store,
            deps.get_content_metrics_store,
            deps.get_decisions_store,
            deps.get_crm_adapter_dep,
        ):
            app.dependency_overrides.pop(dep, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    resp = client.get("/nurture/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {t["tier"] for t in body["tiers"]} == {"T1", "T2", "T3"}
    assert body["engagement_source"] == "crm_aggregate"
    mix = body["engagement_tier_mix"]
    assert mix["total"] == mix["clicked"] + mix["opened"] + mix["cold"]
    # The seeded SLA log: 10 of 30 contacted in the 24h window ⇒ 33%.
    assert body["sla_compliance_pct"] == 33
    assert body["sequences_total"] == 5
    assert body["sequences_healthy"] >= 1
    assert body["handoff_this_week"] >= 0


def test_segments_has_panels_and_heatmap(client: TestClient) -> None:
    resp = client.get("/nurture/segments", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["segments"]) == 6
    assert "income" in body["heatmap"]
    # Heatmap rows are tagged with an engagement tier.
    assert all("engagement_tier" in c for c in body["heatmap"]["income"])


def test_pipeline_distribution_live_aggregate(client: TestClient) -> None:
    resp = client.get("/nurture/pipeline", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "crm_aggregate"
    assert body["total"] == sum(s["count"] for s in body["stages"])
    assert 0 <= body["velocity_pct"] <= 100
    assert body["handoff"]["cumulative"] >= 0


def test_sequences_are_synthetic_mirror(client: TestClient) -> None:
    resp = client.get("/nurture/sequences", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "synthetic_mirror"
    assert len(body["sequences"]) == 5
    # The re-engagement laggard flags unhealthy.
    laggard = next(s for s in body["sequences"] if s["seq_type"] == "re_engagement")
    assert laggard["health_flag"] is True


def test_sms_list_and_filter(client: TestClient) -> None:
    resp = client.get("/nurture/sms", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["threads"]) == 14
    # Filter by status.
    resp = client.get("/nurture/sms", headers=_auth("operator"), params={"status": "objection"})
    assert resp.status_code == 200, resp.text
    threads = resp.json()["threads"]
    assert threads and all(t["status"] == "objection" for t in threads)
    # Theme tags are (re-)derived by the keyword core.
    assert all(t["tag_mode"] == "keyword" for t in threads)


def test_sla_view(client: TestClient) -> None:
    resp = client.get("/nurture/sla", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 30
    assert body["compliance_pct"] == 33
    assert body["window_hours"] == 24
    assert len(body["per_owner"]) == 3
    assert isinstance(body["late"], list)


def test_kpi_feed_shape(client: TestClient) -> None:
    resp = client.get("/nurture/kpi-feed", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pipeline_stage_distribution" in body
    assert "handoff" in body
    assert body["source"] == "crm_aggregate"


def test_attribution_feed(client: TestClient) -> None:
    resp = client.get("/nurture/attribution", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pieces"], "expected seeded content pieces"
    # The broken-UTM reality stays visible (some pieces are unattributable).
    assert body["unattributable_count"] >= 1


# ------------------------------------------------------------------- owner-gated write
def test_build_segment_leader_ok(client: TestClient) -> None:
    resp = client.post(
        "/nurture/segments/build",
        headers=_auth("leader"),
        json={"tier": "T1", "engagement_tiers": ["clicked"], "label": "Hot clicked"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "T1"
    assert body["owner"] == "nurture"
    assert body["size"] >= 0
    assert 0 <= body["reachability_pct"] <= 100


def test_build_segment_demo_operator_forbidden(client: TestClient) -> None:
    # The demo operator owns 'grassroots', not 'nurture' ⇒ 403 (admin/leader-only writes).
    resp = client.post("/nurture/segments/build", headers=_auth("operator"), json={"tier": "T2"})
    assert resp.status_code == 403, resp.text


def test_build_segment_unknown_tier_422(client: TestClient) -> None:
    resp = client.post("/nurture/segments/build", headers=_auth("leader"), json={"tier": "T9"})
    assert resp.status_code == 422, resp.text


# ----------------------------------------------------------------------- cross-links
def test_flag_hot_family_enqueues_nurture_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore, nurture_store: InMemoryNurtureStore
) -> None:
    resp = client.post(f"/nurture/sms/{_SEED_THREAD_0}/flag-hot-family", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workstream"] == "nurture"
    assert body["source"] == "sms_hot_family"
    # The thread is marked hot_family.
    assert nurture_store.get_thread(_PROGRAM, _SEED_THREAD_0).status == "hot_family"  # type: ignore[union-attr]
    # The decision actually landed in the queue.
    assert len(decisions_store.list_open(_PROGRAM)) == 1


def test_flag_hot_family_unknown_thread_404(client: TestClient) -> None:
    resp = client.post(f"/nurture/sms/{UUID(int=0xDEAD)}/flag-hot-family", headers=_auth("leader"))
    assert resp.status_code == 404, resp.text


def test_objection_brief_creates_content_draft(
    client: TestClient, content_store: InMemoryContentMetricsStore
) -> None:
    before = len(content_store.list_calendar(_PROGRAM))
    resp = client.post(
        "/nurture/sms/objection-brief", headers=_auth("leader"), json={"theme": "tuition"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert "tuition" in body["title"]
    # A new calendar entry was created in the Content store (the cross-module link).
    assert len(content_store.list_calendar(_PROGRAM)) == before + 1

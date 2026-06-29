"""Website API tests (Module 13) — reads, leadership-gated writes, the three cross-links.

Headline invariants:

- The READ paths (overview / subpages+filters / traffic / downloads / paths / inputs) are
  open to ANY authenticated seat and shape the simulated GA4 snapshot sensibly. The source
  is labelled honestly (``source_mode='simulated'``, never live — INV-6/9).
- CROSS-LINK 1 + 2: POST /website/pages/flag creates a Content calendar DRAFT (owner=
  website) AND enqueues ONE open `website` Decision-Queue card, persisting both ids.
- CROSS-LINK 3: GET /website/traffic runs the SAME check_utm rule set over the tagged
  campaigns → 3 broken (the CRM-Ops attribution-chain feed).
- POST /website/analysis enqueues a `website` decision and stores its id.
- Every WRITE is LEADER/ADMIN only (an operator is 403); resolve PATCHes 404 on unknown.

These hit the REAL main app (website_router registered), overriding the website store → a
fresh SEEDED in-memory store, plus the content-metrics + decisions stores + the analytics
adapter → fresh in-memory/simulated impls. The autouse conftest principal shim verifies
Bearer tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.adapters.analytics.simulated import SimulatedAnalyticsAdapter
from app.api import deps
from app.core.program import Program
from app.data.content_metrics_store import InMemoryContentMetricsStore
from app.data.decisions_store import InMemoryDecisionsStore
from app.data.website_store import InMemoryWebsiteStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT
_OPEN_FLAG = UUID(int=0xB13_0000)  # the seeded open page flag
_OPEN_REQUEST = UUID(int=0xB13_3000)  # the seeded open analysis request


def _auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture
def website_store() -> InMemoryWebsiteStore:
    store = InMemoryWebsiteStore()
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def content_store() -> InMemoryContentMetricsStore:
    return InMemoryContentMetricsStore()


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    return InMemoryDecisionsStore()


@pytest.fixture
def client(
    website_store: InMemoryWebsiteStore,
    content_store: InMemoryContentMetricsStore,
    decisions_store: InMemoryDecisionsStore,
) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[deps.get_website_store] = lambda: website_store
    app.dependency_overrides[deps.get_content_metrics_store] = lambda: content_store
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    app.dependency_overrides[deps.get_analytics_adapter_dep] = SimulatedAnalyticsAdapter
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        for dep in (
            deps.get_website_store,
            deps.get_content_metrics_store,
            deps.get_decisions_store,
            deps.get_analytics_adapter_dep,
        ):
            app.dependency_overrides.pop(dep, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    resp = client.get("/website/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_mode"] == "simulated"  # honest, never live (INV-6/9)
    assert body["site_rollup"]["total_sessions"] == 11530
    assert len(body["top_landing_pages"]) == 5
    assert body["download_summary"]["total_weekly"] == 523
    assert body["open_flag_count"] == 1
    assert body["open_request_count"] == 1


def test_subpages_filter_and_sort(client: TestClient) -> None:
    resp = client.get("/website/subpages", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    pv = [p["pageviews"] for p in resp.json()["pages"]]
    assert pv == sorted(pv, reverse=True)  # default sort=pageviews

    resp = client.get(
        "/website/subpages", headers=_auth("operator"), params={"site": "anywhere.gt.school"}
    )
    assert {p["site"] for p in resp.json()["pages"]} == {"anywhere.gt.school"}

    resp = client.get("/website/subpages", headers=_auth("operator"), params={"page_type": "form"})
    assert {p["page_type"] for p in resp.json()["pages"]} == {"form"}
    assert all(p["refresh_candidate"] is False for p in resp.json()["pages"])  # forms low-bounce


def test_traffic_utm_validation_three_broken(client: TestClient) -> None:
    resp = client.get("/website/traffic", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["breakdown"]["channels"][0]["channel"] == "organic"
    # CROSS-LINK 3: the website is the ORIGIN of UTM tags — 3 broken feed CRM Ops.
    assert body["utm_validation"]["broken_count"] == 3
    assert body["utm_validation"]["total"] == 6


def test_downloads_ranked(client: TestClient) -> None:
    resp = client.get("/website/downloads", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    weekly = [d["weekly_count"] for d in resp.json()["downloads"]]
    assert weekly == sorted(weekly, reverse=True)


def test_paths_funnel_and_key_pages(client: TestClient) -> None:
    resp = client.get("/website/paths", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["funnel"][0]["of_top_pct"] == 100
    assert body["key_conversion_pages"][0]["page_path"] == "/apply"
    assert body["cross_site_flows"]


# ----------------------------------------- CROSS-LINK 1 + 2: flag page → brief + decision
def test_flag_page_creates_brief_and_decision(
    client: TestClient,
    content_store: InMemoryContentMetricsStore,
    decisions_store: InMemoryDecisionsStore,
    website_store: InMemoryWebsiteStore,
) -> None:
    resp = client.post(
        "/website/pages/flag",
        headers=_auth("leader"),
        json={"page_path": "/blog/2-hour-learning", "site": "gt.school", "reason": "62% bounce"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brief_entry_id"] is not None
    assert body["decision_id"] is not None
    # A Content DRAFT calendar entry owned by website (CROSS-LINK → Module 3).
    calendar = content_store.list_calendar(_PROGRAM)
    assert len(calendar) == 1
    assert calendar[0].owner == "website"
    assert calendar[0].status == "draft"
    # One open `website` Decision-Queue card (CROSS-LINK → Module 11).
    open_decisions = decisions_store.list_open(_PROGRAM)
    assert len(open_decisions) == 1
    assert open_decisions[0].workstream == "website"
    # The persisted flag links both ids (the newly created open flag for this page).
    new_flags = [
        f
        for f in website_store.list_page_flags(_PROGRAM)
        if f.page_path == "/blog/2-hour-learning" and f.brief_entry_id is not None
    ]
    assert any(f.brief_entry_id is not None and f.decision_id is not None for f in new_flags)


def test_flag_page_bad_site_422(client: TestClient) -> None:
    resp = client.post(
        "/website/pages/flag",
        headers=_auth("leader"),
        json={"page_path": "/x", "site": "evil.example", "reason": "r"},
    )
    assert resp.status_code == 422, resp.text


def test_flag_page_operator_forbidden(client: TestClient) -> None:
    resp = client.post(
        "/website/pages/flag",
        headers=_auth("operator"),
        json={"page_path": "/x", "site": "gt.school", "reason": "r"},
    )
    assert resp.status_code == 403, resp.text


# ----------------------------------------- request analysis → Decision Queue (Module 11)
def test_request_analysis_enqueues_decision(
    client: TestClient, decisions_store: InMemoryDecisionsStore
) -> None:
    resp = client.post(
        "/website/analysis",
        headers=_auth("leader"),
        json={"target": "/tuition", "target_kind": "page", "question": "why the jump?"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision_id"] is not None
    open_decisions = decisions_store.list_open(_PROGRAM)
    assert any(d.workstream == "website" for d in open_decisions)


def test_request_analysis_bad_kind_422(client: TestClient) -> None:
    resp = client.post(
        "/website/analysis",
        headers=_auth("leader"),
        json={"target": "x", "target_kind": "galaxy", "question": "q"},
    )
    assert resp.status_code == 422, resp.text


def test_request_analysis_operator_forbidden(client: TestClient) -> None:
    resp = client.post(
        "/website/analysis",
        headers=_auth("operator"),
        json={"target": "x", "target_kind": "page", "question": "q"},
    )
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------- resolve (leadership only)
def test_resolve_flag_leader(client: TestClient) -> None:
    resp = client.patch(
        f"/website/pages/flag/{_OPEN_FLAG}", headers=_auth("leader"), json={"action": "resolve"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"


def test_resolve_request_leader(client: TestClient) -> None:
    resp = client.patch(
        f"/website/analysis/{_OPEN_REQUEST}", headers=_auth("admin"), json={"action": "resolve"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"


def test_resolve_flag_operator_forbidden(client: TestClient) -> None:
    resp = client.patch(
        f"/website/pages/flag/{_OPEN_FLAG}", headers=_auth("operator"), json={"action": "resolve"}
    )
    assert resp.status_code == 403, resp.text


def test_resolve_flag_unknown_404(client: TestClient) -> None:
    resp = client.patch(
        f"/website/pages/flag/{UUID(int=0xDEAD)}",
        headers=_auth("leader"),
        json={"action": "resolve"},
    )
    assert resp.status_code == 404, resp.text

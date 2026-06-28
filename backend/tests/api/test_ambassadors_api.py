"""API test for GET /ambassadors/reconcile (Grassroots dual-source surface).

The router is not registered on the main app (the include line is added
separately), so these tests mount it on a fresh FastAPI app and exercise it both
with the default synthetic sources and with an overridden fixture that forces a
known matched / only / conflict mix.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ambassadors import get_ambassador_sources, router
from app.core.ambassador_reconcile import AmbassadorRecord
from app.data.synthetic_ambassadors import AmbassadorSource, AmbassadorSources


def _client(app: FastAPI) -> TestClient:
    app.include_router(router)
    return TestClient(app)


def test_reconcile_returns_union_counts_conflicts_and_source_health() -> None:
    client = _client(FastAPI())
    resp = client.get("/ambassadors/reconcile")
    assert resp.status_code == 200
    body = resp.json()

    # The curated synthetic roster: 11 union, 7 matched, 2/2 only, 2 conflicts.
    assert body["counts"] == {
        "union": 11,
        "matched": 7,
        "hubspot_only": 2,
        "community_only": 2,
        "conflicts": 2,
    }
    assert len(body["union"]) == 11
    assert len(body["conflicts"]) == 2

    # Source health is real (derived), not a hardcoded badge.
    assert body["source_health"] == "ok"
    assert body["reconciled_minutes_ago"] == 14  # max(6, 14)
    names = {s["name"] for s in body["sources"]}
    assert names == {"HubSpot", "community.gt.school"}

    # Every union row carries a provenance the UI can render.
    provenances = {r["provenance"] for r in body["union"]}
    assert provenances == {"both", "hubspot-only", "community-only"}


def test_reconcile_with_overridden_sources() -> None:
    def _rec(name: str, email: str, status: str) -> AmbassadorRecord:
        return AmbassadorRecord(
            synthetic_name=name,
            synthetic_email=email,
            segment="Chess club",
            region="Round Rock",
            status=status,
        )

    fixture = AmbassadorSources(
        hubspot=AmbassadorSource(
            name="HubSpot",
            rows=(
                _rec("Match One", "m.1@example.invalid", "Active"),
                _rec("Conflict Two", "c.2@example.invalid", "Champion"),
                _rec("HubOnly Three", "h.3@example.invalid", "Onboarded"),
            ),
            synced_minutes_ago=3,
        ),
        community=AmbassadorSource(
            name="community.gt.school",
            rows=(
                _rec("Match One", "m.1@example.invalid", "Active"),
                _rec("Conflict Two", "c.2@example.invalid", "Active"),  # status differs
                _rec("CommOnly Four", "c.4@example.invalid", "Active"),
            ),
            synced_minutes_ago=20,
        ),
    )

    app = FastAPI()
    app.dependency_overrides[get_ambassador_sources] = lambda: fixture
    client = _client(app)

    body = client.get("/ambassadors/reconcile").json()
    assert body["counts"] == {
        "union": 4,
        "matched": 2,
        "hubspot_only": 1,
        "community_only": 1,
        "conflicts": 1,
    }
    assert body["reconciled_minutes_ago"] == 20  # max(3, 20)
    conflict = body["conflicts"][0]
    assert conflict["synthetic_name"] == "Conflict Two"
    assert conflict["hubspot_value"] == "Champion"
    assert conflict["community_value"] == "Active"


def test_reconcile_reports_degraded_when_a_source_is_empty() -> None:
    fixture = AmbassadorSources(
        hubspot=AmbassadorSource(name="HubSpot", rows=(), synced_minutes_ago=5),
        community=AmbassadorSource(
            name="community.gt.school",
            rows=(
                AmbassadorRecord(
                    synthetic_name="Solo One",
                    synthetic_email="s.1@example.invalid",
                    segment="Math circle",
                    region="Frisco",
                    status="Active",
                ),
            ),
            synced_minutes_ago=8,
        ),
    )
    app = FastAPI()
    app.dependency_overrides[get_ambassador_sources] = lambda: fixture
    client = _client(app)

    body = client.get("/ambassadors/reconcile").json()
    assert body["source_health"] == "degraded"
    assert body["counts"]["community_only"] == 1

"""API test for GET /summer/reconcile (D2).

The router is NOT registered in main.py yet (the include line is handed back in the
handoff), so this test mounts the summer router on a STANDALONE app and overrides
``get_principal`` to an admin — exercising the real composition (synthetic sources →
pure reconciler → wire projection) without depending on the main app's wiring.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.summer import router as summer_router


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(summer_router)
    app.dependency_overrides[deps.get_principal] = lambda: deps.Principal(role="admin")
    with TestClient(app) as test_client:
        yield test_client


def test_get_summer_reconcile(client: TestClient) -> None:
    resp = client.get("/summer/reconcile")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["program_id"] == "summer_camp"

    # --- per-campus rollup sums to capacity ---
    campuses = body["per_campus"]
    assert len(campuses) == 4
    assert sum(c["capacity"] for c in campuses) == 350
    for c in campuses:
        assert 0 <= c["registered"] <= c["capacity"]
        assert c["lead"] == c["registered"] - c["paid"]

    totals = body["totals"]
    assert totals["capacity"] == 350
    assert totals["registered"] == 288
    assert totals["paid"] == 219
    assert totals["lead"] == 288 - 219

    # --- dedup summary: the no-double-count proof ---
    dedup = body["dedup"]
    assert dedup["unique_registrations"] == 288
    assert dedup["raw_source_rows"] > dedup["unique_registrations"]  # overlap existed
    assert dedup["duplicates_merged"] == dedup["raw_source_rows"] - 288
    assert dedup["conflicts"] == []
    assert {s["source"] for s in dedup["sources"]} == {"summer_site", "registration_form"}
    assert sum(s["rows"] for s in dedup["sources"]) == dedup["raw_source_rows"]

    # --- revenue vs target ---
    rev = body["revenue"]
    assert rev["paid_registrations"] == 219
    assert rev["revenue_usd"] == 219 * rev["price_per_seat_usd"]
    # The surfaced price + target read from params.summer_camp (INV-11), not a literal.
    summer_params = deps.get_params().summer_camp
    assert rev["price_per_seat_usd"] == summer_params.price_per_seat_usd
    assert rev["target_usd"] == summer_params.revenue_target_usd


def test_summer_reconcile_requires_auth() -> None:
    """No principal override ⇒ get_principal default-denies (no JWT secret ⇒ 401)."""
    app = FastAPI()
    app.include_router(summer_router)
    with TestClient(app) as test_client:
        resp = test_client.get("/summer/reconcile")
    assert resp.status_code == 401, resp.text

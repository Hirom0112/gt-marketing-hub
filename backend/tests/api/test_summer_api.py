"""API tests for the Summer Camp surface (Module 4).

The router IS registered in main.py, but these mount it on a STANDALONE app and
override ``get_principal`` / the camp + decisions + sheets store seams — exercising the
real composition (store → pure reconciler → wire projection; live-kanban filter;
owner-gated cross-link) without depending on the main app's global singletons or a live
Google Sheet.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.sheets.simulated import SimulatedSheetsAdapter
from app.api import deps
from app.api import summer as summer_api
from app.api.summer import router as summer_router
from app.core.program import Program
from app.data.camp_store import InMemoryCampStore
from app.data.decisions_store import InMemoryDecisionsStore

_CAMP = Program.SUMMER_CAMP
# A deterministic operator agent id used for the FOREIGN-operator deny case.
_FOREIGN_AGENT = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def camp_store() -> InMemoryCampStore:
    """A fresh SEEDED in-memory camp store (both sources + channels + sessions)."""
    store = InMemoryCampStore(params=deps.get_params())
    store.seed_demo(_CAMP)
    return store


@pytest.fixture
def client(camp_store: InMemoryCampStore) -> Iterator[TestClient]:
    """The summer router with the camp store overridden + an admin principal."""
    app = FastAPI()
    app.include_router(summer_router)
    app.dependency_overrides[deps.get_camp_store] = lambda: camp_store
    app.dependency_overrides[deps.get_principal] = lambda: deps.Principal(role="admin")
    with TestClient(app) as test_client:
        yield test_client


# =========================================================================== reconcile
def test_get_summer_reconcile(client: TestClient) -> None:
    resp = client.get("/summer/reconcile")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["program_id"] == "summer_camp"

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

    dedup = body["dedup"]
    assert dedup["unique_registrations"] == 288
    assert dedup["raw_source_rows"] > dedup["unique_registrations"]
    assert dedup["duplicates_merged"] == dedup["raw_source_rows"] - 288
    assert dedup["conflicts"] == []
    assert {s["source"] for s in dedup["sources"]} == {"summer_site", "registration_form"}

    rev = body["revenue"]
    assert rev["paid_registrations"] == 219
    assert rev["revenue_usd"] == 219 * rev["price_per_seat_usd"]
    summer_params = deps.get_params().summer_camp
    assert rev["price_per_seat_usd"] == summer_params.price_per_seat_usd
    assert rev["target_usd"] == summer_params.revenue_target_usd
    assert rev["basis"] == "synthetic_paid_times_price"  # honest: no Stripe this phase


def test_reconcile_revenue_synthetic_fallback_when_ledger_empty(client: TestClient) -> None:
    """No camp payments ⇒ revenue falls back to the synthetic paid × price estimate."""
    rev = client.get("/summer/reconcile").json()["revenue"]
    assert rev["basis"] == "synthetic_paid_times_price"
    assert rev["collected_count"] == 0
    assert rev["revenue_usd"] == 219 * rev["price_per_seat_usd"]
    # The synthetic per-campus split sums back to the total.
    assert sum(rev["revenue_by_campus"].values()) == rev["revenue_usd"]


def test_reconcile_revenue_stripe_collected_when_ledger_has_charges(
    client: TestClient, camp_store: InMemoryCampStore
) -> None:
    """A camp ledger with succeeded charges ⇒ revenue reads REAL collected revenue."""
    camp_store.record_camp_payment(
        _CAMP,
        payment_id="pi_a",
        campus="Austin",
        amount_cents=97500,
        currency="usd",
        status="succeeded",
        stripe_event_id="evt_a",
    )
    camp_store.record_camp_payment(
        _CAMP,
        payment_id="pi_b",
        campus="Dallas",
        amount_cents=97500,
        currency="usd",
        status="succeeded",
        stripe_event_id="evt_b",
    )
    # A non-succeeded charge must NOT count toward collected revenue.
    camp_store.record_camp_payment(
        _CAMP,
        payment_id="pi_c",
        campus="Austin",
        amount_cents=97500,
        currency="usd",
        status="requires_payment_method",
        stripe_event_id="evt_c",
    )

    rev = client.get("/summer/reconcile").json()["revenue"]
    assert rev["basis"] == "stripe_collected"
    assert rev["collected_count"] == 2
    assert rev["revenue_usd"] == 1950  # 2 × $975
    assert rev["revenue_by_campus"] == {"Austin": 975, "Dallas": 975}
    # Yield per registered family = revenue_usd / registered (288 registered).
    assert rev["revenue_per_registered_usd"] == round(1950 / 288, 2)


def test_reconcile_revenue_collected_is_idempotent_on_redelivery(
    client: TestClient, camp_store: InMemoryCampStore
) -> None:
    """Recording the SAME PaymentIntent twice does not double-count collected revenue."""
    for _ in range(2):
        camp_store.record_camp_payment(
            _CAMP,
            payment_id="pi_dup",
            campus="Austin",
            amount_cents=97500,
            currency="usd",
            status="succeeded",
            stripe_event_id="evt_dup",
        )
    rev = client.get("/summer/reconcile").json()["revenue"]
    assert rev["collected_count"] == 1
    assert rev["revenue_usd"] == 975


def test_reconcile_extended_dimensions(client: TestClient) -> None:
    body = client.get("/summer/reconcile").json()

    # Channel breakdown — word_of_mouth top, deduped counts sum to 288.
    channels = body["registration_channels"]
    assert channels[0]["channel"] == "word_of_mouth"
    assert sum(c["count"] for c in channels) == 288

    # Funnel — four stages, Attended pending.
    funnel = {f["stage"]: f for f in body["funnel"]}
    assert set(funnel) == {"Lead", "Registered", "Paid", "Attended"}
    assert funnel["Registered"]["count"] == 288
    assert funnel["Paid"]["count"] == 219
    assert funnel["Attended"]["count"] == 0
    assert funnel["Attended"]["pending"] is True

    # Sessions — the four Aug-2026 cohorts.
    assert len(body["sessions"]) == 4

    # Waitlist — every campus under capacity ⇒ 0 overflow.
    assert all(w["waitlisted"] == 0 for w in body["waitlist"])

    # Recent-window count + countdown — present & sensible (now injected at the edge).
    assert isinstance(body["registrations_this_week"], int)
    assert 0 <= body["registrations_this_week"] <= 288
    assert isinstance(body["days_to_camp_start"], int)

    assert body["applied_filters"] == {"campus": None, "grade_band": None, "source": None}


def test_reconcile_campus_slice(client: TestClient) -> None:
    body = client.get("/summer/reconcile", params={"campus": "Austin"}).json()
    assert [c["campus"] for c in body["per_campus"]] == ["Austin"]
    assert body["totals"]["registered"] == 86  # the Austin synthetic fill target
    assert body["applied_filters"]["campus"] == "Austin"
    # Sessions also narrow to the sliced campus.
    assert {s["campus"] for s in body["sessions"]} == {"Austin"}


def test_reconcile_source_slice(client: TestClient) -> None:
    body = client.get("/summer/reconcile", params={"source": "summer_site"}).json()
    # Only one source remains in the dedup provenance.
    assert {s["source"] for s in body["dedup"]["sources"]} == {"summer_site"}
    assert body["applied_filters"]["source"] == "summer_site"


def test_summer_reconcile_requires_auth() -> None:
    """No principal override ⇒ get_principal default-denies (no JWT secret ⇒ 401)."""
    app = FastAPI()
    app.include_router(summer_router)
    with TestClient(app) as test_client:
        resp = test_client.get("/summer/reconcile")
    assert resp.status_code == 401, resp.text


# =========================================================================== content
@pytest.fixture
def content_client() -> Iterator[TestClient]:
    """The summer router with a SEEDED simulated sheets adapter (no live Google call)."""
    app = FastAPI()
    app.include_router(summer_router)
    sim = SimulatedSheetsAdapter.seeded()  # general kanban seed (no camp rows yet)
    app.dependency_overrides[deps.get_sheets_adapter_dep] = lambda: sim
    app.dependency_overrides[deps.get_principal] = lambda: deps.Principal(role="admin")
    with TestClient(app) as test_client:
        yield test_client


def test_summer_content_returns_only_camp_rows(content_client: TestClient) -> None:
    resp = content_client.get("/summer/content")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["stages"] == ["Backlog", "Drafting", "Review", "Scheduled", "Live"]
    # ONLY camp_-tagged rows are returned (the general seed rows are excluded).
    assert body["rows"], "expected the seeded camp rows"
    assert all(r["utm"].startswith("camp_") for r in body["rows"])
    titles = {r["title"] for r in body["rows"]}
    assert {"Camp guide interviews", "Pilot outcomes recap", "Welcome kit content"} <= titles

    # Grouped columns line up with the flat rows.
    total_cards = sum(len(c["cards"]) for c in body["columns"])
    assert total_cards == len(body["rows"])
    # Honest sync label for the simulated seam.
    assert body["sync"]["mode"] == "simulate"
    assert body["sync"]["synced"] is False


def test_summer_content_is_idempotent(content_client: TestClient) -> None:
    first = content_client.get("/summer/content").json()["rows"]
    second = content_client.get("/summer/content").json()["rows"]
    assert len(first) == len(second)  # re-seeding adds nothing new


# =================================================================== session-change
def _principal(role: str, agent_id: UUID | None = None) -> deps.Principal:
    return deps.Principal(role=role, agent_id=agent_id)


@pytest.fixture
def decisions_store() -> InMemoryDecisionsStore:
    return InMemoryDecisionsStore()


def _session_change_app(
    decisions_store: InMemoryDecisionsStore, principal: deps.Principal
) -> TestClient:
    app = FastAPI()
    app.include_router(summer_router)
    app.dependency_overrides[deps.get_decisions_store] = lambda: decisions_store
    app.dependency_overrides[deps.get_active_program] = lambda: _CAMP
    app.dependency_overrides[deps.get_principal] = lambda: principal
    return TestClient(app)


_CHANGE_BODY = {"campus": "Austin", "change_type": "pricing", "detail": "Raise to $1,050"}


def test_session_change_operator_owns_camp_allowed(
    decisions_store: InMemoryDecisionsStore,
) -> None:
    client = _session_change_app(decisions_store, _principal("operator", UUID(int=1)))
    resp = client.post("/summer/session-change", json=_CHANGE_BODY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "summer_session_change"
    assert body["workstream"] == "camp"
    # The decision landed in the queue.
    assert len(decisions_store.list_open(_CAMP)) == 1


def test_session_change_foreign_operator_forbidden(
    decisions_store: InMemoryDecisionsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(summer_api.OPERATOR_WORKSTREAMS, str(_FOREIGN_AGENT), "content")
    client = _session_change_app(decisions_store, _principal("operator", _FOREIGN_AGENT))
    resp = client.post("/summer/session-change", json=_CHANGE_BODY)
    assert resp.status_code == 403, resp.text
    assert decisions_store.list_open(_CAMP) == []


def test_session_change_leader_allowed(decisions_store: InMemoryDecisionsStore) -> None:
    client = _session_change_app(decisions_store, _principal("leader"))
    resp = client.post("/summer/session-change", json=_CHANGE_BODY)
    assert resp.status_code == 200, resp.text
    assert len(decisions_store.list_open(_CAMP)) == 1


def test_session_change_rejects_unknown_priority(
    decisions_store: InMemoryDecisionsStore,
) -> None:
    client = _session_change_app(decisions_store, _principal("leader"))
    resp = client.post("/summer/session-change", json={**_CHANGE_BODY, "priority": "whenever"})
    assert resp.status_code == 422, resp.text

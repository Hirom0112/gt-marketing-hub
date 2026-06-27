"""Open Data enrichment API tests (E1) — the "Open Data query that CHANGES a decision".

The headline: ``POST /open-data/enrich`` runs a Texas-district Open Data query
(through the SEEDED adapter), applies the pure ``enrich_decision`` core, and — when
the recommendation actually MOVES — feeds the changed rec into the B2 Decision Queue
as a card carrying full provenance + the data SOURCE (live OpenData vs seeded).

These tests hit the REAL main app (with ``open_data_router`` registered), overriding
only :func:`app.api.deps.get_open_data_adapter_dep` → the v1 ``SeededOpenDataAdapter``
and :func:`app.api.deps.get_decisions_store` → a fresh in-memory store per test. The
autouse conftest principal shim verifies Bearer tokens against the test secret.

The seeded adapter's two documented poles drive the two ends:

- ``SeededOpenDataAdapter.LOW_RATED_DISTRICT`` (F-rated, STAAR 0.31, enrollment 820)
  trips all three signals ⇒ the rec is boosted ⇒ EXACTLY ONE open
  ``open_data_enrichment`` decision lands in the queue (the end-to-end proof).
- ``SeededOpenDataAdapter.A_RATED_DISTRICT`` (A-rated) trips none ⇒ unchanged ⇒ NO
  decision is enqueued (honest: only a real change enqueues).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.open_data.seeded import SeededOpenDataAdapter
from app.api import deps
from app.core.program import Program
from app.data.decisions_store import InMemoryDecisionsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

_PROGRAM = Program.FALL_ENROLLMENT


def _auth(role: str = "leader") -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture
def store() -> InMemoryDecisionsStore:
    """A fresh in-memory decisions store per test (full isolation)."""
    return InMemoryDecisionsStore()


@pytest.fixture
def client(store: InMemoryDecisionsStore) -> Iterator[TestClient]:
    """The main app, with the Open Data adapter + decisions store + program overridden."""
    from app.main import app

    # Override with a FACTORY (a constructed instance), not the bare class: FastAPI
    # introspects a class override's ``__init__`` signature as sub-dependencies, which
    # would misclassify ``SeededOpenDataAdapter(fixture=...)`` and corrupt body parsing.
    seeded = SeededOpenDataAdapter()
    app.dependency_overrides[deps.get_open_data_adapter_dep] = lambda: seeded
    app.dependency_overrides[deps.get_decisions_store] = lambda: store
    app.dependency_overrides[deps.get_active_program] = lambda: _PROGRAM
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_open_data_adapter_dep, None)
        app.dependency_overrides.pop(deps.get_decisions_store, None)
        app.dependency_overrides.pop(deps.get_active_program, None)


# ------------------------------------------------------------ the change + enqueue
def test_low_rated_district_changes_decision_and_enqueues(
    client: TestClient, store: InMemoryDecisionsStore
) -> None:
    """The F-rated pole boosts the rec → response says changed → ONE open decision lands."""
    base_priority = 0
    resp = client.post(
        "/open-data/enrich",
        headers=_auth("leader"),
        json={
            "district_id": SeededOpenDataAdapter.LOW_RATED_DISTRICT,
            "base_priority": base_priority,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The recommendation MOVED, and the new priority is strictly higher.
    assert body["recommendation_changed"] is True
    assert body["new_priority"] > base_priority

    # Provenance lists the three under-served signals + the boost reason.
    provenance = body["provenance"]
    assert provenance["reason"] == "under_served_district"
    assert set(provenance["signals"]) == {
        "low_rating",
        "staar_below_floor",
        "enrollment_at_min",
    }

    # The SOURCE badge: v1 default mode is simulate ⇒ surfaced as "seeded".
    assert body["data_source"] == "seeded"

    # The enrichment fields are echoed (so the UI can render WHY it changed).
    assert body["enrichment"]["district_id"] == SeededOpenDataAdapter.LOW_RATED_DISTRICT
    assert body["enrichment"]["d_rating"] == "F"

    # End-to-end proof: EXACTLY ONE open `open_data_enrichment` decision now exists,
    # carrying the provenance + data_source the leader will see in the Decision Queue.
    open_decisions = store.list_open(_PROGRAM)
    enrichment_cards = [d for d in open_decisions if d.source == "open_data_enrichment"]
    assert len(enrichment_cards) == 1
    card = enrichment_cards[0]
    assert card.payload["district_id"] == SeededOpenDataAdapter.LOW_RATED_DISTRICT
    assert card.payload["data_source"] == "seeded"
    assert card.payload["recommendation"]["new_priority"] > base_priority
    assert set(card.payload["provenance"]["signals"]) == {
        "low_rating",
        "staar_below_floor",
        "enrollment_at_min",
    }


# ----------------------------------------------------------- no change ⇒ no enqueue
def test_a_rated_district_unchanged_enqueues_nothing(
    client: TestClient, store: InMemoryDecisionsStore
) -> None:
    """The A-rated pole trips no signal → unchanged → NO decision is enqueued (honest)."""
    resp = client.post(
        "/open-data/enrich",
        headers=_auth("leader"),
        json={"district_id": SeededOpenDataAdapter.A_RATED_DISTRICT},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["recommendation_changed"] is False
    assert body["data_source"] == "seeded"

    # Nothing enqueued: only a REAL change feeds the queue.
    assert store.list_open(_PROGRAM) == []


# -------------------------------------------------------------------- default-deny
def test_no_token_unauthorized(client: TestClient) -> None:
    """No Authorization header → 401 (default-deny inherited from get_principal)."""
    from app.main import app

    # Pop the autouse admin-on-no-token shim so the REAL default-deny path runs.
    app.dependency_overrides.pop(deps.get_principal, None)
    try:
        resp = client.post(
            "/open-data/enrich",
            json={"district_id": SeededOpenDataAdapter.LOW_RATED_DISTRICT},
        )
        assert resp.status_code == 401, resp.text
    finally:
        from tests.conftest import install_test_principal_override

        install_test_principal_override()

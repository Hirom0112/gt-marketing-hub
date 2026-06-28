"""Content-analytics API tests (Module 3) — reads, owner-gated calendar writes, brand voice.

Headline invariants:

- The READ paths (overview / calendar / performance / testimonial-stubs) are open to ANY
  authenticated seat and shape the seeded demo store sensibly (the 42% X engine, the two
  conflict days, X top / Facebook bottom, the UTM-attributable subset).
- The calendar WRITE paths are OWNER-gated: an operator who OWNS ``content`` may write, a
  FOREIGN operator (mapped to another workstream) is 403, a leader may write.
- ``brand-voice/suggest`` returns ADVISORY suggestions + a brand score (heuristic path
  forced via an injected judge so no live LLM call runs).

These hit the REAL main app (with ``content_analytics_router`` registered), overriding
the content-metrics store → a fresh SEEDED in-memory store, the content library → a fresh
in-memory double carrying a grassroots testimonial draft, and the brand judge → the
deterministic heuristic. The autouse conftest principal shim verifies Bearer tokens.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ai.brand_judge import heuristic_brand_score
from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import Channel, GeneratedBy, LifecycleStage, Provenance
from app.api import content_analytics as content_api
from app.api import deps
from app.core.program import Program
from app.data.content_metrics_store import InMemoryContentMetricsStore
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt
from tests.support.content_library import InMemoryContentLibrary

_PROGRAM = Program.FALL_ENROLLMENT
# A deterministic operator agent id used for the FOREIGN-operator deny case.
_FOREIGN_AGENT = "33333333-3333-4333-8333-333333333333"


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_agent(role: str, agent_id: str) -> dict[str, str]:
    """A signed ``role`` JWT with an explicit ``agent_id`` (drives the owner gate)."""
    return {
        "Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, agent_id=agent_id)}"
    }


def _testimonial_draft() -> LibraryAsset:
    """A grassroots testimonial DRAFT asset (the cross-module stub the stubs read surfaces)."""
    return LibraryAsset(
        id="grassroots-testimonial-test-1",
        title="A parent's story",
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        body="GT School changed how my child learns.",
        source_ref="grassroots_testimonial",
        tags=["grassroots_testimonial", "testimonial"],
        search_text="A parent's story testimonial",
        validation="pending-grassroots-testimonial-stub",
        lifecycle=LifecycleStage.DRAFT,
        provenance=Provenance(generated_by=GeneratedBy.HUMAN, created_at="2026-06-15T00:00:00Z"),
    )


@pytest.fixture
def content_store() -> InMemoryContentMetricsStore:
    """A fresh SEEDED in-memory content-metrics store per test (the demo calendar/metrics)."""
    store = InMemoryContentMetricsStore(params=deps.get_params())
    store.seed_demo(_PROGRAM)
    return store


@pytest.fixture
def content_library() -> InMemoryContentLibrary:
    """A fresh in-memory library carrying one grassroots testimonial DRAFT."""
    library = InMemoryContentLibrary()
    library.add(_testimonial_draft())
    return library


@pytest.fixture
def client(
    content_store: InMemoryContentMetricsStore,
    content_library: InMemoryContentLibrary,
) -> Iterator[TestClient]:
    """The main app with the content store/library/brand-judge overridden per test."""
    from app.main import app

    app.dependency_overrides[deps.get_content_metrics_store] = lambda: content_store
    app.dependency_overrides[deps.get_content_library_dep] = lambda: content_library
    # Force the deterministic heuristic judge so brand-voice needs no live LLM call.
    app.dependency_overrides[deps.get_brand_judge] = lambda: heuristic_brand_score
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_content_metrics_store, None)
        app.dependency_overrides.pop(deps.get_content_library_dep, None)
        app.dependency_overrides.pop(deps.get_brand_judge, None)


# ------------------------------------------------------------------------- READ paths
def test_overview(client: TestClient) -> None:
    """GET /content/overview → the 3a hero rollup with the computed 42% X engine."""
    resp = client.get("/content/overview", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["x_conversion_rate_pct"] == 42  # computed from the seeded reach/clicks
    assert body["productions_in_flight"] > 0
    # The grassroots testimonial draft is surfaced as a recently-captured stub.
    assert body["testimonial_stub_count"] == 1
    # The draft is NOT kept, so it does not count toward the kept-library count.
    assert body["library_count"] == 0
    assert len(body["channel_standins"]) == 7
    assert body["top_piece_title"] == "Why two hours a day works"


def test_calendar_has_conflicts(client: TestClient) -> None:
    """GET /content/calendar → the month entries + the two seeded same-day conflict days."""
    resp = client.get("/content/calendar", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["entries"]) == 18
    assert body["conflict_threshold"] == 4
    assert len(body["conflict_dates"]) == 2  # the two deliberate over-booked days


def test_performance(client: TestClient) -> None:
    """GET /content/performance → channel breakdown (X top / FB bottom) + piece rankings."""
    resp = client.get("/content/performance", headers=_auth("admin"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_channel = {c["channel"]: c for c in body["channels"]}
    assert by_channel["x"]["is_top"] is True
    assert by_channel["facebook"]["is_bottom"] is True
    assert by_channel["x"]["conversion_rate_pct"] == 42
    assert len(body["top_pieces"]) == 3
    assert len(body["bottom_pieces"]) == 3
    # 3 seeded pieces are UTM-attributed; the other 5 are honestly unattributable.
    assert len(body["content_to_conversion"]) == 3
    assert body["unattributable_count"] == 5
    # The honesty label rides along so the UI can render provenance.
    assert by_channel["x"]["source_kind"] in {"stood_in", "manual", "x_api", "meta_api", "hubspot"}


def test_testimonial_stubs(client: TestClient) -> None:
    """GET /content/testimonial-stubs → the grassroots DRAFT (search hides it)."""
    resp = client.get("/content/testimonial-stubs", headers=_auth("operator"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["source_ref"] == "grassroots_testimonial"
    assert rows[0]["title"] == "A parent's story"


# --------------------------------------------------------------------- the owner gate
def test_reschedule_owner_operator_ok(
    client: TestClient, content_store: InMemoryContentMetricsStore
) -> None:
    """An operator who OWNS content may reschedule a calendar entry."""
    entry = content_store.list_calendar(_PROGRAM)[0]
    resp = client.post(
        "/content/calendar/reschedule",
        headers=_auth("operator"),
        json={"entry_id": str(entry.entry_id), "new_date": "2026-07-15"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scheduled_date"] == "2026-07-15"


def test_reschedule_foreign_operator_forbidden(
    client: TestClient,
    content_store: InMemoryContentMetricsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FOREIGN operator (mapped to another workstream) is 403 on a write."""
    monkeypatch.setitem(content_api.OPERATOR_WORKSTREAMS, _FOREIGN_AGENT, "grassroots")
    entry = content_store.list_calendar(_PROGRAM)[0]
    resp = client.post(
        "/content/calendar/reschedule",
        headers=_auth_agent("operator", _FOREIGN_AGENT),
        json={"entry_id": str(entry.entry_id), "new_date": "2026-07-15"},
    )
    assert resp.status_code == 403, resp.text


def test_reschedule_leader_ok(
    client: TestClient, content_store: InMemoryContentMetricsStore
) -> None:
    """A leader may write (leadership may write any workstream)."""
    entry = content_store.list_calendar(_PROGRAM)[0]
    resp = client.post(
        "/content/calendar/reschedule",
        headers=_auth("leader"),
        json={"entry_id": str(entry.entry_id), "new_date": "2026-07-20"},
    )
    assert resp.status_code == 200, resp.text


def test_reschedule_unknown_404(client: TestClient) -> None:
    """An unknown entry → 404 (the store raises KeyError; the route maps it)."""
    missing = "99999999-9999-4999-8999-999999999999"
    resp = client.post(
        "/content/calendar/reschedule",
        headers=_auth("leader"),
        json={"entry_id": missing, "new_date": "2026-07-15"},
    )
    assert resp.status_code == 404, resp.text


def test_create_calendar_entry_leader(client: TestClient) -> None:
    """A leader may create a calendar entry, which round-trips into the calendar read."""
    resp = client.post(
        "/content/calendar/entry",
        headers=_auth("leader"),
        json={
            "title": "New thought-leadership thread",
            "channel": "x",
            "scheduled_date": "2026-07-01",
            "status": "planned",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "New thought-leadership thread"
    listing = client.get("/content/calendar", headers=_auth("operator")).json()
    assert any(e["title"] == "New thought-leadership thread" for e in listing["entries"])


# --------------------------------------------------------------------- brand voice
def test_brand_voice_suggest_is_advisory(client: TestClient) -> None:
    """POST /content/brand-voice/suggest → advisory suggestions + a brand score (heuristic)."""
    resp = client.post(
        "/content/brand-voice/suggest",
        headers=_auth("operator"),
        json={"text": "This is an amazing, guaranteed, world-class program — act now!"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory"] is True
    # Hype tokens are flagged as advisory rewrites (suggested, never applied).
    befores = {s["before"] for s in body["suggestions"]}
    assert {"amazing", "guaranteed", "world-class", "act now"} <= befores
    assert all(s["kind"] in {"hype", "never_rule"} for s in body["suggestions"])
    # A real conformance score in [0, 1]; hype-laden copy scores low.
    assert 0.0 <= body["brand_score"] <= 1.0


def test_brand_voice_clean_copy_scores_higher(client: TestClient) -> None:
    """On-brand GT copy scores above hype-laden copy and yields no hype suggestions."""
    clean = client.post(
        "/content/brand-voice/suggest",
        headers=_auth("operator"),
        json={
            "text": (
                "GT School is a mastery-based program for gifted learners. Parents can use "
                "TEFA funding to make tuition affordable for their family."
            )
        },
    ).json()
    hype = client.post(
        "/content/brand-voice/suggest",
        headers=_auth("operator"),
        json={"text": "Amazing, guaranteed, world-class — act now!"},
    ).json()
    assert clean["brand_score"] > hype["brand_score"]
    assert clean["suggestions"] == []

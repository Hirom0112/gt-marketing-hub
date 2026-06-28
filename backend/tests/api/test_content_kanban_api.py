"""Content kanban API (S-Sheets) — GET/POST over the SIMULATED adapter.

Hits the REAL main app with :func:`app.api.deps.get_sheets_adapter_dep` overridden to
a fresh seeded :class:`SimulatedSheetsAdapter` per test (no live Google call ever).
The autouse conftest principal shim supplies an admin principal on a no-auth request,
so these exercise the composition (read → group by stage → honest sync block; upsert →
write back) without a token dance.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.sheets.simulated import SimulatedSheetsAdapter
from app.api import deps
from app.api.content_kanban import router as kanban_router
from app.main import app

# The kanban router is intentionally NOT included in app.main yet (the integrator
# adds the include line). Mount it on the shared test app so these acceptance tests
# can exercise the real composition; idempotent — the module imports once.
if not any(getattr(r, "path", None) == "/content/kanban" for r in app.routes):
    app.include_router(kanban_router)

client = TestClient(app)


@pytest.fixture
def adapter() -> Iterator[SimulatedSheetsAdapter]:
    """A fresh seeded simulated adapter wired behind the kanban routes per test."""
    sim = SimulatedSheetsAdapter.seeded()
    app.dependency_overrides[deps.get_sheets_adapter_dep] = lambda: sim
    try:
        yield sim
    finally:
        app.dependency_overrides.pop(deps.get_sheets_adapter_dep, None)


def test_get_kanban_returns_grouped_rows_and_honest_sync(adapter: SimulatedSheetsAdapter) -> None:
    """GET returns the five stage columns, the flat rows, and an HONEST simulate label."""
    resp = client.get("/content/kanban")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["stages"] == ["Backlog", "Drafting", "Review", "Scheduled", "Live"]
    assert len(body["rows"]) == len(adapter.read_rows())
    # Columns are the five stages in order, and every card lands under a real stage.
    assert [c["stage"] for c in body["columns"]] == body["stages"]
    total_cards = sum(len(c["cards"]) for c in body["columns"])
    assert total_cards == len(body["rows"])

    # The label is HONEST: a simulated seam reports simulate / not synced (never a
    # false "SYNCED").
    assert body["sync"]["mode"] == "simulate"
    assert body["sync"]["synced"] is False
    assert body["sync"]["sheet_id"] is None


def test_post_move_updates_stage_in_place(adapter: SimulatedSheetsAdapter) -> None:
    """POST a same-title row with a new stage ⇒ the card MOVES, count unchanged."""
    rows = adapter.read_rows()
    n = len(rows)
    target = rows[0]

    resp = client.post(
        "/content/kanban",
        json={
            "title": target.title,
            "type": target.type,
            "stage": "Live",
            "owner": target.owner,
            "channel": target.channel,
            "utm": target.utm,
            "target_date": target.target_date,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row"]["stage"] == "Live"

    # A re-read shows the move with no new row.
    after = client.get("/content/kanban").json()
    assert len(after["rows"]) == n
    moved = next(r for r in after["rows"] if r["title"] == target.title)
    assert moved["stage"] == "Live"


def test_post_new_card_appends(adapter: SimulatedSheetsAdapter) -> None:
    """POST a new title ⇒ a fresh card is added to the board."""
    n = len(adapter.read_rows())
    resp = client.post(
        "/content/kanban",
        json={
            "title": "Net-new brief",
            "type": "article",
            "stage": "Backlog",
            "owner": "the Content Owner",
            "channel": "Substack",
        },
    )
    assert resp.status_code == 200, resp.text
    after = client.get("/content/kanban").json()
    assert len(after["rows"]) == n + 1
    assert any(r["title"] == "Net-new brief" for r in after["rows"])


def test_post_unknown_stage_is_422(adapter: SimulatedSheetsAdapter) -> None:
    """An unknown stage in the body is rejected (422 at the model edge, fail-closed)."""
    resp = client.post(
        "/content/kanban",
        json={
            "title": "bad",
            "type": "article",
            "stage": "Published",  # not one of the five canonical stages
            "owner": "o",
            "channel": "X",
        },
    )
    assert resp.status_code == 422

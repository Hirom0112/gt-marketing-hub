"""API contract tests for the drop-off telemetry endpoints (Task C; A-24).

Two read-only routes surface the step→form→field drop-off telemetry the SPA
writes into ``apply_events``:

* ``GET /families/{id}/drop-off`` — one family's last position before exit, or a
  204/empty when the family emitted no events (never a 500).
* ``GET /drop-off/heatmap`` — the aggregate cohort heatmap of exit cells.

Both must degrade cleanly when the active repository is the in-memory fallback
(no Supabase, the v1 default): the methods do not exist there, so the routes
return empty/none rather than erroring. These tests cover BOTH the in-memory
fallback (the booted app) and a stub repo that DOES expose the drop-off methods.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.data.supabase_repository import DropOffBucket, DropOffPoint
from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# In-memory fallback: the default booted app has no apply_events store, so the
# routes must degrade cleanly (empty/none), never 500.
# ---------------------------------------------------------------------------


def test_family_drop_off_degrades_to_none_on_in_memory_repo() -> None:
    """A repo without drop-off support ⇒ 204 (no content), never a 500."""
    first_id = client.get("/families").json()[0]["family_id"]
    resp = client.get(f"/families/{first_id}/drop-off")
    assert resp.status_code == 204


def test_heatmap_degrades_to_empty_on_in_memory_repo() -> None:
    """A repo without drop-off support ⇒ an empty bucket list, never a 500."""
    resp = client.get("/drop-off/heatmap")
    assert resp.status_code == 200
    assert resp.json() == {"buckets": []}


def test_family_drop_off_unknown_family_is_204() -> None:
    """An unknown family on the in-memory repo also degrades to 204 (no leak, no 500)."""
    resp = client.get(f"/families/{uuid4()}/drop-off")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Drop-off-capable repo: a stub exposing the two methods proves the response
# shape carries step/form/field through. Overrides the repository dependency.
# ---------------------------------------------------------------------------


class _StubDropOffRepo:
    """A minimal repo exposing only the drop-off methods the route consumes."""

    def __init__(self, point: DropOffPoint | None, buckets: list[DropOffBucket]) -> None:
        self._point = point
        self._buckets = buckets

    def drop_off_for_family(self, family_id: UUID) -> DropOffPoint | None:
        return self._point

    def drop_off_heatmap(self) -> list[DropOffBucket]:
        return self._buckets


def _override(repo: _StubDropOffRepo) -> None:
    app.dependency_overrides[deps.get_repository] = lambda: repo


def teardown_function() -> None:
    app.dependency_overrides.pop(deps.get_repository, None)


def test_family_drop_off_returns_step_form_field() -> None:
    fid = uuid4()
    point = DropOffPoint(
        family_id=fid,
        step="enroll",
        form_key="data_collection_consent",
        field_key="signature",
        event_type="last_step_before_exit",
        occurred_at="2026-06-02T09:00:00+00:00",
    )
    _override(_StubDropOffRepo(point, []))
    resp = client.get(f"/families/{fid}/drop-off")
    assert resp.status_code == 200
    assert resp.json() == {
        "family_id": str(fid),
        "step": "enroll",
        "form_key": "data_collection_consent",
        "field_key": "signature",
        "event_type": "last_step_before_exit",
        "occurred_at": "2026-06-02T09:00:00+00:00",
    }


def test_family_drop_off_none_is_204_even_when_supported() -> None:
    fid = uuid4()
    _override(_StubDropOffRepo(None, []))
    resp = client.get(f"/families/{fid}/drop-off")
    assert resp.status_code == 204


def test_heatmap_returns_buckets_with_form_key() -> None:
    buckets = [
        DropOffBucket(
            step="enroll", form_key="data_collection_consent", field_key="signature", count=2
        ),
        DropOffBucket(step="apply", form_key="consents", field_key=None, count=1),
    ]
    _override(_StubDropOffRepo(None, buckets))
    resp = client.get("/drop-off/heatmap")
    assert resp.status_code == 200
    assert resp.json() == {
        "buckets": [
            {
                "step": "enroll",
                "form_key": "data_collection_consent",
                "field_key": "signature",
                "count": 2,
            },
            {"step": "apply", "form_key": "consents", "field_key": None, "count": 1},
        ]
    }

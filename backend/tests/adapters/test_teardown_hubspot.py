"""S14 W4 — HubSpot teardown script (TDD red→green).

These are the §4.1 red tests for the operator-run teardown that DELETES every
synthetic HubSpot object this project created — i.e. every contact / deal / note
whose ``gt_synthetic_id`` custom property is set. It is the demo-cleanup sibling
of ``scripts/provision_hubspot.py``: it leaves the real portal clean.

Like the live-adapter tests, these run against a ``httpx.MockTransport`` — **no
real network, no live HubSpot call, ever**. A scripted ``_FakeHubSpot`` answers
the ``HAS_PROPERTY`` searches and records every request so the tests assert that:

- teardown DELETEs **exactly** the ids the gt_synthetic_id search returned, across
  all three object types (it can never delete a non-synthetic object — it only
  ever learns ids from the gt_synthetic_id search);
- a non-confirmed (dry-run) run issues **zero** DELETEs;
- the per-run cap fails closed (raises the budget error) rather than overspending
  the shared HubSpot quota (INV-8) — reusing the live adapter's budget exception;
- an empty portal (search returns nothing) is a clean no-op (0 deletes, no crash).

Tokens here are inert fake fragments so the PII-scan stays green (INV-1); no email
or real-PII literal appears.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.adapters.hubspot.live_adapter import HubSpotBudgetExceededError
from scripts.teardown_hubspot import (
    TEARDOWN_OBJECT_TYPES,
    TeardownSummary,
    teardown_synthetic_objects,
)

# A fake Bearer token, assembled from inert fragments so the literal does not
# match the PII-scan's HubSpot-token signature. Not a real secret.
_TOKEN = "pat" + "-" + "test" + "-" + "synthetic-fake-teardown-token"
_BASE_URL = "https://api.hubapi.com"


class _FakeHubSpot:
    """Records requests and answers the CRM v3 search + delete calls.

    Seeded with a fixed set of synthetic object ids per object type; the
    ``gt_synthetic_id HAS_PROPERTY`` search returns them (optionally paged), and a
    ``DELETE`` removes one from the store so a re-search would not return it.
    """

    def __init__(self, *, ids_by_type: dict[str, list[str]], page_size: int | None = None) -> None:
        self.requests: list[httpx.Request] = []
        # object_type -> list of object ids that carry gt_synthetic_id.
        self._ids: dict[str, list[str]] = {k: list(v) for k, v in ids_by_type.items()}
        self._page_size = page_size
        self.deleted: dict[str, list[str]] = {k: [] for k in ids_by_type}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        kind = self._object_kind(path)
        if path.endswith("/search"):
            body: dict[str, Any] = json.loads(request.content) if request.content else {}
            return self._search(kind, body)
        if request.method == "DELETE":
            obj_id = path.rstrip("/").split("/")[-1]
            self.deleted[kind].append(obj_id)
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    @staticmethod
    def _object_kind(path: str) -> str:
        if "contacts" in path:
            return "contacts"
        if "deals" in path:
            return "deals"
        return "notes"

    def _search(self, kind: str, body: dict[str, Any]) -> httpx.Response:
        # The teardown MUST search by gt_synthetic_id HAS_PROPERTY — assert shape.
        names = [
            f.get("propertyName")
            for g in body.get("filterGroups", [])
            for f in g.get("filters", [])
        ]
        operators = [
            f.get("operator") for g in body.get("filterGroups", []) for f in g.get("filters", [])
        ]
        assert names == ["gt_synthetic_id"], f"teardown must search gt_synthetic_id, got {names}"
        assert operators == ["HAS_PROPERTY"], f"must use HAS_PROPERTY, got {operators}"

        all_ids = self._ids.get(kind, [])
        after = body.get("after")
        start = int(after) if after is not None else 0
        page_size = self._page_size or len(all_ids) or 1
        page = all_ids[start : start + page_size]
        results = [{"id": obj_id, "properties": {"gt_synthetic_id": obj_id}} for obj_id in page]
        payload: dict[str, Any] = {"total": len(all_ids), "results": results}
        next_start = start + page_size
        if next_start < len(all_ids):
            payload["paging"] = {"next": {"after": str(next_start)}}
        return httpx.Response(200, json=payload)


def _client(fake: _FakeHubSpot) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake.handler), base_url=_BASE_URL)


def _seed(contacts: int = 2, deals: int = 2, notes: int = 1) -> dict[str, list[str]]:
    """Synthetic object ids per type (the only ids the search ever yields)."""
    return {
        "contacts": [f"contact-{i}" for i in range(contacts)],
        "deals": [f"deal-{i}" for i in range(deals)],
        "notes": [f"note-{i}" for i in range(notes)],
    }


# ===========================================================================
# Core contract — confirm path deletes exactly the searched ids
# ===========================================================================


def test_object_types_cover_contacts_deals_notes() -> None:
    """The teardown targets exactly contacts, deals, and notes (the synthetic set)."""
    assert set(TEARDOWN_OBJECT_TYPES) == {"contacts", "deals", "notes"}


def test_confirm_deletes_exactly_the_searched_ids() -> None:
    """teardown DELETEs exactly the ids the gt_synthetic_id search returned.

    Structural guarantee: it only ever learns ids from the gt_synthetic_id search,
    so it can never touch a non-synthetic object.
    """
    seeded = _seed(contacts=2, deals=3, notes=1)
    fake = _FakeHubSpot(ids_by_type=seeded)

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    for kind, ids in seeded.items():
        assert sorted(fake.deleted[kind]) == sorted(ids), kind
        deletes = [
            r for r in fake.requests if r.method == "DELETE" and f"/objects/{kind}/" in r.url.path
        ]
        assert len(deletes) == len(ids)
    assert isinstance(summary, TeardownSummary)
    assert summary.deleted == {"contacts": 2, "deals": 3, "notes": 1}
    assert summary.dry_run is False


def test_confirm_pages_through_all_results() -> None:
    """Paged search results are all collected and deleted (no truncation)."""
    seeded = {"contacts": [f"contact-{i}" for i in range(5)], "deals": [], "notes": []}
    fake = _FakeHubSpot(ids_by_type=seeded, page_size=2)

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    assert sorted(fake.deleted["contacts"]) == sorted(seeded["contacts"])
    assert summary.deleted["contacts"] == 5


# ===========================================================================
# Safety — dry-run / non-confirmed issues ZERO deletes
# ===========================================================================


def test_dry_run_issues_zero_deletes() -> None:
    """A non-confirmed (dry-run) teardown searches but DELETEs nothing."""
    seeded = _seed(contacts=2, deals=2, notes=2)
    fake = _FakeHubSpot(ids_by_type=seeded)

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=False)

    deletes = [r for r in fake.requests if r.method == "DELETE"]
    assert deletes == [], "dry-run must issue NO DELETE calls"
    # It still reports what WOULD be deleted (the searched counts).
    assert summary.deleted == {"contacts": 2, "deals": 2, "notes": 2}
    assert summary.dry_run is True


# ===========================================================================
# Budget — the per-run cap fails closed (INV-8), never overspends
# ===========================================================================


def test_cap_fails_closed_rather_than_overspending() -> None:
    """Exceeding the per-run HubSpot call budget raises (INV-8), never overspends.

    With a tiny cap, the search+delete sweep blows the budget; the budget error
    is the live adapter's own :class:`HubSpotBudgetExceededError` (reused, not
    duplicated).
    """
    seeded = _seed(contacts=3, deals=3, notes=3)
    fake = _FakeHubSpot(ids_by_type=seeded)

    with _client(fake) as client:
        with pytest.raises(HubSpotBudgetExceededError):
            teardown_synthetic_objects(client, cap=2, confirm=True)


def test_dry_run_also_respects_the_cap() -> None:
    """Even a dry-run's searches are budgeted — a tiny cap fails closed (INV-8)."""
    seeded = {"contacts": [f"contact-{i}" for i in range(10)], "deals": [], "notes": []}
    fake = _FakeHubSpot(ids_by_type=seeded, page_size=1)

    with _client(fake) as client:
        with pytest.raises(HubSpotBudgetExceededError):
            teardown_synthetic_objects(client, cap=2, confirm=False)


# ===========================================================================
# Empty portal — clean no-op
# ===========================================================================


def test_empty_portal_is_a_clean_no_op() -> None:
    """An empty portal (search returns nothing) deletes nothing and does not crash."""
    fake = _FakeHubSpot(ids_by_type={"contacts": [], "deals": [], "notes": []})

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    assert [r for r in fake.requests if r.method == "DELETE"] == []
    assert summary.deleted == {"contacts": 0, "deals": 0, "notes": 0}
    assert summary.total_deleted == 0

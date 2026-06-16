"""S14 W4 — HubSpot teardown script (TDD red→green).

These are the §4.1 tests for the operator-run teardown that DELETEs every
synthetic HubSpot object this project created. "This project's" objects are:

- **contacts / deals** — every contact/deal whose ``gt_synthetic_id`` custom
  property is set (the cockpit upsert key the live adapter writes); and
- **notes** — every Note the live adapter created and **associated** to one of
  those synthetic contacts/deals. Notes do NOT carry ``gt_synthetic_id`` (it is
  not a defined property on the HubSpot ``notes`` schema — a property-search on
  notes 400s live), so a note is found ONLY via its association to a
  confirmed-synthetic parent (A-26). That association is the structural INV-1
  handle: a real note on a real contact is never associated to a synthetic
  parent, so it is unreachable.

It is the demo-cleanup sibling of ``scripts/provision_hubspot.py``: it leaves the
real portal clean.

Like the live-adapter tests, these run against a ``httpx.MockTransport`` — **no
real network, no live HubSpot call, ever**. A scripted ``_FakeHubSpot`` answers
the ``HAS_PROPERTY`` searches, the CRM-v4 association reads, and the deletes,
recording every request so the tests assert that:

- teardown DELETEs **exactly** the contact/deal ids the gt_synthetic_id search
  returned, plus the note ids reached via those parents' associations — and
  nothing else (a non-synthetic object is structurally unreachable);
- associated notes are deleted **before** their parent, so the association handle
  is still live when we read it;
- a non-confirmed (dry-run) run issues **zero** DELETEs (notes included);
- a note NOT associated to any synthetic parent is never deleted;
- the per-run cap fails closed (raises the budget error) rather than overspending
  the shared HubSpot quota (INV-8) — reusing the live adapter's budget exception;
- an empty portal is a clean no-op (0 deletes, no crash).

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
    """Records requests and answers the CRM search + association-read + delete calls.

    Seeded with synthetic contact/deal ids (which the ``gt_synthetic_id
    HAS_PROPERTY`` search returns), and a map of parent ``{type}/{id}`` -> the
    note ids associated to it (which the CRM-v4 association read returns). A
    ``DELETE`` removes the id from its store so a re-search/re-read would not
    return it (idempotent).
    """

    def __init__(
        self,
        *,
        ids_by_type: dict[str, list[str]] | None = None,
        notes_by_parent: dict[str, list[str]] | None = None,
        page_size: int | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        # object_type -> list of object ids carrying gt_synthetic_id (contacts/deals).
        base = ids_by_type or {"contacts": [], "deals": []}
        self._ids: dict[str, list[str]] = {k: list(v) for k, v in base.items()}
        # "{parent_type}/{parent_id}" -> note ids associated to that parent.
        self._notes_by_parent: dict[str, list[str]] = {
            k: list(v) for k, v in (notes_by_parent or {}).items()
        }
        self._page_size = page_size
        self.deleted: dict[str, list[str]] = {"contacts": [], "deals": [], "notes": []}

    # ------------------------------------------------------------------ routing
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/search"):
            body: dict[str, Any] = json.loads(request.content) if request.content else {}
            return self._search(self._object_kind(path), body)
        if "/associations/" in path:
            return self._read_associations(path)
        if request.method == "DELETE":
            kind = self._object_kind(path)
            obj_id = path.rstrip("/").split("/")[-1]
            self.deleted[kind].append(obj_id)
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    @staticmethod
    def _object_kind(path: str) -> str:
        # The association-read tail decides the kind, so check it first (a contact's
        # ".../associations/notes" must read as a NOTE delete target, not a contact).
        tail = path.rstrip("/").split("/")[-1]
        if tail == "notes" or "/objects/notes/" in path:
            return "notes"
        if "contacts" in path:
            return "contacts"
        if "deals" in path:
            return "deals"
        return "notes"

    # ----------------------------------------------------------- contacts/deals
    def _search(self, kind: str, body: dict[str, Any]) -> httpx.Response:
        # Contacts/deals are found by gt_synthetic_id HAS_PROPERTY — assert shape.
        # NOTES are NEVER searched (no gt_synthetic_id on the notes schema, A-26).
        assert kind in ("contacts", "deals"), f"only contacts/deals are searched, got {kind}"
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

    # ----------------------------------------------------------------- notes
    def _read_associations(self, path: str) -> httpx.Response:
        # CRM v4 read: /crm/v4/objects/{parentType}/{parentId}/associations/notes
        # -> {"results": [{"toObjectId": <note id>}, ...]}.
        parts = path.strip("/").split("/")
        # .../objects/{parentType}/{parentId}/associations/{toType}
        to_type = parts[-1]
        parent_id = parts[-3]
        parent_type = parts[-4]
        assert to_type == "notes", f"teardown only reads note associations, got {to_type}"
        key = f"{parent_type}/{parent_id}"
        note_ids = [n for n in self._notes_by_parent.get(key, []) if n not in self.deleted["notes"]]
        results = [{"toObjectId": nid} for nid in note_ids]
        return httpx.Response(200, json={"results": results})


def _client(fake: _FakeHubSpot) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake.handler), base_url=_BASE_URL)


def _seed(contacts: int = 2, deals: int = 2) -> dict[str, list[str]]:
    """Synthetic contact/deal ids per type (the only parents the search yields)."""
    return {
        "contacts": [f"contact-{i}" for i in range(contacts)],
        "deals": [f"deal-{i}" for i in range(deals)],
    }


# ===========================================================================
# Object-type coverage
# ===========================================================================


def test_object_types_cover_contacts_and_deals() -> None:
    """The top-level swept types are contacts and deals (notes ride associations).

    Notes are NOT a top-level swept type: ``gt_synthetic_id`` is not a defined
    property on the HubSpot notes schema, so a property-search on notes 400s
    (A-26). Notes are torn down via their association to a synthetic parent.
    """
    assert set(TEARDOWN_OBJECT_TYPES) == {"contacts", "deals"}


# ===========================================================================
# Core contract — confirm path deletes exactly the searched parents + their notes
# ===========================================================================


def test_confirm_deletes_searched_parents_and_their_associated_notes() -> None:
    """teardown DELETEs the gt_synthetic_id parents AND their associated notes.

    The note is reached ONLY via the parent's association — proving the new path
    exists (the OLD code, which property-searched notes, never deletes them).
    """
    seeded = _seed(contacts=2, deals=1)
    notes_by_parent = {
        "contacts/contact-0": ["note-a"],
        "deals/deal-0": ["note-b"],
    }
    fake = _FakeHubSpot(ids_by_type=seeded, notes_by_parent=notes_by_parent)

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    # Parents deleted exactly as searched.
    assert sorted(fake.deleted["contacts"]) == ["contact-0", "contact-1"]
    assert sorted(fake.deleted["deals"]) == ["deal-0"]
    # The associated notes were deleted via their parent association.
    assert sorted(fake.deleted["notes"]) == ["note-a", "note-b"]
    note_deletes = [
        r for r in fake.requests if r.method == "DELETE" and "/objects/notes/" in r.url.path
    ]
    assert {r.url.path.rstrip("/").split("/")[-1] for r in note_deletes} == {"note-a", "note-b"}

    assert isinstance(summary, TeardownSummary)
    assert summary.deleted == {"contacts": 2, "deals": 1, "notes": 2}
    assert summary.dry_run is False


def test_associated_note_deleted_before_its_parent() -> None:
    """A note is DELETEd before its parent — the association handle stays live.

    Deleting the parent first would drop the association we use to find the note.
    """
    fake = _FakeHubSpot(
        ids_by_type={"contacts": ["contact-0"], "deals": []},
        notes_by_parent={"contacts/contact-0": ["note-a"]},
    )

    with _client(fake) as client:
        teardown_synthetic_objects(client, cap=200, confirm=True)

    deletes = [r for r in fake.requests if r.method == "DELETE" and "/objects/" in r.url.path]
    order = [r.url.path.rstrip("/").split("/")[-1] for r in deletes]
    assert order.index("note-a") < order.index("contact-0")


def test_note_on_two_parents_is_deleted_once() -> None:
    """A note associated to BOTH a contact and a deal is DELETEd exactly once.

    The live adapter associates a note to both the contact and the deal; the
    teardown must dedup so the same note id is not DELETEd twice (which would
    202/204 then 404 on the live portal).
    """
    fake = _FakeHubSpot(
        ids_by_type={"contacts": ["contact-0"], "deals": ["deal-0"]},
        notes_by_parent={
            "contacts/contact-0": ["note-shared"],
            "deals/deal-0": ["note-shared"],
        },
    )

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    assert fake.deleted["notes"] == ["note-shared"]
    note_deletes = [
        r for r in fake.requests if r.method == "DELETE" and "/objects/notes/" in r.url.path
    ]
    assert len(note_deletes) == 1, "the shared note must be deleted exactly once"
    assert summary.deleted["notes"] == 1


def test_confirm_pages_through_all_parent_results() -> None:
    """Paged contact/deal search results are all collected and deleted (no truncation)."""
    seeded = {"contacts": [f"contact-{i}" for i in range(5)], "deals": []}
    fake = _FakeHubSpot(ids_by_type=seeded, page_size=2)

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    assert sorted(fake.deleted["contacts"]) == sorted(seeded["contacts"])
    assert summary.deleted["contacts"] == 5


# ===========================================================================
# Safety — a note NOT associated to a synthetic parent is unreachable
# ===========================================================================


def test_note_not_associated_to_a_synthetic_parent_is_never_deleted() -> None:
    """A note hanging off NO synthetic parent is structurally unreachable (INV-1).

    "real-note" is not in any synthetic parent's association set, so the teardown
    never learns its id and never DELETEs it.
    """
    fake = _FakeHubSpot(
        ids_by_type={"contacts": ["contact-0"], "deals": []},
        notes_by_parent={"contacts/contact-0": ["note-synthetic"]},
    )

    with _client(fake) as client:
        teardown_synthetic_objects(client, cap=200, confirm=True)

    assert "real-note" not in fake.deleted["notes"]
    assert fake.deleted["notes"] == ["note-synthetic"]


# ===========================================================================
# Safety — dry-run / non-confirmed issues ZERO deletes (notes included)
# ===========================================================================


def test_dry_run_issues_zero_deletes_and_lists_notes() -> None:
    """A non-confirmed (dry-run) teardown reads associations but DELETEs nothing."""
    fake = _FakeHubSpot(
        ids_by_type=_seed(contacts=2, deals=2),
        notes_by_parent={"contacts/contact-0": ["note-a"], "deals/deal-0": ["note-b"]},
    )

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=False)

    deletes = [r for r in fake.requests if r.method == "DELETE"]
    assert deletes == [], "dry-run must issue NO DELETE calls"
    # It still reports what WOULD be deleted — parents AND the associated notes.
    assert summary.deleted == {"contacts": 2, "deals": 2, "notes": 2}
    assert summary.dry_run is True


# ===========================================================================
# Budget — the per-run cap fails closed (INV-8), never overspends
# ===========================================================================


def test_cap_fails_closed_rather_than_overspending() -> None:
    """Exceeding the per-run HubSpot call budget raises (INV-8), never overspends.

    The search + association-read + delete sweep blows a tiny cap; the budget
    error is the live adapter's own :class:`HubSpotBudgetExceededError` (reused).
    """
    fake = _FakeHubSpot(
        ids_by_type=_seed(contacts=3, deals=3),
        notes_by_parent={"contacts/contact-0": ["note-a"]},
    )

    with _client(fake) as client:
        with pytest.raises(HubSpotBudgetExceededError):
            teardown_synthetic_objects(client, cap=2, confirm=True)


def test_association_reads_and_note_deletes_count_against_the_cap() -> None:
    """The new association lookups + note deletes are budgeted (INV-8).

    With one synthetic contact carrying one associated note and no deals, a
    dry-run still spends: contact search (1) + deal search (1) + the contact's
    association read (1). A cap of 2 fails closed on that association read,
    proving the new calls ride the same budget.
    """
    fake = _FakeHubSpot(
        ids_by_type={"contacts": ["contact-0"], "deals": []},
        notes_by_parent={"contacts/contact-0": ["note-a"]},
    )

    with _client(fake) as client:
        with pytest.raises(HubSpotBudgetExceededError):
            teardown_synthetic_objects(client, cap=2, confirm=False)


# ===========================================================================
# Empty portal — clean no-op
# ===========================================================================


def test_empty_portal_is_a_clean_no_op() -> None:
    """An empty portal (search returns nothing) deletes nothing and does not crash."""
    fake = _FakeHubSpot(ids_by_type={"contacts": [], "deals": []})

    with _client(fake) as client:
        summary = teardown_synthetic_objects(client, cap=200, confirm=True)

    assert [r for r in fake.requests if r.method == "DELETE"] == []
    assert summary.deleted == {"contacts": 0, "deals": 0, "notes": 0}
    assert summary.total_deleted == 0

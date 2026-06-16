"""HubSpot synthetic-object teardown (S14 W4) — an OPS tool, NOT a test.

The demo-cleanup sibling of ``scripts/provision_hubspot.py``: it leaves the real
portal clean after a demo by DELETING every HubSpot object this project ever
created — and ONLY those. "This project's" objects are:

- **contacts / deals** — exactly the ones carrying the ``gt_synthetic_id`` custom
  property (the cockpit upsert key the live adapter sets on every contact/deal it
  writes, NEVER email — INV-1 guard 1). A real contact/deal never carries
  ``gt_synthetic_id``, so the search can only return synthetic objects.
- **notes** — every Note the live adapter created and **associated** to one of
  those synthetic contacts/deals. Notes do NOT carry ``gt_synthetic_id``: it is
  not a defined property on the HubSpot ``notes`` schema, so a property-search on
  notes 400s on the live portal (verified live; A-26). A note is therefore found
  ONLY via its association to a confirmed-synthetic parent — the same association
  the live adapter writes (``send_message``). That association is the structural
  INV-1 handle: a real note on a real contact is never associated to a synthetic
  parent, so it is unreachable.

So this script can only ever delete synthetic data:

- it searches **contacts and deals** by ``gt_synthetic_id HAS_PROPERTY`` (the
  HubSpot operator that matches "property is set"), paging through all results;
- for each synthetic parent it reads its associated notes (CRM v4
  ``GET /crm/v4/objects/{type}/{id}/associations/notes``), DELETEs those notes
  FIRST (so the association handle is still live), then DELETEs the parent;
- it only ever learns a contact/deal id from the search and a note id from a
  synthetic parent's association, so a non-synthetic object is structurally
  unreachable.

Safety posture:

- **Dry-run by default.** Without ``--confirm`` it lists what WOULD be deleted and
  issues ZERO DELETEs. Deletion is irreversible against a live portal, so the
  destructive path is opt-in (``--confirm``), never the default.
- **Budgeted (INV-8).** Every HubSpot call — search AND delete — goes through one
  budgeted ``_request`` that fails closed at the per-run cap by raising the live
  adapter's :class:`HubSpotBudgetExceededError` (reused, not duplicated), rather
  than overspending the account-shared quota. The cap comes from
  ``HUBSPOT_CALLS_PER_RUN_CAP`` via the settings seam (INV-11).

Like the provisioning script it reads the full-scope Private App token from the
repo-root ``.env`` and runs by hand; pytest never imports the live path (the tests
inject a ``httpx.MockTransport`` client and call :func:`teardown_synthetic_objects`
directly — no real network).

Usage:
    cd backend && .venv/bin/python scripts/teardown_hubspot.py            # dry-run
    cd backend && .venv/bin/python scripts/teardown_hubspot.py --confirm  # delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

# Reuse the live adapter's constants/exception — do NOT duplicate them (INV-11):
# the gt_synthetic_id property name and the budget error are owned there.
from app.adapters.hubspot.live_adapter import (
    _GT_SYNTHETIC_ID,
    HubSpotBudgetExceededError,
)
from app.core.settings import get_settings

# Repo layout: this file is backend/scripts/teardown_hubspot.py (mirrors the
# provisioning script's path resolution).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env"

# The fixed HubSpot CRM v3 API surface — the third party's own URLs, NOT tunables
# (INV-11 does not apply to a third party's fixed routes; the live adapter
# documents this exception). The object-type tail of ``/crm/v3/objects/{type}`` is
# the only thing that varies per sweep.
_API_BASE = "https://api.hubapi.com"
_OBJECTS_ROOT = "/crm/v3/objects"
# CRM v4 is the surface for reading associations (mirrors the live adapter's
# v4-default write side in ``live_adapter._associate``; the v3 association reads
# return a different shape and the v3 write 404s without a type id).
_OBJECTS_V4_ROOT = "/crm/v4/objects"
# The HubSpot search operator meaning "this property is set" — the structural
# filter that scopes the teardown to objects this project created.
_HAS_PROPERTY = "HAS_PROPERTY"
# The notes object type — torn down via association to a synthetic parent, never
# a property search (``gt_synthetic_id`` is not a notes-schema property; A-26).
_NOTES = "notes"

# The parent object types this project writes and searches by gt_synthetic_id
# (contacts, deals). Notes are NOT a top-level swept type — they are reached via
# these parents' associations (A-26). Exposed for the test to assert coverage.
TEARDOWN_OBJECT_TYPES: tuple[str, ...] = ("contacts", "deals")


class TeardownSummary(BaseModel):
    """The per-object-type result of one teardown sweep (printed as the summary)."""

    model_config = ConfigDict(frozen=True)

    # object_type -> count deleted (confirm) or that WOULD be deleted (dry-run).
    deleted: dict[str, int]
    dry_run: bool

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted.values())


class _BudgetedClient:
    """One budgeted HubSpot caller — guard 3 (INV-8) trips on the (cap+1)th call.

    Mirrors the live adapter's ``_request``: the budget is checked BEFORE the call,
    so an exhausted budget never reaches the network (fail closed), and a non-2xx
    response raises via ``raise_for_status``. The error is the live adapter's own
    :class:`HubSpotBudgetExceededError`, reused rather than redefined.
    """

    def __init__(self, client: httpx.Client, *, cap: int) -> None:
        self._client = client
        self._cap = cap
        self._calls_made = 0

    def request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        if self._calls_made >= self._cap:
            raise HubSpotBudgetExceededError(
                f"HubSpot per-run call budget exhausted ({self._cap}); stop the "
                f"teardown rather than overspend the shared quota (INV-8)."
            )
        self._calls_made += 1
        response = self._client.request(method, path, json=json)
        response.raise_for_status()
        return response


def _associated_note_ids(budgeted: _BudgetedClient, parent_type: str, parent_id: str) -> list[str]:
    """Read the note ids associated to one synthetic ``parent_type``/``parent_id``.

    Uses the CRM v4 read-associations endpoint
    (``GET /crm/v4/objects/{parent_type}/{parent_id}/associations/notes``), the read
    sibling of the live adapter's v4-default write association. The response shape is
    ``{"results": [{"toObjectId": <note id>}, ...], "paging": {...}}``; we page
    through it. A note id is learned ONLY from a confirmed-synthetic parent's
    association, so a non-synthetic note is structurally unreachable (INV-1, A-26).
    """
    path = f"{_OBJECTS_V4_ROOT}/{parent_type}/{parent_id}/associations/{_NOTES}"
    note_ids: list[str] = []
    after: str | None = None
    while True:
        request_path = path if after is None else f"{path}?after={after}"
        body = budgeted.request("GET", request_path).json()
        note_ids.extend(str(result["toObjectId"]) for result in body.get("results") or [])
        after = (body.get("paging") or {}).get("next", {}).get("after")
        if not after:
            return note_ids


def _search_synthetic_ids(budgeted: _BudgetedClient, object_type: str) -> list[str]:
    """Page through every object of ``object_type`` carrying ``gt_synthetic_id``.

    Filters on ``gt_synthetic_id HAS_PROPERTY`` ONLY — never email — so the result
    is exactly the synthetic set this project created. Returns the matched ids.
    """
    object_path = f"{_OBJECTS_ROOT}/{object_type}"
    ids: list[str] = []
    after: str | None = None
    while True:
        payload: dict[str, Any] = {
            "filterGroups": [
                {"filters": [{"propertyName": _GT_SYNTHETIC_ID, "operator": _HAS_PROPERTY}]}
            ],
            "properties": [_GT_SYNTHETIC_ID],
        }
        if after is not None:
            payload["after"] = after
        body = budgeted.request("POST", f"{object_path}/search", json=payload).json()
        ids.extend(str(result["id"]) for result in body.get("results") or [])
        after = (body.get("paging") or {}).get("next", {}).get("after")
        if not after:
            return ids


def _delete_object(
    budgeted: _BudgetedClient, object_type: str, obj_id: str, *, confirm: bool
) -> None:
    """DELETE (confirm) or log (dry-run) one ``object_type``/``obj_id``.

    A DELETE only ever targets an id learned from the gt_synthetic_id search (for
    contacts/deals) or from a synthetic parent's note association (for notes), so
    a non-synthetic object is structurally unreachable.
    """
    object_path = f"{_OBJECTS_ROOT}/{object_type}"
    if confirm:
        budgeted.request("DELETE", f"{object_path}/{obj_id}")
        print(f"  ✓ deleted {object_type}/{obj_id}")
    else:
        print(f"  ~ [dry-run] would DELETE {object_type}/{obj_id}")


def _teardown_parents_and_notes(
    budgeted: _BudgetedClient,
    parent_type: str,
    *,
    confirm: bool,
    deleted_note_ids: set[str],
) -> int:
    """Tear down every synthetic ``parent_type`` (contact/deal) and its notes.

    For each parent found by ``gt_synthetic_id HAS_PROPERTY``: read its associated
    notes, DELETE those notes FIRST (so the association handle is still live when we
    read it — deleting the parent first would drop it), then DELETE the parent.

    Note ids are deduped via the shared ``deleted_note_ids`` set: the live adapter
    associates one note to BOTH the contact and the deal, so the same note surfaces
    under two parents — we DELETE it exactly once (a second DELETE would 404 on a
    live portal). Returns the count of PARENTS deleted of this type; the note count
    is tracked by the caller via the set.
    """
    parent_ids = _search_synthetic_ids(budgeted, parent_type)
    for parent_id in parent_ids:
        for note_id in _associated_note_ids(budgeted, parent_type, parent_id):
            if note_id in deleted_note_ids:
                continue  # already removed via the other parent (dedup).
            deleted_note_ids.add(note_id)
            _delete_object(budgeted, _NOTES, note_id, confirm=confirm)
        _delete_object(budgeted, parent_type, parent_id, confirm=confirm)
    return len(parent_ids)


def teardown_synthetic_objects(client: httpx.Client, *, cap: int, confirm: bool) -> TeardownSummary:
    """Tear down every synthetic object (contacts, deals, notes) in the portal.

    Contacts/deals are found by the ``gt_synthetic_id`` search; notes are found via
    each synthetic parent's association (A-26) and DELETEd before their parent.

    Args:
        client: An ``httpx.Client`` with ``base_url`` set to the HubSpot API host
            and Bearer auth on its headers (tests inject a ``MockTransport`` one).
        cap: The per-run HubSpot call budget — the (cap+1)th call fails closed
            (INV-8). Sourced from ``HUBSPOT_CALLS_PER_RUN_CAP`` by :func:`main`.
            Every call — search, association read, AND delete — is budgeted.
        confirm: When ``False`` (the default posture) the sweep DELETEs nothing —
            it only lists what would be removed.

    Returns:
        A :class:`TeardownSummary` with the per-type counts (deleted, or — in
        dry-run — that would be deleted), including the deduped note count.
    """
    budgeted = _BudgetedClient(client, cap=cap)
    deleted: dict[str, int] = {}
    deleted_note_ids: set[str] = set()
    for parent_type in TEARDOWN_OBJECT_TYPES:
        verb = "Deleting" if confirm else "[dry-run] Listing"
        print(
            f"\n[{parent_type}] {verb} synthetic objects (gt_synthetic_id HAS_PROPERTY) "
            f"+ their associated notes"
        )
        deleted[parent_type] = _teardown_parents_and_notes(
            budgeted, parent_type, confirm=confirm, deleted_note_ids=deleted_note_ids
        )
    deleted[_NOTES] = len(deleted_note_ids)
    return TeardownSummary(deleted=deleted, dry_run=not confirm)


def _load_token() -> str:
    """Read HUBSPOT_PRIVATE_APP_TOKEN from the repo-root .env (no dotenv dep).

    Mirrors ``scripts/provision_hubspot.py._load_token`` so the two ops tools read
    the token identically.
    """
    if not _ENV_PATH.exists():
        sys.exit(f"ERROR: {_ENV_PATH} not found — cannot read HUBSPOT_PRIVATE_APP_TOKEN.")
    for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("HUBSPOT_PRIVATE_APP_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if not token or token.startswith("<"):
                sys.exit("ERROR: HUBSPOT_PRIVATE_APP_TOKEN is a placeholder/empty.")
            return token
    sys.exit("ERROR: HUBSPOT_PRIVATE_APP_TOKEN not present in .env.")


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=_API_BASE,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete every synthetic (gt_synthetic_id) HubSpot object (S14 W4)."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually DELETE. Without this flag the run is a dry-run (no deletes).",
    )
    args = parser.parse_args()

    # The per-run cap is a tunable home (INV-11) — read it through the settings
    # seam, not a literal. The kill switch, if set, also forces a dry-run: a live
    # teardown is itself a metered HubSpot write path, so it honors the same
    # INV-8 brake the registry uses.
    settings = get_settings()
    cap = settings.hubspot_calls_per_run_cap
    confirm = args.confirm and not settings.hubspot_kill_switch
    if args.confirm and settings.hubspot_kill_switch:
        print("HUBSPOT_KILL_SWITCH is set — forcing dry-run (no deletes; INV-8).")

    token = _load_token()
    print(f"=== HubSpot teardown (confirm={confirm}, cap={cap}) ===")

    with _client(token) as client:
        summary = teardown_synthetic_objects(client, cap=cap, confirm=confirm)

    print("\n=== Summary ===")
    mode = "deleted" if summary.dry_run is False else "would delete (dry-run)"
    # Parents (contacts, deals) plus notes (reached via association, A-26).
    for object_type in (*TEARDOWN_OBJECT_TYPES, _NOTES):
        print(f"  {object_type:<10} {mode}: {summary.deleted.get(object_type, 0)}")
    print(f"  {'total':<10} {mode}: {summary.total_deleted}")
    if summary.dry_run:
        print("Dry-run — re-run with --confirm to actually delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

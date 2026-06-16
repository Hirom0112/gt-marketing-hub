"""HubSpot synthetic-object teardown (S14 W4) — an OPS tool, NOT a test.

The demo-cleanup sibling of ``scripts/provision_hubspot.py``: it leaves the real
portal clean after a demo by DELETING every HubSpot object this project ever
created — and ONLY those. "This project's" objects are exactly the ones carrying
the ``gt_synthetic_id`` custom property (the cockpit upsert key; the live adapter
sets it on every contact/deal/note it writes, NEVER email — INV-1 guard 1). A real
contact/deal/note never carries ``gt_synthetic_id``, so this script can only ever
delete synthetic data:

- it searches each object type by ``gt_synthetic_id HAS_PROPERTY`` (the HubSpot
  operator that matches "property is set"), paging through all results;
- it DELETEs each returned id by ``DELETE /crm/v3/objects/{type}/{id}``;
- it only ever learns an id from that search, so a non-synthetic object is
  structurally unreachable.

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
# The HubSpot search operator meaning "this property is set" — the structural
# filter that scopes the teardown to objects this project created.
_HAS_PROPERTY = "HAS_PROPERTY"

# The object types this project writes (and therefore this script tears down):
# contacts, deals, and notes — every type the live adapter upserts. Exposed for
# the test to assert the full synthetic set is covered.
TEARDOWN_OBJECT_TYPES: tuple[str, ...] = ("contacts", "deals", "notes")


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


def _teardown_object_type(budgeted: _BudgetedClient, object_type: str, *, confirm: bool) -> int:
    """Search + (optionally) DELETE every synthetic object of one type.

    Returns the count of objects deleted (confirm) or that WOULD be deleted
    (dry-run). A DELETE only ever targets an id returned by the gt_synthetic_id
    search, so a non-synthetic object is structurally unreachable.
    """
    object_path = f"{_OBJECTS_ROOT}/{object_type}"
    ids = _search_synthetic_ids(budgeted, object_type)
    for obj_id in ids:
        if confirm:
            budgeted.request("DELETE", f"{object_path}/{obj_id}")
            print(f"  ✓ deleted {object_type}/{obj_id}")
        else:
            print(f"  ~ [dry-run] would DELETE {object_type}/{obj_id}")
    return len(ids)


def teardown_synthetic_objects(client: httpx.Client, *, cap: int, confirm: bool) -> TeardownSummary:
    """Tear down every synthetic object (contacts, deals, notes) in the portal.

    Args:
        client: An ``httpx.Client`` with ``base_url`` set to the HubSpot API host
            and Bearer auth on its headers (tests inject a ``MockTransport`` one).
        cap: The per-run HubSpot call budget — the (cap+1)th call fails closed
            (INV-8). Sourced from ``HUBSPOT_CALLS_PER_RUN_CAP`` by :func:`main`.
        confirm: When ``False`` (the default posture) the sweep DELETEs nothing —
            it only lists what would be removed.

    Returns:
        A :class:`TeardownSummary` with the per-type counts (deleted, or — in
        dry-run — that would be deleted).
    """
    budgeted = _BudgetedClient(client, cap=cap)
    deleted: dict[str, int] = {}
    for object_type in TEARDOWN_OBJECT_TYPES:
        verb = "Deleting" if confirm else "[dry-run] Listing"
        print(f"\n[{object_type}] {verb} synthetic objects (gt_synthetic_id HAS_PROPERTY)")
        deleted[object_type] = _teardown_object_type(budgeted, object_type, confirm=confirm)
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
    for object_type in TEARDOWN_OBJECT_TYPES:
        print(f"  {object_type:<10} {mode}: {summary.deleted[object_type]}")
    print(f"  {'total':<10} {mode}: {summary.total_deleted}")
    if summary.dry_run:
        print("Dry-run — re-run with --confirm to actually delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Idempotent HubSpot portal provisioning (S10 W1) — an OPS tool, NOT a test.

Runs against the LIVE portal (default `246504420`) using the full-scope Private
App token from the repo-root `.env` (`HUBSPOT_PRIVATE_APP_TOKEN`). It is a
migration-style tool: safe to re-run, prints a clear summary, and **creates
schema only** — it NEVER writes a contact/deal record, so it cannot write PII
(INV-1). Run it by hand; pytest never imports it.

What it does (ANALYSIS/hubspot-complement-plan.md §4/§6):

1. **Reshape the single deal pipeline** (`default`) to the four cockpit stages
   (interest → apply → enroll → tuition) by PATCHing the *labels* of four of the
   six existing active stage ids (ascending displayOrder + probabilities) and
   leaving Closed Lost. Relabel-in-place keeps the round-trip lossless and is
   idempotent (re-running re-asserts the same labels). The two now-unused active
   stage ids are left intact (no destructive deletes).
2. **Create custom properties** idempotently (skip if present): on `deals`
   gt_synthetic_id / gt_funding_state / gt_stall_reason / gt_priority /
   gt_forms_signed / gt_apply_date; on `contacts` gt_synthetic_id. Reuses the
   portal's existing `funding_source` / `program_type` (and `grade_level` where
   present) — see notes inline — rather than duplicating them.
3. **Read the resulting stage ids back** and rewrite `crm.stage_map` in the
   local `params/params.yaml` (gitignored; the live-truth copy). The committed
   `params.example.yaml` carries the same shape as a template (INV-11).

Usage:
    cd backend && .venv/bin/python scripts/provision_hubspot.py
    cd backend && .venv/bin/python scripts/provision_hubspot.py --dry-run

This token is a full-access scan/demo god-token (fine for the audit + demo). The
production adapter ships on a separate minimal-scope app (INV-5; plan §9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

# Repo layout: this file is backend/scripts/provision_hubspot.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env"
_PARAMS_PATH = _REPO_ROOT / "params" / "params.yaml"

_API_BASE = "https://api.hubapi.com"
_DEFAULT_PORTAL = "246504420"
_DEAL_PIPELINE_ID = "default"

# The four cockpit funnel stages, in funnel order, with the HubSpot label +
# display order + win probability to assert on the relabelled stages. Stage ids
# are NOT hardcoded here — they are read live from the pipeline (the first four
# active, ascending displayOrder) so this is portable to any pipeline.
_COCKPIT_STAGES: list[tuple[str, str, float]] = [
    # (cockpit Stage value, HubSpot label, probability)
    ("interest", "Interest", 0.1),
    ("apply", "Apply", 0.3),
    ("enroll", "Enroll", 0.6),
    ("tuition", "Tuition", 0.9),
]

# Custom properties to create per object (idempotent — skip if name exists).
# Group must be an existing property group on the object; "dealinformation" /
# "contactinformation" are HubSpot defaults present on every portal.
_DEAL_PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "gt_synthetic_id",
        "label": "GT Synthetic ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
        "description": "Cockpit synthetic family id — the idempotency/upsert key (NEVER email; INV-1 guard 1).",
    },
    {
        "name": "gt_funding_state",
        "label": "GT Funding State",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
        "description": "Cockpit FundingState mirror (none→applied→…→funded). Installment math never crosses.",
    },
    {
        "name": "gt_stall_reason",
        "label": "GT Stall Reason",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
        "description": "Cockpit deterministic StallReason label (for HubSpot-side filtering).",
    },
    {
        "name": "gt_priority",
        "label": "GT Priority",
        "type": "number",
        "fieldType": "number",
        "groupName": "dealinformation",
        "description": "Cockpit work-queue score (params-derived). Renders a sorted Deal view in HubSpot.",
    },
    {
        "name": "gt_forms_signed",
        "label": "GT Forms Signed",
        "type": "number",
        "fieldType": "number",
        "groupName": "dealinformation",
        "description": "Six-form gauntlet progress (0–6). The gauntlet itself is Enterprise-walled, so only this flat count crosses.",
    },
    {
        "name": "gt_apply_date",
        "label": "GT Apply Date",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "dealinformation",
        "description": "Application date (datetime).",
    },
]
# NOTE on reuse (plan §4): the portal already has `amount` (TEFA award mirror),
# `funding_source`, and `program_type` on deals — the adapter reuses those
# instead of creating gt_ duplicates. `grade_level` lives on contacts where it
# fits; we do not duplicate it. Only the gt_* props above are net-new.
_CONTACT_PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "gt_synthetic_id",
        "label": "GT Synthetic ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Cockpit synthetic family/contact id — upsert key (NEVER email; INV-1 guard 1).",
    },
]


def _load_token() -> str:
    """Read HUBSPOT_PRIVATE_APP_TOKEN from the repo-root .env (no dotenv dep)."""
    if not _ENV_PATH.exists():
        sys.exit(f"ERROR: {_ENV_PATH} not found — cannot read HUBSPOT_PRIVATE_APP_TOKEN.")
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("HUBSPOT_PRIVATE_APP_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if not token or token.startswith("<"):
                sys.exit("ERROR: HUBSPOT_PRIVATE_APP_TOKEN is a placeholder/empty.")
            return token
    sys.exit("ERROR: HUBSPOT_PRIVATE_APP_TOKEN not present in .env.")


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=_API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


def _get_deal_pipeline(client: httpx.Client) -> dict[str, Any]:
    resp = client.get(f"/crm/v3/pipelines/deals/{_DEAL_PIPELINE_ID}")
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    return payload


def reshape_pipeline(client: httpx.Client, *, dry_run: bool) -> dict[str, str]:
    """Relabel the first four active stage ids to the cockpit stages (idempotent).

    Returns the cockpit-value → HubSpot-stage-id map (incl. closed_lost) read
    back from the live pipeline after the PATCH.
    """
    pipeline = _get_deal_pipeline(client)
    stages = sorted(pipeline["stages"], key=lambda s: s.get("displayOrder", 0))

    def _is_closed(stage: dict[str, Any]) -> bool:
        # HubSpot returns isClosed as the *string* "true"/"false".
        return str(stage.get("metadata", {}).get("isClosed", "")).lower() == "true"

    active = [s for s in stages if not _is_closed(s)]
    closed = [s for s in stages if _is_closed(s)]

    if len(active) < len(_COCKPIT_STAGES):
        sys.exit(
            f"ERROR: pipeline has {len(active)} active stages; "
            f"need at least {len(_COCKPIT_STAGES)} to relabel."
        )

    stage_map: dict[str, str] = {}
    for order, ((value, label, prob), stage) in enumerate(zip(_COCKPIT_STAGES, active)):
        stage_id = stage["id"]
        stage_map[value] = stage_id
        current_label = stage.get("label")
        if current_label == label and stage.get("displayOrder") == order:
            print(f"  · stage {stage_id} already '{label}' (order {order}) — skip")
            continue
        body = {
            "label": label,
            "displayOrder": order,
            "metadata": {"probability": str(prob)},
        }
        if dry_run:
            print(f"  ~ [dry-run] PATCH stage {stage_id}: '{current_label}' → '{label}'")
            continue
        resp = client.patch(
            f"/crm/v3/pipelines/deals/{_DEAL_PIPELINE_ID}/stages/{stage_id}",
            json=body,
        )
        resp.raise_for_status()
        print(f"  ✓ relabelled stage {stage_id}: '{current_label}' → '{label}'")

    # Closed Lost — kept, mapped for the adapter (not a cockpit funnel Stage).
    closed_lost = next((s for s in closed if s["id"] == "closedlost"), closed[0] if closed else None)
    if closed_lost is not None:
        stage_map["closed_lost"] = closed_lost["id"]
        print(f"  · kept terminal stage {closed_lost['id']} ('{closed_lost.get('label')}')")

    return stage_map


def _existing_property_names(client: httpx.Client, object_type: str) -> set[str]:
    resp = client.get(f"/crm/v3/properties/{object_type}")
    resp.raise_for_status()
    return {p["name"] for p in resp.json().get("results", [])}


def create_properties(
    client: httpx.Client,
    object_type: str,
    properties: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> list[str]:
    """Create each property if absent (idempotent). Returns the names ensured."""
    existing = _existing_property_names(client, object_type)
    ensured: list[str] = []
    for prop in properties:
        name = prop["name"]
        ensured.append(name)
        if name in existing:
            print(f"  · {object_type}.{name} already exists — skip")
            continue
        if dry_run:
            print(f"  ~ [dry-run] CREATE {object_type}.{name} ({prop['type']})")
            continue
        resp = client.post(f"/crm/v3/properties/{object_type}", json=prop)
        resp.raise_for_status()
        print(f"  ✓ created {object_type}.{name} ({prop['type']})")
    return ensured


def write_stage_map_to_params(stage_map: dict[str, str], *, dry_run: bool) -> None:
    """Rewrite crm.stage_map in the local params.yaml from the live read-back."""
    if not _PARAMS_PATH.exists():
        print(f"  ! {_PARAMS_PATH} not found — skipping params write (copy from example first).")
        return
    data = yaml.safe_load(_PARAMS_PATH.read_text(encoding="utf-8"))
    crm = data.setdefault("crm", {})
    if crm.get("stage_map") == stage_map:
        print("  · params crm.stage_map already matches live — skip")
        return
    if dry_run:
        print(f"  ~ [dry-run] would write crm.stage_map = {stage_map}")
        return
    crm["stage_map"] = stage_map
    _PARAMS_PATH.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"  ✓ wrote crm.stage_map → {_PARAMS_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotent HubSpot portal provisioning (S10 W1).")
    parser.add_argument("--portal", default=_DEFAULT_PORTAL, help="Portal id (for the summary only).")
    parser.add_argument("--dry-run", action="store_true", help="Plan changes without writing.")
    args = parser.parse_args()

    token = _load_token()
    print(f"=== Provisioning HubSpot portal {args.portal} (dry_run={args.dry_run}) ===")

    with _client(token) as client:
        print("\n[1/3] Reshape deal pipeline → 4 cockpit stages")
        stage_map = reshape_pipeline(client, dry_run=args.dry_run)

        print("\n[2/3] Create custom properties (idempotent)")
        deal_props = create_properties(client, "deals", _DEAL_PROPERTIES, dry_run=args.dry_run)
        contact_props = create_properties(
            client, "contacts", _CONTACT_PROPERTIES, dry_run=args.dry_run
        )

        print("\n[3/3] Write stage map back to params.yaml")
        write_stage_map_to_params(stage_map, dry_run=args.dry_run)

    print("\n=== Summary ===")
    print("Stage map (cockpit → HubSpot stage id):")
    for value, stage_id in stage_map.items():
        print(f"  {value:<12} → {stage_id}")
    print(f"Deal gt_* properties ensured:    {deal_props}")
    print(f"Contact gt_* properties ensured: {contact_props}")
    print("Reused existing deal props (no dup): amount, funding_source, program_type")
    print("INV-1: schema only — no contact/deal records written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

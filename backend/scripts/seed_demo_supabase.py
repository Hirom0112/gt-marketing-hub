"""DH-0b — seed the curated 6-family demo cohort into the shared Supabase.

The §10.5 live-seed step (MULTI_AGENT_COCKPIT §10.5): make the apply pages + the
cockpit read ONE truth on the curated cohort. It mints one ANONYMOUS Supabase
session per family (its own ``auth.uid()`` — the RLS owner key the apply SPA signs
in as), seeds that family's whole record under its uid via PostgREST (service_role,
SERVER-ONLY — INV-5), persists the SIS verdicts to ``sis_status``, and emits the
``VITE_DEMO_FAMILIES`` + ``VITE_DEMO_SESSIONS`` env the apply family-switcher reads.

ALL synthetic (INV-1): the cohort is :func:`generate_demo_cohort`. The schema must
ALREADY be rebuilt (migrations 0001–0016 applied to a clean ``public``) — this
script only seeds DATA, it does not run DDL.

Usage (env from the repo .env, loaded by the caller):
    uv run python scripts/seed_demo_supabase.py            # seed + print env
    uv run python scripts/seed_demo_supabase.py --emit-only # just re-emit env (no writes)

DESTRUCTIVE-adjacent: it INSERTs into the (freshly emptied) cloud. Confirm the
target ref before running. It refuses to run against an unexpected project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.sis.simulated import SimulatedSISAdapter  # noqa: E402
from app.core.params import load_params  # noqa: E402
from app.data.sis_reconcile_job import run_sis_reconcile  # noqa: E402
from app.data.supabase_repository import SupabaseFamilyRepository  # noqa: E402
from app.data.synthetic import generate_demo_cohort  # noqa: E402

EXPECTED_REF = "kgyzptpzccoczbruhvow"  # the throwaway gt-apply-demo project (safety guard)
EXAMPLE_PARAMS = Path(__file__).resolve().parents[1].parent / "params" / "params.example.yaml"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env {name} — load the repo .env before running")
    return v


def _ref(url: str) -> str:
    # https://<ref>.supabase.co
    return url.split("://", 1)[1].split(".", 1)[0]


def _columns(database_url: str, table: str) -> set[str]:
    """The live column set for a table (introspected — keeps the seed drift-proof)."""
    out = subprocess.run(
        [
            "psql",
            database_url,
            "-tAc",
            "select column_name from information_schema.columns "
            f"where table_schema='public' and table_name='{table}';",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return {c.strip() for c in out.stdout.splitlines() if c.strip()}


def _mint_anon_session(url: str, anon_key: str) -> dict[str, Any]:
    """Mint one anonymous Supabase session → {uid, access_token, refresh_token}."""
    resp = httpx.post(
        f"{url}/auth/v1/signup",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json={"data": {}},
        timeout=30.0,
    )
    resp.raise_for_status()
    body = resp.json()
    return {
        "uid": body["user"]["id"],
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
    }


def _post_rows(url: str, key: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    resp = httpx.post(
        f"{url}/rest/v1/{table}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json=rows,
        timeout=60.0,
    )
    if resp.status_code >= 300:
        sys.exit(f"INSERT {table} failed: HTTP {resp.status_code} {resp.text[:300]}")


def _hint(stage: str, assigned: bool) -> str:
    seat = "assigned" if assigned else "UNASSIGNED — admin routes live"
    return f"{stage} · {seat}"


def main() -> None:
    url = _env("SUPABASE_URL").rstrip("/")
    service_key = _env("SUPABASE_SERVICE_ROLE_KEY")
    anon_key = _env("SUPABASE_ANON_KEY")
    database_url = _env("DATABASE_URL")
    ref = _ref(url)
    if ref != EXPECTED_REF:
        sys.exit(f"REFUSING: SUPABASE_URL ref {ref!r} != expected throwaway {EXPECTED_REF!r}")

    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)
    emit_only = "--emit-only" in sys.argv

    # One anonymous session per family (deterministic family order). uid = the
    # family's RLS owner key; family_record.user_id is set to it so the apply SPA,
    # signed in as that uid, reads exactly this family (RLS-scoped, no leak).
    uid_by_family: dict[str, str] = {}
    sessions: dict[str, dict[str, str]] = {}
    demo_families: list[dict[str, str]] = []
    if not emit_only:
        for fam in ds.families:
            s = _mint_anon_session(url, anon_key)
            uid_by_family[str(fam.family_id)] = s["uid"]
            sessions[s["uid"]] = {
                "access_token": s["access_token"],
                "refresh_token": s["refresh_token"],
            }

        # Seed in FK order: spine first, then the source/student tables. The 2
        # sales_agents the assigned_rep_id FK needs were seeded by migration 0013.
        cols_cache: dict[str, set[str]] = {}

        def cols(table: str) -> set[str]:
            if table not in cols_cache:
                cols_cache[table] = _columns(database_url, table)
            return cols_cache[table]

        def shape(model: Any, table: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
            row = json.loads(model.model_dump_json())
            if extra:
                row.update(extra)
            table_cols = cols(table)
            # Warn (don't silently drop) on a model field with no DB column — that is
            # model/DDL drift (a missing migration), the exact failure 0016 fixed. The
            # demo would otherwise look fine while the column is absent.
            missing = set(row) - table_cols
            if missing:
                print(
                    f"WARN: {table} has no column(s) for {sorted(missing)} — drift?",
                    file=sys.stderr,
                )
            return {k: v for k, v in row.items() if k in table_cols}

        # family_record — inject user_id (the minted anon uid) so RLS owner-scoping holds.
        _post_rows(
            url,
            service_key,
            "family_record",
            [
                shape(f, "family_record", extra={"user_id": uid_by_family[str(f.family_id)]})
                for f in ds.families
            ],
        )
        # The rest are plain shape-and-post, in FK order (table → source models).
        # `lead_assignment` carries the seeded baseline ownership-history facts (one
        # per owned family, LA-23) so the deal-view timeline has provenance live in
        # the cloud demo; its FKs (family_record above + sales_agent from migration
        # 0013) are already present.
        plain: list[tuple[str, list[Any]]] = [
            ("leads_new", list(ds.leads)),
            ("app_form", [*ds.app_forms, *ds.student_app_forms]),
            ("enrollment_forms", [*ds.enrollment_forms, *ds.student_enrollment_forms]),
            ("community_profiles", list(ds.community_profiles)),
            ("student", list(ds.students)),
            ("lead_assignment", list(ds.lead_assignments)),
        ]
        for table, items in plain:
            _post_rows(url, service_key, table, [shape(x, table) for x in items])

        # Persist the SIS verdicts to sis_status (the 🔴/🟡/✅ buckets the cockpit +
        # apply "Closed — pending SIS" read). Reconcile the seeded cloud repo against
        # the demo cohort's aligned roster (DH-3), then write the 4 PII-firewall fields.
        repo = SupabaseFamilyRepository(base_url=url, service_role_key=service_key, params=params)
        adapter = SimulatedSISAdapter.from_cohort(
            ds, seed=params.back_to_school.seed, params=params
        )
        verdicts = run_sis_reconcile(repo, adapter, params)
        sis_rows = [
            {
                "family_id": str(v.family_id),
                "present": v.present,
                "confirmed_at": v.confirmed_at.isoformat() if v.confirmed_at else None,
                "bucket": str(v.bucket),
            }
            for v in verdicts
        ]
        _post_rows(url, service_key, "sis_status", sis_rows)

    # Emit the apply-SPA env (the switcher list + the per-uid session tokens).
    for fam in ds.families:
        uid = uid_by_family.get(str(fam.family_id), "")
        demo_families.append(
            {
                "uid": uid,
                "familyId": str(fam.family_id),
                "label": fam.display_name,
                "hint": _hint(
                    fam.current_stage.value if fam.current_stage else "interest",
                    fam.assigned_rep_id is not None,
                ),
            }
        )

    print("VITE_DEMO_FAMILIES=" + json.dumps(demo_families, separators=(",", ":")))
    print("VITE_DEMO_SESSIONS=" + json.dumps(sessions, separators=(",", ":")))


if __name__ == "__main__":
    main()

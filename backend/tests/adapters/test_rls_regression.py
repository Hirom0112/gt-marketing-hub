"""Cross-account RLS regression — THREAT_MODEL.md §6, D-RLS-5 (INV-5).

The product disclosed a CWE-639 / IDOR (a `public`-schema table with RLS never
enabled, behind the anon key). This test is the live guard that the cure holds:
two synthetic accounts are seeded, then account A attempts to `SELECT` account
B's rows under the `anon`/`authenticated` role. A correct deny-by-default,
owner-scoped, null-guarded policy set returns **0 rows** (D-RLS-2, D-RLS-3).

Environment note (ASSUMPTIONS.md A-3): the build environment has no
`SUPABASE_URL`, so migrations cannot be applied to a live DB here. Per the A-3
TODO spec this test **skips-with-marker** when `SUPABASE_URL` is absent — that
skip is expected and correct. When a live Supabase IS configured, it runs for
real and asserts the 0-row property. The static invariant guard
(`tests/unit/test_migrations_rls.py`) runs unconditionally with no DB.
"""

from __future__ import annotations

import os

import pytest


def test_foreign_read_returns_zero_rows() -> None:
    """A foreign SELECT under anon/authenticated returns 0 rows (D-RLS-5).

    Two synthetic accounts; account A reads account B's `family_record` rows
    using the anon (publishable) key with account A's auth context. RLS must
    yield zero rows. Skipped when no live Supabase is configured (A-3).
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    if not supabase_url:
        pytest.skip("no SUPABASE_URL — RLS regression requires a live Supabase")

    # --- Live path (runs only when SUPABASE_URL is present). ----------------
    # Imported lazily so the module loads (and skips cleanly) in environments
    # without the supabase client installed (S0 keeps it out of the runtime
    # deps; it arrives with the live-DB slice).
    from supabase import create_client  # type: ignore[import-not-found]

    anon_key = os.environ["SUPABASE_ANON_KEY"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    account_a_jwt = os.environ["RLS_TEST_ACCOUNT_A_JWT"]
    account_b_user_id = os.environ["RLS_TEST_ACCOUNT_B_USER_ID"]

    # service_role (BYPASSRLS, server-only — D-RLS-4) seeds a row owned by B.
    admin = create_client(supabase_url, service_key)
    seeded = (
        admin.table("family_record")
        .insert(
            {
                "family_id": "00000000-0000-0000-0000-0000000000b0",
                "user_id": account_b_user_id,
                "display_name": "The Synthetic-B Family",
                "primary_contact_synthetic_email": "synthetic-b@example.test",
                "current_stage": "interest",
                "attribution_source": "rls-regression-seed",
            }
        )
        .execute()
    )
    assert seeded.data, "seed of account B's row failed"

    try:
        # Account A reads B's rows under the anon key with A's JWT. RLS must
        # return zero rows (D-RLS-2/3/5).
        as_a = create_client(supabase_url, anon_key)
        as_a.postgrest.auth(account_a_jwt)
        result = as_a.table("family_record").select("*").execute()
        foreign = [row for row in (result.data or []) if row.get("user_id") == account_b_user_id]
        assert foreign == [], (
            f"IDOR REGRESSION (D-RLS-5): account A read {len(foreign)} of "
            f"account B's family_record rows under the anon role — RLS failed"
        )
    finally:
        admin.table("family_record").delete().eq(
            "family_id", "00000000-0000-0000-0000-0000000000b0"
        ).execute()

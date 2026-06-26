"""Live proof that the API connection role lacks BYPASSRLS (A1 — task 4).

The headline lever of A1's program isolation: the app/API connects as a
least-privilege Postgres role (`app_runtime`) created **WITHOUT** the BYPASSRLS
attribute, so even the SERVER path is RLS-bounded to its own program. A role with
BYPASSRLS would silently defeat the RESTRICTIVE program policy (PLAN_v2.md §A1
Risk 2). This test asserts ``pg_roles.rolbypassrls = false`` for ``app_runtime``;
the true ``service_role``/superuser (the BYPASSRLS cross-program read path) is
reserved for migrations only.

Like ``tests/adapters/test_rls_regression.py`` (D-RLS-5), this is a LIVE proof: it
needs a real Postgres to inspect ``pg_roles``. With no live DB configured it
**skips cleanly** (ASSUMPTIONS.md A-3) so CI without creds still passes — the
unconditional build-time guard is the static migration test
(``tests/unit/test_migrations_rls.py::test_program_id_restrictive_isolation``).

Connection: a direct Postgres DSN (``SUPABASE_DB_URL`` / ``DATABASE_URL``) read
via ``psycopg`` IF that optional driver is importable; otherwise the test skips
(no new hard dependency is added for a proof that does not run in this env).
"""

from __future__ import annotations

import os

import pytest

# The role the API connects as (created by migration 0024). Its defining property
# for A1: it must NOT bypass RLS.
_APP_ROLE = "app_runtime"


def _db_dsn() -> str | None:
    """A direct Postgres DSN from the canonical env vars, if configured."""
    for var in ("SUPABASE_DB_URL", "DATABASE_URL"):
        value = (os.environ.get(var) or "").strip()
        if value and not value.startswith("<"):
            return value
    return None


def test_app_role_lacks_bypassrls() -> None:
    """`app_runtime` exists and has rolbypassrls = false (server path stays RLS-bounded).

    Skips when no live Postgres DSN is configured or the optional psycopg driver is
    not installed (A-3) — the static guard covers the invariant unconditionally.
    """
    # A live Supabase must be in play (mirror the regression test's gating) AND a
    # direct DSN must be available to inspect pg_roles.
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not supabase_url or supabase_url.startswith("<"):
        pytest.skip("no SUPABASE_URL — app-role proof requires a live Supabase")

    dsn = _db_dsn()
    if dsn is None:
        pytest.skip("no SUPABASE_DB_URL/DATABASE_URL — app-role proof needs a direct DSN")

    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed — live DB proof skipped")

    with psycopg.connect(dsn) as conn:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s",
            (_APP_ROLE,),
        ).fetchone()

    assert row is not None, (
        f"role {_APP_ROLE!r} not found — migration 0024 must create the "
        f"least-privilege API connection role"
    )
    assert row[0] is False, (
        f"{_APP_ROLE!r} has BYPASSRLS — program isolation is silently defeated "
        f"(PLAN_v2.md §A1 Risk 2); the app role MUST be NOBYPASSRLS"
    )

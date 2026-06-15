"""Static RLS-invariant guard over the DDL migrations (no database).

Enforces the THREAT_MODEL.md §6 doctrine (CLAUDE.md §1, INV-5) by parsing the
`.sql` files directly, so the invariant is checked on EVERY build — even with no
Supabase present (ASSUMPTIONS.md A-3). The live cross-account regression
(`tests/adapters/test_rls_regression.py`, D-RLS-5) complements this; this test
makes the deny-by-default + null-guard invariant impossible to silently lose.

Asserts:
  * D-RLS-1 — every `CREATE TABLE` is matched by an `ENABLE ROW LEVEL SECURITY`.
  * D-RLS-2 — every table carries at least one policy with the `auth.uid()` null
    guard (`auth.uid() ... IS NOT NULL`).
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "app" / "data" / "migrations"

_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ENABLE_RLS = re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_CREATE_POLICY = re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE)
# The null guard: `auth.uid()` somewhere on a line/clause that also says
# `IS NOT NULL`. We look for `auth.uid()` followed (allowing a closing paren and
# whitespace) by `IS NOT NULL`.
_NULL_GUARD = re.compile(r"auth\.uid\(\)\s*\)?\s*IS\s+NOT\s+NULL", re.IGNORECASE)
# Security-definer functions are banned in the exposed schema (D-RLS-7).
_SECURITY_DEFINER = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)


def _sql_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no .sql migrations found under {MIGRATIONS_DIR}"
    return files


def _all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _sql_files())


def test_every_table_enables_rls() -> None:
    """D-RLS-1: count(CREATE TABLE) == count(ENABLE ROW LEVEL SECURITY)."""
    sql = _all_sql()
    n_tables = len(_CREATE_TABLE.findall(sql))
    n_rls = len(_ENABLE_RLS.findall(sql))
    assert n_tables > 0, "expected at least one CREATE TABLE in the migrations"
    assert n_tables == n_rls, (
        f"deny-by-default RLS violated (D-RLS-1): {n_tables} CREATE TABLE vs "
        f"{n_rls} ENABLE ROW LEVEL SECURITY — every public-schema table must "
        f"enable RLS at creation"
    )


def test_at_least_one_null_guarded_policy() -> None:
    """D-RLS-2: at least one owner-scoped policy with the auth.uid() null guard."""
    sql = _all_sql()
    assert _CREATE_POLICY.search(sql), "expected at least one CREATE POLICY"
    assert _NULL_GUARD.search(sql), (
        "null guard missing (D-RLS-2): no policy contains "
        "`auth.uid() ... IS NOT NULL` — the explicit guard that closes the "
        "`null = user_id` IDOR trap"
    )


def test_one_null_guard_per_policy() -> None:
    """D-RLS-2: every policy carries the null guard (no unguarded policy slips in)."""
    sql = _all_sql()
    n_policies = len(_CREATE_POLICY.findall(sql))
    n_guards = len(_NULL_GUARD.findall(sql))
    assert n_policies > 0, "expected at least one CREATE POLICY"
    assert n_guards >= n_policies, (
        f"unguarded policy detected (D-RLS-2): {n_policies} policies but only "
        f"{n_guards} `auth.uid() IS NOT NULL` guards — every policy must be "
        f"null-guarded"
    )


def test_no_security_definer_in_exposed_schema() -> None:
    """D-RLS-7: no security-definer helper functions in the exposed schema."""
    sql = _all_sql()
    assert not _SECURITY_DEFINER.search(sql), (
        "security-definer function found (D-RLS-7 violated): exposed-schema "
        "RLS must not rely on SECURITY DEFINER helpers"
    )

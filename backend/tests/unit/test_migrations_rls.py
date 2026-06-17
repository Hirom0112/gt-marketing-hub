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


def _enrich_sql() -> str:
    """The 0006 apply-events enrichment migration text (Task A)."""
    return (MIGRATIONS_DIR / "0006_apply_events_enrich.sql").read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    """Drop `-- …` line comments so structural assertions match DDL, not prose."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _owner_delete_sql() -> str:
    """The 0007 owner-scoped DELETE migration DDL (comments stripped)."""
    return _strip_comments(
        (MIGRATIONS_DIR / "0007_owner_delete.sql").read_text(encoding="utf-8")
    )


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


# ---------------------------------------------------------------------------
# 0006 apply_events enrichment (Task A) — step→form→field granularity, ADDITIVE
# and doctrine-preserving (no CREATE TABLE, no policy change). The columns are
# metadata-only (form_key is a structural form id, NEVER PII, NEVER a child key:
# INV-1/INV-6/COPPA); the enum gains three additive interaction kinds.
# ---------------------------------------------------------------------------

_ENRICH_NEW_COLUMNS = ("form_key", "nav_seq")
_ENRICH_NEW_ENUM_VALUES = ("form_viewed", "form_completed", "field_changed")


def test_0006_adds_form_key_and_nav_seq_columns() -> None:
    """0006 adds the two nullable enrichment columns (additive/back-compat)."""
    sql = _enrich_sql()
    for column in _ENRICH_NEW_COLUMNS:
        assert re.search(rf"ADD\s+COLUMN[^;]*\b{column}\b", sql, re.IGNORECASE), (
            f"0006 must `ADD COLUMN {column}` on apply_events"
        )


def test_0006_adds_three_enum_values_additively() -> None:
    """0006 ADDs the three new apply_event_type values (the existing 6 are kept)."""
    sql = _enrich_sql()
    for value in _ENRICH_NEW_ENUM_VALUES:
        assert re.search(rf"ALTER\s+TYPE[^;]*ADD\s+VALUE[^;]*'{value}'", sql, re.IGNORECASE), (
            f"0006 must `ALTER TYPE apply_event_type ADD VALUE '{value}'`"
        )


def test_0006_does_not_alter_rls_or_policies() -> None:
    """0006 is metadata-only: no CREATE TABLE / CREATE POLICY / RLS toggle (D-RLS).

    The column/enum additions inherit apply_events' existing owner-scoped,
    null-guarded policies from 0003 — 0006 must not add, alter, or weaken any.
    """
    sql = _enrich_sql()
    assert not _CREATE_TABLE.search(sql), "0006 must not create a table (enrichment only)"
    assert not _CREATE_POLICY.search(sql), "0006 must not add a policy (inherits 0003's)"
    assert not _ENABLE_RLS.search(sql), "0006 must not re-toggle RLS (already enabled in 0003)"
    assert not re.search(r"\bDROP\s+POLICY\b", sql, re.IGNORECASE), "0006 must not drop a policy"
    assert not re.search(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", sql, re.IGNORECASE), (
        "0006 must not disable RLS"
    )


# ---------------------------------------------------------------------------
# 0007 owner-scoped DELETE (S18 "My Applications" dashboard) — lets an
# authenticated applicant delete their OWN application. Every DELETE policy is
# owner-scoped + null-guarded (D-RLS-2/D-RLS-3); the migration adds nothing else
# (no CREATE TABLE, no RLS toggle, no FOR ALL, no DROP, and grants DELETE only to
# `authenticated`). service_role is unaffected.
# ---------------------------------------------------------------------------

# The six tables that must gain an owner-scoped DELETE policy.
_DELETE_TABLES = (
    "family_record",
    "leads_new",
    "app_form",
    "enrollment_forms",
    "community_profiles",
    "apply_events",
)
_FOR_DELETE = re.compile(r"\bFOR\s+DELETE\b", re.IGNORECASE)
_FOR_ALL = re.compile(r"\bFOR\s+ALL\b", re.IGNORECASE)


def test_0007_adds_owner_scoped_delete_policy_per_table() -> None:
    """0007 adds a CREATE POLICY ... FOR DELETE for each of the six owned tables."""
    sql = _owner_delete_sql()
    for table in _DELETE_TABLES:
        # A CREATE POLICY whose ON clause names this table and which is FOR DELETE.
        assert re.search(
            rf"CREATE\s+POLICY\s+\w+\s+ON\s+{table}\b[^;]*FOR\s+DELETE",
            sql,
            re.IGNORECASE,
        ), f"0007 must add an owner-scoped FOR DELETE policy on {table}"


def test_0007_every_delete_policy_is_null_guarded() -> None:
    """D-RLS-2: every DELETE policy 0007 adds carries the auth.uid() null guard."""
    sql = _owner_delete_sql()
    n_policies = len(_CREATE_POLICY.findall(sql))
    n_delete = len(_FOR_DELETE.findall(sql))
    n_guards = len(_NULL_GUARD.findall(sql))
    assert n_policies == len(_DELETE_TABLES), (
        f"0007 should add exactly {len(_DELETE_TABLES)} policies, found {n_policies}"
    )
    assert n_delete == n_policies, (
        "every 0007 policy must be FOR DELETE (no INSERT/SELECT/ALL policies)"
    )
    assert n_guards >= n_policies, (
        f"unguarded DELETE policy (D-RLS-2): {n_policies} policies but only "
        f"{n_guards} `auth.uid() IS NOT NULL` guards"
    )


def test_0007_grants_delete_to_authenticated_only() -> None:
    """0007 grants DELETE to `authenticated`; never to anon."""
    sql = _owner_delete_sql()
    grant = re.search(r"GRANT\s+DELETE\b[^;]*", sql, re.IGNORECASE)
    assert grant, "0007 must GRANT DELETE on the owned tables"
    assert re.search(r"\bauthenticated\b", grant.group(0), re.IGNORECASE), (
        "0007 must grant DELETE to `authenticated`"
    )
    assert not re.search(r"\banon\b", grant.group(0), re.IGNORECASE), (
        "0007 must NOT grant DELETE to anon (D-RLS-3: unauthenticated = no rows)"
    )


def test_0007_changes_nothing_else() -> None:
    """0007 is DELETE-policy-only: no CREATE TABLE / RLS toggle / FOR ALL / DROP."""
    sql = _owner_delete_sql()
    assert not _CREATE_TABLE.search(sql), "0007 must not create a table"
    assert not _ENABLE_RLS.search(sql), "0007 must not re-toggle RLS (already enabled)"
    assert not _FOR_ALL.search(sql), "0007 must not use FOR ALL (DELETE-scoped only)"
    assert not re.search(r"\bDROP\s+POLICY\b", sql, re.IGNORECASE), (
        "0007 must not drop a policy"
    )
    assert not re.search(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", sql, re.IGNORECASE), (
        "0007 must not disable RLS"
    )
    assert not _SECURITY_DEFINER.search(sql), "0007 must not use SECURITY DEFINER"

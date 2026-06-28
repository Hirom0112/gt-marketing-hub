"""Static RLS-invariant guard over the 0032 summer-camp migration (D2; no database).

Mirrors the doctrine-parse style of ``tests/unit/test_migrations_rls.py`` (which the
global CREATE==ENABLE==FORCE + one-null-guard-per-policy invariants already cover in
aggregate) and pins 0032's specific shape: two NET-NEW tenant tables, each ENABLE +
FORCE RLS, each carrying a null-guarded PERMISSIVE read policy AND a RESTRICTIVE
program-isolation policy keyed on the app_metadata.program_id claim (the 0024 pattern),
program_id NOT NULL DEFAULT 'summer_camp', no SECURITY DEFINER, and NO child PII column.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "app" / "data" / "migrations"

_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ENABLE_RLS = re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_FORCE_RLS = re.compile(r"\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_CREATE_POLICY = re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE)
_RESTRICTIVE = re.compile(r"\bAS\s+RESTRICTIVE\b", re.IGNORECASE)
_NULL_GUARD = re.compile(r"auth\.uid\(\)\s*\)?\s*IS\s+NOT\s+NULL", re.IGNORECASE)
_SECURITY_DEFINER = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)

_TABLES = ("campus", "camp_registration")


def _strip_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _sql() -> str:
    return _strip_comments((MIGRATIONS_DIR / "0032_summer_camp.sql").read_text(encoding="utf-8"))


def test_0032_creates_two_tables_with_enable_and_force_rls() -> None:
    """D-RLS-1: each new table is created and BOTH ENABLEs and FORCEs RLS."""
    sql = _sql()
    assert len(_CREATE_TABLE.findall(sql)) == len(_TABLES)
    # Per-table: created, enabled, forced.
    for table in _TABLES:
        assert re.search(rf"CREATE\s+TABLE\s+{table}\b", sql, re.IGNORECASE), (
            f"0032 must CREATE TABLE {table}"
        )
        assert re.search(
            rf"ALTER\s+TABLE\s+{table}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
        ), f"0032 must ENABLE RLS on {table}"
        assert re.search(
            rf"ALTER\s+TABLE\s+{table}\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
        ), f"0032 must FORCE RLS on {table}"
    # File-local count balance (mirrors the global invariant).
    assert (
        len(_CREATE_TABLE.findall(sql))
        == len(_ENABLE_RLS.findall(sql))
        == len(_FORCE_RLS.findall(sql))
    )


def test_0032_every_policy_is_null_guarded() -> None:
    """D-RLS-2: every policy carries the auth.uid() IS NOT NULL guard (no unguarded slip)."""
    sql = _sql()
    n_policies = len(_CREATE_POLICY.findall(sql))
    assert n_policies == 4, f"expected 4 policies (2 per table), found {n_policies}"
    assert len(_NULL_GUARD.findall(sql)) >= n_policies


def test_0032_program_isolation_mirrors_0024() -> None:
    """Each table has a RESTRICTIVE program-isolation policy keyed on the program claim."""
    sql = _sql()
    assert len(_RESTRICTIVE.findall(sql)) == len(_TABLES)
    for table in _TABLES:
        assert re.search(
            rf"CREATE\s+POLICY\s+\w+\s+ON\s+{table}\b[^;]*AS\s+RESTRICTIVE",
            sql,
            re.IGNORECASE | re.DOTALL,
        ), f"0032 must add a RESTRICTIVE program-isolation policy on {table}"
    # Every restrictive policy keys on the app_metadata.program_id claim.
    assert len(re.findall(r"app_metadata'?\s*->>\s*'program_id'", sql)) >= len(_TABLES)


def test_0032_program_id_defaults_to_summer_camp() -> None:
    """Each tenant table tags rows program_id NOT NULL DEFAULT 'summer_camp' (INV-11)."""
    sql = _sql()
    for table in _TABLES:
        assert re.search(
            r"program_id\s+text\s+NOT\s+NULL\s+DEFAULT\s+'summer_camp'", sql, re.IGNORECASE
        ), f"{table} must default program_id to 'summer_camp'"
    assert len(re.findall(r"DEFAULT\s+'summer_camp'", sql, re.IGNORECASE)) == len(_TABLES)


def test_0032_no_child_pii_columns() -> None:
    """INV-1/INV-6: camp_registration carries an AGGREGATE band only — no child PII."""
    sql = _sql()
    forbidden = ("child_name", "first_name", "last_name", "dob", "date_of_birth", "birth")
    for token in forbidden:
        assert not re.search(rf"\b{token}\b", sql, re.IGNORECASE), (
            f"0032 must not store child PII ({token}) — aggregate band only (INV-1/INV-6)"
        )
    assert re.search(r"\bchild_grade_band\b", sql), "expected the aggregate child_grade_band column"


def test_0032_no_security_definer() -> None:
    """D-RLS-7: no SECURITY DEFINER helper (inline predicates only)."""
    assert not _SECURITY_DEFINER.search(_sql())

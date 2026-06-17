"""Live RLS-posture check — the SAME static invariants test_migrations_rls runs,
executed at RUNTIME so the cockpit can surface them as a live panel (M7 Panel A).

MULTI_AGENT_COCKPIT.md §7 Panel A: "surface the checks that already exist as tests
(test_migrations_rls count invariants + the cross-account regression) LIVE: every
public table FORCE-RLS + null-guarded ⇒ green; a table that lost its policy ⇒ red
alarm." This module is that reuse: it parses the migration DDL with the IDENTICAL
regexes the unit test uses and returns a structured posture verdict. A table that
loses its FORCE line (or a policy that loses its null guard) flips the posture RED.

This is the deterministic core (CLAUDE §7): pure, no IO beyond reading the committed
`.sql` files, no `app.ai` / `app.adapters` import. The same DDL is the single source
of truth for both the build-time test and the runtime panel — they can never drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The migrations directory — app/core/security_posture.py → parents[1] is app/.
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "data" / "migrations"

# The IDENTICAL regexes test_migrations_rls uses (THREAT_MODEL §6, D-RLS-1/2/7).
_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ENABLE_RLS = re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_FORCE_RLS = re.compile(r"\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_CREATE_POLICY = re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE)
_NULL_GUARD = re.compile(r"auth\.uid\(\)\s*\)?\s*IS\s+NOT\s+NULL", re.IGNORECASE)
_SECURITY_DEFINER = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)


def _strip_comments(sql: str) -> str:
    """Drop `-- …` line comments so structural counts match DDL, not prose."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


@dataclass(frozen=True, slots=True)
class PostureCheck:
    """One named invariant + its pass/fail + a human-readable detail."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PostureResult:
    """The live RLS-posture verdict (Panel A).

    ``green`` is True only when EVERY check passed — a single regressed table
    (lost FORCE, lost its null guard) flips the whole posture RED, fail-closed.
    """

    green: bool
    checks: list[PostureCheck] = field(default_factory=list)


def _migration_text(migrations: list[str] | None) -> str:
    """The concatenated migration DDL — the committed files, or an injected set.

    ``migrations`` lets a test feed a DOCTORED migration set (e.g. a table missing
    its FORCE line) to prove the posture flips RED without touching the real files.
    None ⇒ read every committed `*.sql` under :data:`MIGRATIONS_DIR`.
    """
    if migrations is not None:
        return "\n".join(migrations)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def evaluate_posture(migrations: list[str] | None = None) -> PostureResult:
    """Run the test_migrations_rls invariants LIVE and return the posture verdict.

    Mirrors the four THREAT_MODEL §6 build-time checks exactly:
      * D-RLS-1 — count(CREATE TABLE) == count(ENABLE ROW LEVEL SECURITY);
      * D-RLS-1 — count(CREATE TABLE) == count(FORCE ROW LEVEL SECURITY);
      * D-RLS-2 — every CREATE POLICY carries the `auth.uid() IS NOT NULL` guard;
      * D-RLS-7 — no SECURITY DEFINER in the exposed schema.

    A table that loses its FORCE line drops the force count below the table count
    ⇒ that check fails ⇒ ``green`` is False (the red alarm). ``migrations`` injects
    a doctored set for tests; None reads the committed files.
    """
    raw = _migration_text(migrations)
    stripped = _strip_comments(raw)

    n_tables = len(_CREATE_TABLE.findall(stripped))
    n_enable = len(_ENABLE_RLS.findall(stripped))
    n_force = len(_FORCE_RLS.findall(stripped))
    n_policies = len(_CREATE_POLICY.findall(stripped))
    n_guards = len(_NULL_GUARD.findall(stripped))
    # Security-definer is checked on the RAW text (the build-time test also reads
    # the un-stripped concatenation), so a definer helper anywhere fails it.
    has_definer = bool(_SECURITY_DEFINER.search(raw))

    checks = [
        PostureCheck(
            name="every_table_enables_rls",
            passed=n_tables > 0 and n_tables == n_enable,
            detail=f"{n_tables} CREATE TABLE vs {n_enable} ENABLE ROW LEVEL SECURITY (D-RLS-1)",
        ),
        PostureCheck(
            name="every_table_forces_rls",
            passed=n_tables > 0 and n_tables == n_force,
            detail=f"{n_tables} CREATE TABLE vs {n_force} FORCE ROW LEVEL SECURITY (D-RLS-1)",
        ),
        PostureCheck(
            name="every_policy_null_guarded",
            passed=n_policies > 0 and n_guards >= n_policies,
            detail=f"{n_policies} CREATE POLICY vs {n_guards} auth.uid() null guards (D-RLS-2)",
        ),
        PostureCheck(
            name="no_security_definer_in_exposed_schema",
            passed=not has_definer,
            detail="no definer-rights helper in the exposed schema (D-RLS-7)"
            if not has_definer
            else "a definer-rights helper was found in the exposed schema (D-RLS-7 violated)",
        ),
    ]
    return PostureResult(green=all(c.passed for c in checks), checks=checks)

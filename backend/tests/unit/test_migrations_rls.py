"""Static RLS-invariant guard over the DDL migrations (no database).

Enforces the THREAT_MODEL.md §6 doctrine (CLAUDE.md §1, INV-5) by parsing the
`.sql` files directly, so the invariant is checked on EVERY build — even with no
Supabase present (ASSUMPTIONS.md A-3). The live cross-account regression
(`tests/adapters/test_rls_regression.py`, D-RLS-5) complements this; this test
makes the deny-by-default + null-guard invariant impossible to silently lose.

Asserts:
  * D-RLS-1 — every `CREATE TABLE` is matched by an `ENABLE ROW LEVEL SECURITY`.
  * D-RLS-1 — every `CREATE TABLE` is matched by a `FORCE ROW LEVEL SECURITY`
    (across all migrations) so the table-owner role obeys the policies too
    (AUDIT R2: brand_memory was ENABLEd in 0002 but never FORCEd).
  * D-RLS-2 — every table carries at least one policy with the `auth.uid()` null
    guard (`auth.uid() ... IS NOT NULL`).
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "app" / "data" / "migrations"

_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ENABLE_RLS = re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_FORCE_RLS = re.compile(r"\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
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
    return _strip_comments((MIGRATIONS_DIR / "0007_owner_delete.sql").read_text(encoding="utf-8"))


def _student_grain_sql() -> str:
    """The 0009 per-child `student` grain migration DDL (comments stripped)."""
    return _strip_comments((MIGRATIONS_DIR / "0009_student_grain.sql").read_text(encoding="utf-8"))


def _voucher_events_sql() -> str:
    """The 0010 append-only `voucher_event` timeline migration DDL (comments stripped)."""
    return _strip_comments((MIGRATIONS_DIR / "0010_voucher_events.sql").read_text(encoding="utf-8"))


def _apply_writes_sql() -> str:
    """The 0011 apply-write policies migration DDL (comments stripped)."""
    return _strip_comments((MIGRATIONS_DIR / "0011_apply_writes.sql").read_text(encoding="utf-8"))


def _funding_state_enum_sql() -> str:
    """The 0012 funding_state enum-value addition migration text (NOT stripped)."""
    return (MIGRATIONS_DIR / "0012_funding_state_values.sql").read_text(encoding="utf-8")


def _sales_agents_sql() -> str:
    """The 0013 sales_agent registry + family_record.assigned_rep_id DDL (comments stripped)."""
    return _strip_comments((MIGRATIONS_DIR / "0013_sales_agents.sql").read_text(encoding="utf-8"))


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


def test_every_table_forces_rls() -> None:
    """D-RLS-1: count(CREATE TABLE) == count(FORCE ROW LEVEL SECURITY).

    ENABLE subjects non-owner roles to the policies, but the table-owner role is
    exempt unless RLS is also FORCED. Every public table must therefore be FORCEd
    across the migrations (AUDIT R2: brand_memory, created/ENABLEd in 0002, was
    omitted from 0004's FORCE list and is FORCEd by 0008).
    """
    # Strip comments so the FORCE/CREATE counts reflect DDL, not the prose in
    # 0004's header (which mentions "FORCE ROW LEVEL SECURITY" several times).
    sql = _strip_comments(_all_sql())
    n_tables = len(_CREATE_TABLE.findall(sql))
    n_force = len(_FORCE_RLS.findall(sql))
    assert n_tables > 0, "expected at least one CREATE TABLE in the migrations"
    assert n_tables == n_force, (
        f"owner-role escape hatch open (D-RLS-1): {n_tables} CREATE TABLE vs "
        f"{n_force} FORCE ROW LEVEL SECURITY — every public-schema table must "
        f"FORCE RLS so even the table-owner role obeys the owner-scoped policies"
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
    assert not re.search(r"\bDROP\s+POLICY\b", sql, re.IGNORECASE), "0007 must not drop a policy"
    assert not re.search(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", sql, re.IGNORECASE), (
        "0007 must not disable RLS"
    )
    assert not _SECURITY_DEFINER.search(sql), "0007 must not use SECURITY DEFINER"


# ---------------------------------------------------------------------------
# 0009 per-child `student` grain (TODO.md R1) — the live household→child grain.
# A new CREATE TABLE that MUST ENABLE *and* FORCE RLS (D-RLS-1) and carry
# owner-scoped, null-guarded SELECT + owner DELETE policies, scoped through the
# owned `family_record.user_id` subquery (D-RLS-2/D-RLS-3) — exactly the pattern
# 0001/0003/0007 use for the other `family_id`-owned source tables. The household
# identity key is `family_record.user_id` (no new household_id column). The
# all-migrations enable/force/null-guard invariants above already cover the table
# in aggregate; these tests pin the table's specific policy shape.
# ---------------------------------------------------------------------------


def test_0009_creates_student_with_enable_and_force_rls() -> None:
    """0009 adds the `student` table and both ENABLEs and FORCEs RLS on it (D-RLS-1)."""
    sql = _student_grain_sql()
    assert re.search(r"CREATE\s+TABLE\s+student\b", sql, re.IGNORECASE), (
        "0009 must CREATE TABLE student"
    )
    assert re.search(
        r"ALTER\s+TABLE\s+student\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0009 must ENABLE RLS on student"
    assert re.search(
        r"ALTER\s+TABLE\s+student\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0009 must FORCE RLS on student (owner-role escape hatch, D-RLS-1)"


def test_0009_student_owned_via_family_record_user_id() -> None:
    """0009 scopes `student` ownership through family_id → family_record.user_id.

    The household identity key is `family_record.user_id` (no separate
    household_id column): the student FKs to family_record, and the policies scope
    via the owned-family subquery on `user_id` — the same predicate as the other
    family_id-owned source tables.
    """
    sql = _student_grain_sql()
    assert re.search(
        r"family_id\s+uuid\s+NOT\s+NULL\s+REFERENCES\s+family_record\s*\(\s*family_id\s*\)",
        sql,
        re.IGNORECASE,
    ), "student.family_id must REFERENCE family_record(family_id)"
    assert re.search(
        r"FROM\s+family_record\s+fr\s+WHERE\s+fr\.user_id\s*=\s*\(\s*SELECT\s+auth\.uid\(\)\s*\)",
        sql,
        re.IGNORECASE,
    ), "student policies must scope via family_record.user_id = auth.uid() (the household key)"
    # No household_id column (least churn — user_id IS the household key).
    assert not re.search(r"\bhousehold_id\b", sql, re.IGNORECASE), (
        "0009 must NOT add a household_id column (family_record.user_id is the household key)"
    )


def test_0009_student_has_owner_select_and_delete_policies() -> None:
    """0009 adds an owner-scoped SELECT and an owner-scoped DELETE policy on student.

    Mirrors 0001 (SELECT) + 0007 (owner DELETE). No INSERT policy here (the
    apply-SPA child-write path is a separate task; writes stay deny-by-default).
    """
    sql = _student_grain_sql()
    assert re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+student\b[^;]*FOR\s+SELECT", sql, re.IGNORECASE
    ), "0009 must add an owner-scoped FOR SELECT policy on student"
    assert re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+student\b[^;]*FOR\s+DELETE", sql, re.IGNORECASE
    ), "0009 must add an owner-scoped FOR DELETE policy on student"
    # Exactly two policies, both null-guarded; no INSERT/UPDATE/ALL policy.
    n_policies = len(_CREATE_POLICY.findall(sql))
    n_guards = len(_NULL_GUARD.findall(sql))
    assert n_policies == 2, f"0009 should add exactly 2 policies on student, found {n_policies}"
    assert n_guards >= n_policies, (
        f"unguarded student policy (D-RLS-2): {n_policies} policies but only "
        f"{n_guards} `auth.uid() IS NOT NULL` guards"
    )
    assert not _FOR_ALL.search(sql), "0009 must not use FOR ALL on student"
    assert not re.search(r"FOR\s+INSERT", sql, re.IGNORECASE), (
        "0009 must not add an INSERT policy (apply-SPA write path is a separate task)"
    )


def test_0009_grants_select_and_delete_not_insert() -> None:
    """0009 grants SELECT (anon+authenticated) + DELETE (authenticated only); no INSERT."""
    sql = _student_grain_sql()
    grant_select = re.search(r"GRANT\s+SELECT\s+ON\s+student\b[^;]*", sql, re.IGNORECASE)
    assert grant_select, "0009 must GRANT SELECT ON student"
    grant_delete = re.search(r"GRANT\s+DELETE\s+ON\s+student\b[^;]*", sql, re.IGNORECASE)
    assert grant_delete, "0009 must GRANT DELETE ON student"
    assert re.search(r"\bauthenticated\b", grant_delete.group(0), re.IGNORECASE), (
        "0009 must grant DELETE to authenticated"
    )
    assert not re.search(r"\banon\b", grant_delete.group(0), re.IGNORECASE), (
        "0009 must NOT grant DELETE to anon (D-RLS-3)"
    )
    assert not re.search(r"GRANT\s+INSERT\b", sql, re.IGNORECASE), (
        "0009 must NOT grant INSERT (apply-SPA child-write path is a separate task)"
    )


def test_0009_no_security_definer() -> None:
    """D-RLS-7: 0009 uses no security-definer helper (inline subqueries only)."""
    assert not _SECURITY_DEFINER.search(_student_grain_sql()), (
        "0009 must not use a SECURITY DEFINER helper (D-RLS-7)"
    )


# ---------------------------------------------------------------------------
# 0010 append-only `voucher_event` timeline (TODO.md R2) — a per-(family/student)
# voucher state-transition log feeding the work-queue deadline ranking + §10
# observability. A new CREATE TABLE that MUST ENABLE *and* FORCE RLS (D-RLS-1) and
# carry owner-scoped, null-guarded SELECT + INSERT policies (the INSERT carries a
# null-guarded WITH CHECK), scoped through the owned `family_record.user_id`
# subquery — the same pattern 0003/0009 use for the other family_id-owned tables.
# CRITICAL: APPEND-ONLY — grants only SELECT + INSERT (NEVER UPDATE / DELETE to
# anon/authenticated), so the timeline is immutable from the client. The
# all-migrations enable/force/null-guard invariants above already cover the table
# in aggregate; these tests pin the table's append-only shape + policy set.
# ---------------------------------------------------------------------------

_FOR_INSERT = re.compile(r"\bFOR\s+INSERT\b", re.IGNORECASE)


def test_0010_creates_voucher_event_with_enable_and_force_rls() -> None:
    """0010 adds the `voucher_event` table and both ENABLEs and FORCEs RLS (D-RLS-1)."""
    sql = _voucher_events_sql()
    assert re.search(r"CREATE\s+TABLE\s+voucher_event\b", sql, re.IGNORECASE), (
        "0010 must CREATE TABLE voucher_event"
    )
    assert re.search(
        r"ALTER\s+TABLE\s+voucher_event\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0010 must ENABLE RLS on voucher_event"
    assert re.search(
        r"ALTER\s+TABLE\s+voucher_event\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0010 must FORCE RLS on voucher_event (owner-role escape hatch, D-RLS-1)"


def test_0010_voucher_event_owned_via_family_record_user_id() -> None:
    """0010 scopes `voucher_event` ownership through family_id → family_record.user_id.

    Same ownership predicate as the other family_id-owned tables: the event FKs to
    family_record, and the policies scope via the owned-family subquery on
    `user_id` (the household identity key) — null-guarded.
    """
    sql = _voucher_events_sql()
    assert re.search(
        r"family_id\s+uuid\s+NOT\s+NULL\s+REFERENCES\s+family_record\s*\(\s*family_id\s*\)",
        sql,
        re.IGNORECASE,
    ), "voucher_event.family_id must REFERENCE family_record(family_id)"
    assert re.search(
        r"FROM\s+family_record\s+fr\s+WHERE\s+fr\.user_id\s*=\s*\(\s*SELECT\s+auth\.uid\(\)\s*\)",
        sql,
        re.IGNORECASE,
    ), "voucher_event policies must scope via family_record.user_id = auth.uid()"


def test_0010_voucher_event_has_owner_select_and_insert_policies() -> None:
    """0010 adds an owner-scoped SELECT and an owner-scoped INSERT policy (no UPDATE/DELETE).

    Append-only: SELECT (read own timeline) + INSERT (append own event, null-guarded
    WITH CHECK). No UPDATE/DELETE/ALL policy — the timeline is immutable once written.
    """
    sql = _voucher_events_sql()
    assert re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+voucher_event\b[^;]*FOR\s+SELECT", sql, re.IGNORECASE
    ), "0010 must add an owner-scoped FOR SELECT policy on voucher_event"
    assert re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+voucher_event\b[^;]*FOR\s+INSERT", sql, re.IGNORECASE
    ), "0010 must add an owner-scoped FOR INSERT policy on voucher_event"
    # Exactly two policies, both null-guarded; no UPDATE/DELETE/ALL policy.
    n_policies = len(_CREATE_POLICY.findall(sql))
    n_guards = len(_NULL_GUARD.findall(sql))
    assert n_policies == 2, (
        f"0010 should add exactly 2 policies on voucher_event, found {n_policies}"
    )
    assert n_guards >= n_policies, (
        f"unguarded voucher_event policy (D-RLS-2): {n_policies} policies but only "
        f"{n_guards} `auth.uid() IS NOT NULL` guards"
    )
    assert not _FOR_ALL.search(sql), "0010 must not use FOR ALL on voucher_event"
    assert not _FOR_DELETE.search(sql), (
        "0010 must not add a DELETE policy (append-only — the timeline is immutable)"
    )
    assert not re.search(r"FOR\s+UPDATE", sql, re.IGNORECASE), (
        "0010 must not add an UPDATE policy (append-only — the timeline is immutable)"
    )


def test_0010_voucher_event_insert_has_null_guarded_with_check() -> None:
    """D-RLS-2: the INSERT policy carries a null-guarded WITH CHECK (owner-scoped)."""
    sql = _voucher_events_sql()
    # The INSERT policy must use WITH CHECK (not USING) and be null-guarded.
    insert_policy = re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+voucher_event\b[^;]*FOR\s+INSERT[^;]*WITH\s+CHECK[^;]*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert insert_policy, "0010 INSERT policy must carry a WITH CHECK clause"
    assert _NULL_GUARD.search(insert_policy.group(0)), (
        "0010 INSERT WITH CHECK must be null-guarded (auth.uid() IS NOT NULL)"
    )


def test_0010_grants_select_and_insert_not_update_or_delete() -> None:
    """0010 grants SELECT + INSERT only — NEVER UPDATE / DELETE (append-only)."""
    sql = _voucher_events_sql()
    grant_select = re.search(r"GRANT\s+SELECT\s+ON\s+voucher_event\b[^;]*", sql, re.IGNORECASE)
    assert grant_select, "0010 must GRANT SELECT ON voucher_event"
    grant_insert = re.search(r"GRANT\s+INSERT\s+ON\s+voucher_event\b[^;]*", sql, re.IGNORECASE)
    assert grant_insert, "0010 must GRANT INSERT ON voucher_event"
    assert re.search(r"\bauthenticated\b", grant_insert.group(0), re.IGNORECASE), (
        "0010 must grant INSERT to authenticated"
    )
    # APPEND-ONLY: no UPDATE / DELETE grant anywhere in the migration.
    assert not re.search(r"GRANT\s+UPDATE\b", sql, re.IGNORECASE), (
        "0010 must NOT grant UPDATE (append-only timeline)"
    )
    assert not re.search(r"GRANT\s+DELETE\b", sql, re.IGNORECASE), (
        "0010 must NOT grant DELETE (append-only timeline)"
    )


def test_0010_no_security_definer() -> None:
    """D-RLS-7: 0010 uses no security-definer helper (inline subqueries only)."""
    assert not _SECURITY_DEFINER.search(_voucher_events_sql()), (
        "0010 must not use a SECURITY DEFINER helper (D-RLS-7)"
    )


# ---------------------------------------------------------------------------
# 0011 apply-write policies (the two write paths the apply SPA needs that were
# deny-by-default): (A) student owner-scoped, null-guarded INSERT (the child-write
# path 0009 deferred), scoped through family_id → family_record.user_id; and (B)
# family_record owner-scoped, null-guarded UPDATE (so the SPA can write
# funding_type), mirroring 0007's owner-DELETE predicate but FOR UPDATE with BOTH
# USING and WITH CHECK. Adds NO table (RLS/FORCE counts unchanged), no FOR ALL, no
# DROP, no SECURITY DEFINER; grants INSERT(student)/UPDATE(family_record) to
# `authenticated` only — never anon (D-RLS-3). service_role is unaffected.
# ---------------------------------------------------------------------------

_FOR_UPDATE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)
_WITH_CHECK = re.compile(r"\bWITH\s+CHECK\b", re.IGNORECASE)
_USING = re.compile(r"\bUSING\b", re.IGNORECASE)


def test_0011_adds_student_owner_insert_policy() -> None:
    """0011 adds an owner-scoped FOR INSERT policy on student (the deferred child-write path).

    Scoped through family_id → family_record.user_id (the SAME ownership subquery
    0009's SELECT/DELETE use), with a null-guarded WITH CHECK.
    """
    sql = _apply_writes_sql()
    insert_policy = re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+student\b[^;]*FOR\s+INSERT[^;]*WITH\s+CHECK[^;]*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert insert_policy, (
        "0011 must add an owner-scoped FOR INSERT policy on student with WITH CHECK"
    )
    assert _NULL_GUARD.search(insert_policy.group(0)), (
        "0011 student INSERT WITH CHECK must be null-guarded (auth.uid() IS NOT NULL)"
    )
    assert re.search(
        r"family_id\s+IN\s*\(\s*SELECT\s+fr\.family_id\s+FROM\s+family_record\s+fr"
        r"\s+WHERE\s+fr\.user_id\s*=\s*\(\s*SELECT\s+auth\.uid\(\)\s*\)",
        insert_policy.group(0),
        re.IGNORECASE,
    ), "0011 student INSERT must scope via family_id → family_record.user_id"


def test_0011_adds_family_record_owner_update_policy() -> None:
    """0011 adds an owner-scoped FOR UPDATE policy on family_record (so the SPA sets funding_type).

    Mirrors 0007's owner-DELETE predicate but FOR UPDATE with BOTH USING (gates the
    rows visible to update) and WITH CHECK (gates the post-image so the owner can't
    reassign user_id away), each null-guarded.
    """
    sql = _apply_writes_sql()
    update_policy = re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+family_record\b[^;]*FOR\s+UPDATE[^;]*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert update_policy, "0011 must add an owner-scoped FOR UPDATE policy on family_record"
    body = update_policy.group(0)
    assert _USING.search(body), "0011 family_record UPDATE must have a USING clause"
    assert _WITH_CHECK.search(body), "0011 family_record UPDATE must have a WITH CHECK clause"
    # Both clauses null-guarded (≥2 guards: one in USING, one in WITH CHECK).
    assert len(_NULL_GUARD.findall(body)) >= 2, (
        "0011 family_record UPDATE must be null-guarded in BOTH USING and WITH CHECK"
    )
    assert re.search(r"\(\s*SELECT\s+auth\.uid\(\)\s*\)\s*=\s*user_id", body, re.IGNORECASE), (
        "0011 family_record UPDATE must match (SELECT auth.uid()) = user_id (the spine owner)"
    )


def test_0011_policies_are_only_insert_and_update_null_guarded() -> None:
    """D-RLS-2: 0011 adds exactly the two write policies, both null-guarded; no FOR ALL/DELETE."""
    sql = _apply_writes_sql()
    n_policies = len(_CREATE_POLICY.findall(sql))
    assert n_policies == 2, f"0011 should add exactly 2 policies, found {n_policies}"
    assert len(_FOR_INSERT.findall(sql)) == 1, "0011 should add exactly one FOR INSERT policy"
    assert len(_FOR_UPDATE.findall(sql)) == 1, "0011 should add exactly one FOR UPDATE policy"
    # The UPDATE policy carries USING + WITH CHECK (2 guards) + the INSERT WITH
    # CHECK (1 guard) = ≥3 null guards across the two policies.
    assert len(_NULL_GUARD.findall(sql)) >= n_policies, (
        "every 0011 policy must be null-guarded (D-RLS-2)"
    )
    assert not _FOR_ALL.search(sql), "0011 must not use FOR ALL"
    assert not _FOR_DELETE.search(sql), "0011 must not add a DELETE policy"
    assert not re.search(r"FOR\s+SELECT", sql, re.IGNORECASE), (
        "0011 must not add a SELECT policy (SELECT is governed by 0001/0009)"
    )


def test_0011_grants_insert_and_update_to_authenticated_only() -> None:
    """0011 grants INSERT(student) + UPDATE(family_record) to authenticated; never anon."""
    sql = _apply_writes_sql()
    grant_insert = re.search(r"GRANT\s+INSERT\s+ON\s+student\b[^;]*", sql, re.IGNORECASE)
    assert grant_insert, "0011 must GRANT INSERT ON student"
    assert re.search(r"\bauthenticated\b", grant_insert.group(0), re.IGNORECASE), (
        "0011 must grant INSERT(student) to authenticated"
    )
    assert not re.search(r"\banon\b", grant_insert.group(0), re.IGNORECASE), (
        "0011 must NOT grant INSERT(student) to anon (D-RLS-3)"
    )
    grant_update = re.search(r"GRANT\s+UPDATE\b[^;]*ON\s+family_record\b[^;]*", sql, re.IGNORECASE)
    assert grant_update, "0011 must GRANT UPDATE ON family_record"
    assert re.search(r"\bauthenticated\b", grant_update.group(0), re.IGNORECASE), (
        "0011 must grant UPDATE(family_record) to authenticated"
    )
    assert not re.search(r"\banon\b", grant_update.group(0), re.IGNORECASE), (
        "0011 must NOT grant UPDATE(family_record) to anon (D-RLS-3)"
    )


def test_0011_changes_nothing_else() -> None:
    """0011 is policy+grant-only: no CREATE TABLE / RLS toggle / DROP / SECURITY DEFINER."""
    sql = _apply_writes_sql()
    assert not _CREATE_TABLE.search(sql), (
        "0011 must not create a table (RLS/FORCE counts unchanged)"
    )
    assert not _ENABLE_RLS.search(sql), "0011 must not re-toggle RLS (already enabled)"
    assert not _FORCE_RLS.search(sql), "0011 must not re-FORCE RLS (already forced)"
    assert not re.search(r"\bDROP\s+POLICY\b", sql, re.IGNORECASE), "0011 must not drop a policy"
    assert not re.search(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", sql, re.IGNORECASE), (
        "0011 must not disable RLS"
    )
    assert not _SECURITY_DEFINER.search(sql), "0011 must not use SECURITY DEFINER (D-RLS-7)"


# ---------------------------------------------------------------------------
# 0012 funding_state enum values — adds the two values the Python FundingState
# enum + live signal map can transition INTO ('selected_gt', 'reconfirmed') that
# 0001's funding_state enum is MISSING. Mirrors 0006's ADD VALUE IF NOT EXISTS
# idempotent house style + the transaction-ordering caveat. Adds no table, no
# policy, no RLS toggle.
# ---------------------------------------------------------------------------

_FUNDING_STATE_NEW_VALUES = ("selected_gt", "reconfirmed")


def test_0012_adds_funding_state_values_idempotently() -> None:
    """0012 ADDs 'selected_gt' + 'reconfirmed' to funding_state via ADD VALUE IF NOT EXISTS."""
    sql = _funding_state_enum_sql()
    for value in _FUNDING_STATE_NEW_VALUES:
        assert re.search(
            rf"ALTER\s+TYPE\s+funding_state\s+ADD\s+VALUE\s+IF\s+NOT\s+EXISTS\s+'{value}'",
            sql,
            re.IGNORECASE,
        ), f"0012 must `ALTER TYPE funding_state ADD VALUE IF NOT EXISTS '{value}'`"


def test_0012_does_not_alter_rls_or_tables() -> None:
    """0012 is enum-only: no CREATE TABLE / CREATE POLICY / RLS toggle."""
    sql = _strip_comments(_funding_state_enum_sql())
    assert not _CREATE_TABLE.search(sql), "0012 must not create a table (enum-only)"
    assert not _CREATE_POLICY.search(sql), "0012 must not add a policy (enum-only)"
    assert not _ENABLE_RLS.search(sql), "0012 must not re-toggle RLS (enum-only)"


# ---------------------------------------------------------------------------
# 0013 sales_agent registry + family_record.assigned_rep_id (MULTI_AGENT_COCKPIT
# §3, PLAN.md M0 R1) — the DB-authoritative ownership seam. Adds:
#   * `sales_agent` — an N-configurable registry of demo agents (deterministic
#     seed of 2: #1 closer = the founder's seat, #2 setter), each with a stable
#     per-rank uuid, rank, synthetic_name (INV-1), tier (closer|setter), and a
#     simulated hubspot_owner_id (INV-9 live owner mirror). A new CREATE TABLE
#     that MUST ENABLE *and* FORCE RLS (D-RLS-1) and carry a null-guarded policy
#     (a registry every authenticated app user may read ⇒ `auth.uid() IS NOT
#     NULL`, the same guard shape as the source tables — keeps the global
#     CREATE==ENABLE==FORCE + one-guard-per-policy invariants green).
#   * `family_record.assigned_rep_id` — a NEW NULLABLE column, FK→sales_agent,
#     DISTINCT from `user_id`. `user_id` = the applicant family's RLS owner;
#     `assigned_rep_id` = the salesperson who owns the deal (NULL ⇒ unassigned /
#     the intake pool). Plus `assigned_at timestamptz`. This is PLAN.md M0's R1
#     risk: assigned_rep_id MUST NOT be a reuse of user_id.
# ---------------------------------------------------------------------------


def test_sales_agent_and_assigned_rep() -> None:
    """0013 adds the sales_agent registry (ENABLE+FORCE+null-guarded RLS), the
    distinct family_record.assigned_rep_id FK + assigned_at, and a deterministic
    2-agent (closer/setter) seed — keeping the global RLS count invariants green.
    """
    sql = _sales_agents_sql()

    # --- the sales_agent registry table ---
    assert re.search(r"CREATE\s+TABLE\s+sales_agent\b", sql, re.IGNORECASE), (
        "0013 must CREATE TABLE sales_agent"
    )
    assert re.search(
        r"ALTER\s+TABLE\s+sales_agent\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0013 must ENABLE RLS on sales_agent (D-RLS-1)"
    assert re.search(
        r"ALTER\s+TABLE\s+sales_agent\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0013 must FORCE RLS on sales_agent (owner-role escape hatch, D-RLS-1)"

    # At least one CREATE POLICY on sales_agent carrying the auth.uid() null guard.
    sales_agent_policy = re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+sales_agent\b[^;]*;", sql, re.IGNORECASE | re.DOTALL
    )
    assert sales_agent_policy, "0013 must add at least one CREATE POLICY on sales_agent"
    assert _NULL_GUARD.search(sales_agent_policy.group(0)), (
        "0013 sales_agent policy must carry the auth.uid() IS NOT NULL guard (D-RLS-2)"
    )

    # --- family_record.assigned_rep_id (FK→sales_agent, nullable) + assigned_at ---
    add_rep = re.search(
        r"ALTER\s+TABLE\s+family_record\s+ADD\s+COLUMN[^;]*\bassigned_rep_id\b[^;]*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert add_rep, "0013 must ALTER TABLE family_record ADD COLUMN assigned_rep_id"
    assert re.search(r"REFERENCES\s+sales_agent\b", add_rep.group(0), re.IGNORECASE), (
        "assigned_rep_id must be a FK REFERENCES sales_agent"
    )
    assert not re.search(r"\bNOT\s+NULL\b", add_rep.group(0), re.IGNORECASE), (
        "assigned_rep_id must be NULLABLE (NULL ⇒ unassigned / intake pool)"
    )
    assert re.search(
        r"ALTER\s+TABLE\s+family_record\s+ADD\s+COLUMN[^;]*\bassigned_at\b[^;]*timestamptz",
        sql,
        re.IGNORECASE | re.DOTALL,
    ), "0013 must ADD COLUMN assigned_at timestamptz on family_record"

    # --- assigned_rep_id is DISTINCT from user_id (R1) ---
    # The new column is named assigned_rep_id, NOT a reuse of user_id; the header
    # (un-stripped) documents user_id=applicant owner vs assigned_rep_id=rep.
    raw = (MIGRATIONS_DIR / "0013_sales_agents.sql").read_text(encoding="utf-8")
    assert re.search(r"\bassigned_rep_id\b", sql), "assigned_rep_id column must appear (DDL)"
    assert re.search(r"\buser_id\b", raw), (
        "user_id must be referenced (the distinct applicant owner — documented vs assigned_rep_id)"
    )
    assert not re.search(
        r"ADD\s+COLUMN[^;]*\bassigned_rep_id\b[^;]*\buser_id\b", add_rep.group(0), re.IGNORECASE
    ), "assigned_rep_id must NOT be defined as a reuse of user_id (R1: distinct columns)"

    # --- deterministic 2-agent seed (rank 1 closer, rank 2 setter) ---
    assert re.search(r"INSERT\s+INTO\s+sales_agent\b", sql, re.IGNORECASE), (
        "0013 must seed sales_agent rows"
    )
    assert re.search(r"\bcloser\b", sql, re.IGNORECASE), (
        "seed must include the closer tier (rank 1)"
    )
    assert re.search(r"\bsetter\b", sql, re.IGNORECASE), (
        "seed must include the setter tier (rank 2)"
    )
    # Two demo agents: either two INSERT statements or a multi-row VALUES with two
    # rank literals (1 and 2). Assert both ranks are present.
    assert re.search(r"\b1\b", sql), "seed must include rank 1 (the closer)"
    assert re.search(r"\b2\b", sql), "seed must include rank 2 (the setter)"
    # Idempotent seed.
    assert re.search(r"ON\s+CONFLICT", sql, re.IGNORECASE), (
        "0013 seed must be idempotent (ON CONFLICT DO NOTHING or equivalent)"
    )

    # --- doctrine: no FOR ALL, no SECURITY DEFINER ---
    assert not _SECURITY_DEFINER.search(sql), "0013 must not use SECURITY DEFINER (D-RLS-7)"


# ---------------------------------------------------------------------------
# 0014 sis_status — the SIS reconcile verdict table + the PII firewall
# (MULTI_AGENT_COCKPIT §6; TODO.md M5). The daily reconcile job (service_role)
# writes one row per family; the family status page reads its OWN row (anon+RLS).
# ---------------------------------------------------------------------------
def _sis_status_sql() -> str:
    """The 0014 sis_status verdict-table DDL (comments stripped)."""
    return _strip_comments((MIGRATIONS_DIR / "0014_sis_status.sql").read_text(encoding="utf-8"))


def test_sis_status_table_force_rls_and_pii_firewall() -> None:
    """0014 adds the sis_status verdict table (ENABLE+FORCE+null-guarded RLS)
    carrying ONLY the firewall fields — no child name/DOB/grade — plus the four
    bucket vocab in a CHECK, keeping the global RLS count invariants green.
    """
    sql = _sis_status_sql()

    # --- the verdict table + RLS + FORCE ---
    assert re.search(r"CREATE\s+TABLE\s+sis_status\b", sql, re.IGNORECASE), (
        "0014 must CREATE TABLE sis_status"
    )
    assert re.search(
        r"ALTER\s+TABLE\s+sis_status\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0014 must ENABLE RLS on sis_status (D-RLS-1)"
    assert re.search(
        r"ALTER\s+TABLE\s+sis_status\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE
    ), "0014 must FORCE RLS on sis_status (D-RLS-1)"

    # --- exactly the firewall columns; NO child / roster PII columns (INV-1/INV-6) ---
    for col in ("family_id", "present", "confirmed_at", "bucket"):
        assert re.search(rf"\b{col}\b", sql), f"sis_status must carry {col}"
    for forbidden in ("dob", "birth", "grade", "student", "child", "first_name", "last_name"):
        assert not re.search(rf"\b{forbidden}\b", sql, re.IGNORECASE), (
            f"sis_status must NOT carry roster/child PII ({forbidden}) — PII firewall (INV-1/INV-6)"
        )

    # --- the four reconcile buckets in a CHECK (vocab matches core SisBucket) ---
    for bucket in ("confirmed", "records_lag", "paid_not_in_sis", "ambiguous"):
        assert re.search(rf"'{bucket}'", sql), f"sis_status bucket CHECK must include '{bucket}'"

    # --- owner-scoped, null-guarded, READ-ONLY policy; client writes deny-by-default ---
    policy = re.search(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+sis_status\b[^;]*;", sql, re.IGNORECASE | re.DOTALL
    )
    assert policy, "0014 must add a CREATE POLICY on sis_status"
    assert _NULL_GUARD.search(policy.group(0)), (
        "0014 sis_status policy must carry the auth.uid() IS NOT NULL guard (D-RLS-2)"
    )
    assert re.search(r"\bFOR\s+SELECT\b", policy.group(0), re.IGNORECASE), (
        "the sis_status policy is read-only (writes are server-side service_role, D-RLS-4)"
    )
    assert not re.search(r"FOR\s+(INSERT|UPDATE|DELETE|ALL)\b", sql, re.IGNORECASE), (
        "sis_status must have no anon/authenticated write policy (writes via service_role)"
    )
    assert not _SECURITY_DEFINER.search(sql), "0014 must not use SECURITY DEFINER (D-RLS-7)"

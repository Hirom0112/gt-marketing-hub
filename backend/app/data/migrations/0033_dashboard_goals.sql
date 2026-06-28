-- 0033_dashboard_goals.sql — Module 6 Phase 3: the leadership-editable KPI goals store
-- + its append-only change log. Two program-scoped tables: the per-KPI scorecard
-- TARGETS leadership sets, and the immutable change-log of every edit.
--
-- Authoritative source: CLAUDE.md §1 (INV-5 deny-by-default RLS + service_role
-- server-only, INV-11 one canonical home — the SPEC-DEFAULT targets live in
-- app/data/goals_store.py DEFAULT_GOALS, NOT here; this is schema-only), THREAT_MODEL.md
-- §6 (D-RLS-1…7 — notably D-RLS-7: no definer-rights helper in the exposed schema),
-- app/core/program.py (the canonical Program enum), 0024 (the program-tenancy doctrine),
-- and 0010/0015/0028/0030 (the append-only-ledger / event doctrine). Mirrors the
-- 0030_budget.sql leadership-definition-table + append-only-event shape exactly.
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0030 + 0010/0015/0028
-- doctrine.
-- ===========================================================================
--   (A) `dashboard_goal` — the leadership-set per-KPI scorecard target. `key` is the
--       KPI key (one of the nine scorecard KPIs); `target` is the numeric goal;
--       `updated_by`/`updated_at` stamp the last edit. UNIQUE (program_id, key) so a
--       set is an in-place UPSERT per program (the layouts 0029 merge-duplicates
--       posture). SCHEMA-ONLY: the spec-default targets are SEEDED in the app
--       (DEFAULT_GOALS, INV-11 — the targets' one canonical home is goals_store.py),
--       NOT written here. A leadership DEFINITION table — not family-owned data — so
--       its read policy is the NULL-GUARDED `auth.uid() IS NOT NULL` shape (leadership
--       reads via the API require_role gate; the privileged edit path is service_role).
--
--   (B) `dashboard_goal_event` — the APPEND-ONLY change log: one row per edit.
--       `old_target`/`new_target` capture the transition, `changed_by` the actor,
--       `note` an optional reason. Immutable once written (the 0010/0015/0028 posture):
--       GRANT only SELECT + INSERT, NO UPDATE/DELETE grant or policy.
--
--   (C) BOTH tables are PROGRAM-SCOPED (goals belong to a program): each carries
--       `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the canonical
--       Program.FALL_ENROLLMENT, app/core/program.py, INV-11) and the 0024
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` JWT claim AND carrying the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3) — AND-ed on top
--       of the permissive policies (isolation tightens, never loosens). NOT NULL DEFAULT
--       (not nullable) so the RESTRICTIVE `app_metadata.program_id == program_id`
--       predicate correctly bounds the rows (a nullable program_id would never match).
--
--   (D) RLS: each table both turns on AND forces row-level security (D-RLS-1), and
--       EVERY policy carries the auth.uid() null guard (D-RLS-2). This keeps the global
--       CREATE==enable==force + one-guard-per-policy invariants (test_migrations_rls)
--       green (this migration adds +2 tables / +2 enable / +2 force) while anon
--       (auth.uid() = NULL) matches no row.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + privileged
-- goal-edit path and is unaffected by RLS/force; it is never client-exposed (INV-5).
-- D-RLS-7: this migration defines NO definer-rights function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) dashboard_goal — the leadership-set per-KPI target. Program-scoped. A leadership
-- definition table (not family-owned data): the null-guarded read, not owner-scoping,
-- governs reads. Schema-only — the spec defaults seed from the app (INV-11).
-- ---------------------------------------------------------------------------
CREATE TABLE dashboard_goal (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The KPI key (one of the nine scorecard KPIs, app/data/goals_store.py GOAL_KEYS).
    key        text NOT NULL,

    -- The leadership-set numeric target for this KPI (fractional rates AND counts ⇒
    -- double precision, not the bigint the budget allocations use).
    target     double precision NOT NULL,

    -- WHO last set it (a verified-principal reference — a uid/role token, never a name).
    updated_by text NOT NULL,

    updated_at timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). NOT NULL DEFAULT pins existing/new rows to
    -- the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id text NOT NULL DEFAULT 'fall_enrollment',

    -- One target per KPI per program ⇒ a set is an in-place UPSERT (merge-duplicates).
    UNIQUE (program_id, key)
);

-- ---------------------------------------------------------------------------
-- (B) dashboard_goal_event — the APPEND-ONLY change log. Program-scoped. Immutable
-- once written (the 0010/0015/0028 posture).
-- ---------------------------------------------------------------------------
CREATE TABLE dashboard_goal_event (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The KPI key whose target changed.
    key         text NOT NULL,

    -- The transition: the target BEFORE and AFTER the change.
    old_target  double precision NOT NULL,
    new_target  double precision NOT NULL,

    -- WHO made the change (a verified-principal reference, never a name).
    changed_by  text NOT NULL,

    -- An optional free-form note / reason (PII-free, synthetic — INV-1).
    note        text,

    created_at  timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). NOT NULL DEFAULT (see header note C).
    program_id  text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE dashboard_goal ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboard_goal FORCE ROW LEVEL SECURITY;
ALTER TABLE dashboard_goal_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboard_goal_event FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive policies. A leadership read: a null-guarded SELECT (leadership reads via
-- the API require_role gate; the goal WRITE is privileged — service_role). Every policy
-- carries the (SELECT auth.uid()) IS NOT NULL guard (D-RLS-2/D-RLS-3): anon matches no
-- row, and the global one-guard-per-policy invariant stays green.
-- ===========================================================================

-- dashboard_goal: leadership read (null-guarded). No client write policy — the goal
-- edit is privileged and lands via the service_role (the API require_role gate).
CREATE POLICY dashboard_goal_authenticated_select ON dashboard_goal
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- dashboard_goal_event: leadership read of the change log (null-guarded). Append-only —
-- there is deliberately NO UPDATE/DELETE policy (the change-log row is immutable once
-- written). Writes land via the privileged service_role.
CREATE POLICY dashboard_goal_event_authenticated_select ON dashboard_goal_event
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0028/0030 pattern): the caller must
-- be authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so neither a
-- read nor a write can cross the program boundary; AND-ed on top of the permissive
-- policies above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY dashboard_goal_program_isolation ON dashboard_goal
    AS RESTRICTIVE
    FOR ALL
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY dashboard_goal_event_program_isolation ON dashboard_goal_event
    AS RESTRICTIVE
    FOR ALL
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

-- ===========================================================================
-- PostgREST role grants. dashboard_goal: SELECT (the policy gates WHO — any
-- authenticated, in-program). dashboard_goal_event: APPEND-ONLY — SELECT + INSERT only,
-- NO UPDATE/DELETE grant (the change-log row is immutable). All still bounded by RLS
-- (app_runtime is NOBYPASSRLS, 0024). service_role (server-only, BYPASSRLS) is the seed
-- + privileged goal-edit path and is unaffected.
-- ===========================================================================
GRANT SELECT ON dashboard_goal TO authenticated, app_runtime;
GRANT SELECT, INSERT ON dashboard_goal_event TO authenticated, app_runtime;

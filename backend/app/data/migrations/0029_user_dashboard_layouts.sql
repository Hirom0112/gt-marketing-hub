-- 0029_user_dashboard_layouts.sql — B3: the per-user composable-Home widget layout.
-- One row per auth user holding the React-Grid-Layout placement array, so each
-- operator's Home arrangement persists across sessions.
--
-- Authoritative source: TODO_v2.md §B3, PLAN_v2.md §B3, CLAUDE.md §1 (INV-5
-- deny-by-default RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7),
-- ASSUMPTIONS.md A-37 (operational/global tables are NOT program-partitioned), and
-- 0027_rbac.sql's user_roles (the user-scoped, null-guarded global-table pattern
-- this migration mirrors).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0027 user_roles doctrine.
-- ===========================================================================
--   (A) `user_dashboard_layouts` — one row per auth user. `user_id` (the auth user,
--       auth.uid()) is the PRIMARY KEY, so a user has at most one saved layout;
--       `layout` is the jsonb React-Grid-Layout placement array (PII-free,
--       synthetic — INV-1); `updated_at` stamps the last save.
--
--   (B) OWNER-scoped. A user reads/writes ONLY their own row, keyed on
--       (SELECT auth.uid()) = user_id AND carrying the (SELECT auth.uid()) IS NOT
--       NULL guard (D-RLS-2/D-RLS-3) — the same predicate as 0027's user_roles.
--       anon (auth.uid() = NULL) matches no row.
--
--   (C) MUTABLE, NOT append-only. A saved preference is overwritten in place, so —
--       unlike the immutable ledgers (0010/0015/0026) — this table carries an
--       owner-scoped UPDATE policy (plus SELECT + INSERT). The INSERT/UPDATE
--       WITH CHECK pins user_id = auth.uid() so a user cannot write a row for
--       another user.
--
--   (D) GLOBAL/cross-program (ASSUMPTIONS A-37). A layout is a personal preference,
--       not program-specific — so, like user_roles / sales_agent / security_event,
--       it carries NO program_id tag and NO restrictive program-isolation policy.
--
-- Doctrine: the table both turns on AND forces row-level security (D-RLS-1) and
-- every policy carries the auth.uid() null guard (D-RLS-2), keeping the global
-- CREATE==enable==force + one-guard-per-policy invariants (test_migrations_rls)
-- green (this migration adds +1 table / +1 enable / +1 force). No definer-rights
-- helper (D-RLS-7): the owner-scoping is inline. service_role (server-only,
-- bypass-rls, D-RLS-4) is unaffected and is never client-exposed (INV-5).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) user_dashboard_layouts — one row per auth user. user_id is the PRIMARY KEY
-- (one saved layout per user). GLOBAL (cross-program, A-37): no program_id.
-- ---------------------------------------------------------------------------
CREATE TABLE user_dashboard_layouts (
    -- The auth user the layout belongs to (auth.uid()). PRIMARY KEY ⇒ one row per
    -- user. No FK to auth.users, matching the family_record.user_id convention
    -- (RLS keys on auth.uid(), not a DB FK).
    user_id    uuid NOT NULL PRIMARY KEY,

    -- The React-Grid-Layout placement array (PII-free, synthetic — INV-1).
    layout     jsonb NOT NULL,

    updated_at timestamptz NOT NULL DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE user_dashboard_layouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_dashboard_layouts FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Owner-scoped, null-guarded policies (D-RLS-2/D-RLS-3). A user reads/writes ONLY
-- their own row; anon (auth.uid() = NULL) matches nothing. MUTABLE: SELECT + INSERT
-- + UPDATE (a saved preference is overwritten in place — there is deliberately NO
-- DELETE policy).
-- ---------------------------------------------------------------------------

-- A user reads ONLY their own saved layout.
CREATE POLICY user_dashboard_layouts_owner_select ON user_dashboard_layouts
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- A user inserts ONLY their own row. WITH CHECK pins user_id = auth.uid() so a user
-- cannot create a layout for another user.
CREATE POLICY user_dashboard_layouts_owner_insert ON user_dashboard_layouts
    FOR INSERT
    TO authenticated
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- A user updates ONLY their own row. USING gates the rows visible to update;
-- WITH CHECK gates the post-image so the owner cannot reassign user_id away — both
-- owner-scoped + null-guarded.
CREATE POLICY user_dashboard_layouts_owner_update ON user_dashboard_layouts
    FOR UPDATE
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- ===========================================================================
-- PostgREST role grants. SELECT/INSERT/UPDATE to the app roles (the owner-scoped
-- policies above gate WHICH rows — a user's own only); never to anon (D-RLS-3:
-- unauthenticated = no rows). NO DELETE grant (a layout is overwritten, not
-- deleted). All still bounded by RLS (app_runtime is NOBYPASSRLS, 0024).
-- service_role (server-only, bypass-rls) is unaffected.
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE ON user_dashboard_layouts TO authenticated, app_runtime;

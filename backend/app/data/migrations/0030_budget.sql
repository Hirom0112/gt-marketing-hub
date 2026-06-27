-- 0030_budget.sql — B4: the $365K Budget Tracker's two program-scoped tables. The
-- four budget rows (Grassroots / Content / Guerrilla / Ops) and the append-only
-- spend/commitment ledger that rolls up against them.
--
-- Authoritative source: TODO_v2.md §B4, PLAN_v2.md §B4, CLAUDE.md §1 (INV-5
-- deny-by-default RLS + service_role server-only, INV-11 one canonical home — the
-- dollar allocations live in params/params.yaml, NOT here), THREAT_MODEL.md §6
-- (D-RLS-1…7 — notably D-RLS-7: no definer-rights helper in the exposed schema),
-- app/core/program.py (the canonical Program enum), 0024 (the program-tenancy
-- doctrine) and 0010/0015/0026 (the append-only-ledger doctrine).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0026 + 0010/0015
-- doctrine.
-- ===========================================================================
--   (A) `budget_workstream` — the four budget rows of the tracker. `name` is the
--       UNIQUE workstream key (grassroots / content / guerrilla / ops); `planned_usd`
--       is the allocated budget. SCHEMA-ONLY: the four allocations are SEEDED from
--       params at app boot (INV-11 — the dollar amounts' one canonical home is
--       params/params.yaml), NOT written here (the 0026 schema-only posture). A
--       leadership DEFINITION table — not family-owned data — so its read policy is
--       the NULL-GUARDED `auth.uid() IS NOT NULL` shape (leadership reads via the
--       API require_role gate). v1 seeds the allocation from params, so the
--       (privileged) write path is service_role; no client write policy.
--
--   (B) `budget_entry` — the APPEND-ONLY spend/commitment ledger: one row per line
--       item. `kind` is recommended / planned / committed / actual; `amount_usd` is
--       the line amount; `workstream` keys the owning budget_workstream.name. One
--       fact per line item, immutable once written (the 0010/0015/0026 posture):
--       GRANT only SELECT + INSERT, NO UPDATE/DELETE grant or policy.
--
--   (C) BOTH tables are PROGRAM-SCOPED (a budget belongs to a program): each carries
--       `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the canonical
--       Program.FALL_ENROLLMENT, app/core/program.py, INV-11) and the 0024
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` JWT claim AND carrying the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3) — AND-ed on
--       top of the permissive policies (isolation tightens, never loosens). NOTE:
--       budget_entry's program_id is NOT NULL DEFAULT (NOT the nullable column the
--       task sketch listed) — a nullable program_id would never match the
--       RESTRICTIVE `app_metadata.program_id == program_id` predicate (NULL = x is
--       not true), so the row would be invisible; NOT NULL DEFAULT keeps the
--       ledger correctly isolated, mirroring 0028's decision_event.
--
--   (D) RLS: each table both turns on AND forces row-level security (D-RLS-1), and
--       EVERY policy carries the auth.uid() null guard (D-RLS-2). This keeps the
--       global create==enable==force + one-guard-per-policy invariants
--       (test_migrations_rls) green (this migration adds +2 tables / +2 enable /
--       +2 force) while anon (auth.uid() = NULL) matches no row.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + privileged
-- budget-edit path and is unaffected by RLS/force; it is never client-exposed
-- (INV-5). D-RLS-7: this migration defines NO definer-rights function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) budget_workstream — the four budget rows. Program-scoped. A leadership
-- definition table (not family-owned data): the null-guarded read, not
-- owner-scoping, governs reads. Schema-only — allocations seed from params.
-- ---------------------------------------------------------------------------
CREATE TABLE budget_workstream (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The UNIQUE workstream key (grassroots / content / guerrilla / ops).
    name        text NOT NULL UNIQUE,

    -- The allocated budget for this workstream (whole US dollars). Seeded from
    -- params at app boot (INV-11) — NOT written by this schema-only migration.
    planned_usd bigint NOT NULL,

    created_at  timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). NOT NULL DEFAULT pins existing/new rows
    -- to the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id  text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (B) budget_entry — the APPEND-ONLY spend/commitment ledger. Program-scoped.
-- Immutable once written (the 0010/0015/0026 posture).
-- ---------------------------------------------------------------------------
CREATE TABLE budget_entry (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The owning workstream (keys budget_workstream.name, the UNIQUE workstream key).
    workstream text NOT NULL REFERENCES budget_workstream (name),

    -- The line-item kind: recommended / planned / committed / actual.
    kind       text NOT NULL
        CHECK (kind IN ('recommended', 'planned', 'committed', 'actual')),

    -- The line amount (whole US dollars).
    amount_usd bigint NOT NULL,

    -- An optional free-form note (PII-free, synthetic — INV-1).
    note       text,

    created_at timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). NOT NULL DEFAULT (see header note C) so
    -- the RESTRICTIVE program-isolation policy correctly bounds the ledger.
    program_id text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE budget_workstream ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_workstream FORCE ROW LEVEL SECURITY;
ALTER TABLE budget_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_entry FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive policies. A leadership read: a null-guarded SELECT (leadership reads
-- via the API require_role gate; budget WRITE is privileged — service_role). Every
-- policy carries the (SELECT auth.uid()) IS NOT NULL guard (D-RLS-2/D-RLS-3): anon
-- matches no row, and the global one-guard-per-policy invariant stays green.
-- ===========================================================================

-- budget_workstream: leadership read (null-guarded). No client write policy — the
-- v1 allocation is seeded from params and edited via the privileged service_role.
CREATE POLICY budget_workstream_authenticated_select ON budget_workstream
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- budget_entry: leadership read of the ledger (null-guarded). Append-only — there
-- is deliberately NO UPDATE/DELETE policy (the ledger row is immutable once
-- written). Writes land via the privileged service_role (the API require_role gate).
CREATE POLICY budget_entry_authenticated_select ON budget_entry
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0026/0028 pattern): the caller
-- must be authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so
-- neither a read nor a write can cross the program boundary; AND-ed on top of the
-- permissive policies above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY budget_workstream_program_isolation ON budget_workstream
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

CREATE POLICY budget_entry_program_isolation ON budget_entry
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
-- PostgREST role grants. budget_workstream: SELECT (the policy gates WHO — any
-- authenticated, in-program). budget_entry: APPEND-ONLY — SELECT + INSERT only, NO
-- UPDATE/DELETE grant (the ledger row is immutable). All still bounded by RLS
-- (app_runtime is NOBYPASSRLS, 0024). service_role (server-only, BYPASSRLS) is the
-- seed + privileged budget-edit path and is unaffected.
-- ===========================================================================
GRANT SELECT ON budget_workstream TO authenticated, app_runtime;
GRANT SELECT, INSERT ON budget_entry TO authenticated, app_runtime;

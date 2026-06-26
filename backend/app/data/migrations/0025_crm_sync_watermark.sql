-- 0025_crm_sync_watermark.sql — A2: durable per-program incremental-poll state
-- for the CRM poller.
--
-- Authoritative source: PLAN_v2.md §A2, TODO_v2.md §A2, CLAUDE.md §1 (INV-5
-- deny-by-default RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7),
-- app/core/program.py (the canonical Program enum), 0024_program_isolation.sql
-- (the program-tenancy doctrine this table follows).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024 doctrine.
-- ===========================================================================
-- The CRM incremental poller (built separately) needs durable per-program state:
-- the last-synced HubSpot `hs_lastmodifieddate` watermark, one row per
-- (program_id, object_type). On each poll it READS the watermark, pulls records
-- modified STRICTLY AFTER it, and ADVANCES it to the max `hs_lastmodifieddate`
-- seen. A null watermark means the object type has never been synced (a cold full
-- pull). This is operational poller state, not family-owned data.
--
--   (A) `crm_sync_watermark` — one row per (program_id, object_type). The
--       `program_id text NOT NULL DEFAULT 'fall_enrollment'` tenancy tag matches
--       0024's convention: the literal 'fall_enrollment' is the canonical
--       Program.FALL_ENROLLMENT value (app/core/program.py — INV-11's one home for
--       the program vocabulary; a SQL migration cannot read params/params.yaml).
--       A UNIQUE(program_id, object_type) makes the read/advance an idempotent
--       upsert key.
--
--   (B) RLS: `ENABLE` AND `FORCE` (D-RLS-1). Following A1 (0024), the single policy
--       is the `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` JWT claim AND carrying the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3): the rule is
--       "authenticated AND in-program". This keeps the global
--       CREATE==ENABLE==FORCE + one-guard-per-policy invariants (test_migrations_rls)
--       green while anon (auth.uid() = NULL) matches no row.
--
-- The poller writes server-side via the `service_role` (BYPASSRLS, server-only,
-- D-RLS-4) — never client-exposed (INV-5). Doctrine preserved: no security-definer
-- helper in the exposed schema (D-RLS-7).
--
-- CRITICAL (test_migrations_rls): this new table MUST ENABLE *and* FORCE
-- row-level security (the table count must equal the enable count and the force
-- count across all migrations) and its policy carries the auth.uid() null guard.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- crm_sync_watermark — per-(program_id, object_type) incremental-poll state.
-- Operational poller state (not family-owned); program-tenanted like 0024.
-- ---------------------------------------------------------------------------
CREATE TABLE crm_sync_watermark (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Program tenancy tag (matches 0024). The NOT NULL DEFAULT pins existing/new
    -- rows to the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id            text NOT NULL DEFAULT 'fall_enrollment',

    -- The HubSpot object type this watermark tracks (e.g. 'deal', 'contact').
    object_type           text NOT NULL,

    -- The last-synced HubSpot `hs_lastmodifieddate`. NULL ⇒ never synced (the
    -- poller does a cold full pull and then advances this to the max seen).
    watermark_modified_at timestamptz,

    -- Row update stamp, advanced on each watermark advance.
    updated_at            timestamptz NOT NULL DEFAULT now(),

    -- One watermark row per (program, object type) — the upsert key.
    UNIQUE (program_id, object_type)
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the policy (the test asserts FORCE-count == table-count).
ALTER TABLE crm_sync_watermark ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_sync_watermark FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policy (the 0024 pattern): the caller must be
-- authenticated (auth.uid() null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING (read/update/delete
-- visibility) + WITH CHECK (insert/update post-image) so neither a read nor a
-- write can cross the program boundary. service_role (BYPASSRLS, server-only) is
-- the poller's write path and is unaffected.
-- ---------------------------------------------------------------------------
CREATE POLICY crm_sync_watermark_program_isolation ON crm_sync_watermark
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
-- PostgREST role grants. Minimal privileges to the least-privilege API role
-- (0024's app_runtime, created NOBYPASSRLS) — all still bounded by RLS. The poller
-- writes via service_role (server-only, BYPASSRLS), which is unaffected.
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON crm_sync_watermark TO app_runtime;

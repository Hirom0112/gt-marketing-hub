-- 0041_crm_ops.sql — Module 7 (CRM / Marketing Operations): the program-scoped
-- persistence behind the CRM-Ops surface — the data-quality issue queue and the CRM
-- fix log.
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real
-- PII, INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical
-- home — the category/kind/severity/status LABELS live in params/app-layer, NOT here),
-- THREAT_MODEL.md §6 (D-RLS-1…7), app/core/program.py (the canonical Program enum),
-- and 0035/0039/0040 (the program-tenancy + deny-by-default RLS doctrine this migration
-- mirrors EXACTLY).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0040 doctrine.
-- ===========================================================================
--   (A) `data_quality_issue` — one persisted data-quality issue. AUTO-detected issues
--       carry a deterministic `signature` (entity_ref + kind) so a rescan UPSERTS
--       (never duplicates) and existing acknowledged/resolved rows keep their status.
--       MANUAL issues are filed by an owner. Every value is synthetic/aggregate (INV-1);
--       `entity_ref` is a synthetic family/entity token, never PII.
--   (B) `crm_fix_log` — one applied CRM-Ops fix (a UTM normalization or a scoring-model
--       change). The honest change log the source-tracking + lead-scoring views render.
--
--   PROGRAM-SCOPED: every table carries `program_id text NOT NULL DEFAULT
--   'fall_enrollment'` (the canonical Program.FALL_ENROLLMENT) + the 0024/0035
--   `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--   `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL` null
--   guard (D-RLS-2/D-RLS-3).
--
--   RLS: ENABLE AND FORCE row-level security on EVERY table (D-RLS-1), and EVERY
--   policy carries the auth.uid() null guard (D-RLS-2). This migration adds +2 tables
--   / +2 enable / +2 force / +4 null-guarded policies so the global
--   create==enable==force + one-guard-per-policy invariants (test_migrations_rls)
--   stay green.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + CRM-Ops write
-- path (the API require_role/owner gate) and is unaffected by RLS/force; it is never
-- client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) data_quality_issue — one persisted data-quality issue (auto or manual).
-- ---------------------------------------------------------------------------
CREATE TABLE data_quality_issue (
    issue_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The deterministic dedup key (entity_ref + kind for auto issues; a unique token for
    -- manual ones). A rescan UPSERTS on this so an issue is never duplicated.
    signature         text NOT NULL,

    -- The lane the issue belongs to. CHECK is the DB backstop; the app-layer is the home.
    category          text NOT NULL DEFAULT 'other'
        CHECK (category IN ('utm', 'sync', 'scoring', 'tracking', 'other')),

    -- The data_quality core's issue kind (conflict / utm_broken / … / a manual kind).
    kind              text NOT NULL DEFAULT '',

    severity          text NOT NULL DEFAULT 'medium',
    description       text NOT NULL DEFAULT '',

    -- The owning workstream/operator routing token (not PII); 'crm'.
    owner             text NOT NULL DEFAULT 'crm',

    status            text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),

    source            text NOT NULL DEFAULT 'auto'
        CHECK (source IN ('auto', 'manual')),

    -- A SYNTHETIC entity token (e.g. a family id), NEVER PII (INV-1/INV-6).
    entity_ref        text NOT NULL DEFAULT '',

    priority          text NOT NULL DEFAULT 'normal',

    created_at        timestamptz NOT NULL DEFAULT now(),
    resolved_at       timestamptz,
    resolution        text NOT NULL DEFAULT '',
    resolved_by       text NOT NULL DEFAULT '',

    program_id        text NOT NULL DEFAULT 'fall_enrollment',

    -- The signature is unique PER PROGRAM so the upsert dedups within a tenant.
    UNIQUE (program_id, signature)
);

ALTER TABLE data_quality_issue ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_quality_issue FORCE ROW LEVEL SECURITY;

CREATE POLICY data_quality_issue_authenticated_select ON data_quality_issue
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY data_quality_issue_program_isolation ON data_quality_issue
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

GRANT SELECT ON data_quality_issue TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (B) crm_fix_log — one applied CRM-Ops fix (UTM normalization or scoring change).
-- ---------------------------------------------------------------------------
CREATE TABLE crm_fix_log (
    fix_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    kind              text NOT NULL
        CHECK (kind IN ('utm_fix', 'scoring_change')),

    summary           text NOT NULL DEFAULT '',

    -- The verified actor token (not PII; the JWT sub or role).
    actor             text NOT NULL DEFAULT '',

    applied_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE crm_fix_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_fix_log FORCE ROW LEVEL SECURITY;

CREATE POLICY crm_fix_log_authenticated_select ON crm_fix_log
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY crm_fix_log_program_isolation ON crm_fix_log
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

GRANT SELECT ON crm_fix_log TO authenticated, app_runtime;

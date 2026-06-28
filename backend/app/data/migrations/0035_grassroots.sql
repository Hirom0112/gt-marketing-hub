-- 0035_grassroots.sql — Module 2 (Grassroots Engine): the four program-scoped
-- tables behind the Grassroots ambassador/referral/market-map/events surface.
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate adult data only —
-- NO real PII, INV-5 deny-by-default RLS + service_role server-only, INV-11 one
-- canonical home — the goal TARGETS + market-map CATEGORIES live in
-- params/params.yaml, NOT here), THREAT_MODEL.md §6 (D-RLS-1…7), app/core/program.py
-- (the canonical Program enum), and 0024/0028/0030 (the program-tenancy +
-- deny-by-default RLS doctrine this migration mirrors exactly).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0030 doctrine.
-- ===========================================================================
--   (A) `ambassador`        — the Grassroots ambassador roster (synthetic/aggregate
--       adult data only — INV-1/INV-6, NO real PII). `status` is the pipeline stage
--       (prospect → outreached → onboarded → active → champion).
--   (B) `referral_sprint`   — a time-boxed referral push (a window + the families it
--       enlisted/identified/converted), the "sprint health" source.
--   (C) `market_node`       — one node of the community market map. `category` is an
--       AGGREGATE label only (parent groups / homeschool / chess / robotics / …),
--       NEVER a real org/person identity.
--   (D) `ambassador_event`  — a parent-led event (the SOURCE OF TRUTH the Field &
--       Events module reads READ-ONLY). `host_ambassador_id` FKs `ambassador`.
--
--   (E) All four are PROGRAM-SCOPED: each carries
--       `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the canonical
--       Program.FALL_ENROLLMENT, app/core/program.py, INV-11) and the 0024
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL`
--       null guard (D-RLS-2/D-RLS-3) — AND-ed on top of the permissive read policy.
--
--   (F) RLS: each table both ENABLEs AND FORCEs row-level security (D-RLS-1), and
--       EVERY policy carries the auth.uid() null guard (D-RLS-2). This keeps the
--       global create==enable==force + one-guard-per-policy invariants
--       (test_migrations_rls) green (this migration adds +4 tables / +4 enable /
--       +4 force / +8 null-guarded policies) while anon (auth.uid() = NULL) matches
--       no row.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + grassroots
-- write path (the API require_role/owner gate) and is unaffected by RLS/force; it is
-- never client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights
-- function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) ambassador — the Grassroots roster. Synthetic/aggregate adult data only
-- (INV-1/INV-6 — NO real PII): synthetic name/email (the @example.invalid sink),
-- aggregate segment/region labels. Program-scoped.
-- ---------------------------------------------------------------------------
CREATE TABLE ambassador (
    ambassador_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Synthetic display name (an adult, never a child; INV-1/INV-6).
    synthetic_name  text NOT NULL,
    -- Synthetic contact email (the @example.invalid sink; INV-1).
    synthetic_email text NOT NULL,
    -- Aggregate community segment / region labels (INV-6 — never precise minor geo).
    segment         text NOT NULL DEFAULT '',
    region          text NOT NULL DEFAULT '',

    -- The pipeline stage. CHECK mirrors the app-layer STAGES (INV-11 home is the app).
    status          text NOT NULL DEFAULT 'prospect'
        CHECK (status IN ('prospect', 'outreached', 'onboarded', 'active', 'champion')),

    -- Warm intros + peer-to-peer calls this ambassador is credited with (counters).
    intros          integer NOT NULL DEFAULT 0,
    p2p_calls       integer NOT NULL DEFAULT 0,

    -- The last-touch date (date-only; no time-of-day). NULL ⇒ never touched.
    last_touch      date,

    -- The owning workstream/operator label (a routing token, not PII).
    owner           text NOT NULL DEFAULT 'grassroots',

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    program_id      text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (B) referral_sprint — a time-boxed referral push. Program-scoped.
-- ---------------------------------------------------------------------------
CREATE TABLE referral_sprint (
    sprint_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    name                 text NOT NULL,
    window_start         date NOT NULL,
    window_end           date NOT NULL,

    ambassadors_enlisted integer NOT NULL DEFAULT 0,
    families_identified  integer NOT NULL DEFAULT 0,
    conversions          integer NOT NULL DEFAULT 0,

    status               text NOT NULL DEFAULT 'active'
        CHECK (status IN ('planned', 'active', 'closed')),

    created_at           timestamptz NOT NULL DEFAULT now(),

    program_id           text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (C) market_node — one node of the community market map. `category` is an
-- AGGREGATE label only (INV-1/INV-6). Program-scoped.
-- ---------------------------------------------------------------------------
CREATE TABLE market_node (
    node_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- An AGGREGATE category label (parent groups / homeschool / chess / robotics /
    -- debate / math circles / …) — NEVER a real org/person identity (INV-1).
    category        text NOT NULL,
    -- A synthetic/aggregate contact label (e.g. "Austin robotics parents list") —
    -- never a real person's name/contact (INV-1).
    contact_label   text NOT NULL DEFAULT '',

    status          text NOT NULL DEFAULT 'cold'
        CHECK (status IN ('cold', 'outreach', 'in_conversation', 'active', 'closed')),

    leads_generated integer NOT NULL DEFAULT 0,
    last_activity   date,
    owner           text NOT NULL DEFAULT 'grassroots',

    created_at      timestamptz NOT NULL DEFAULT now(),

    program_id      text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (D) ambassador_event — a parent-led event (the SOURCE OF TRUTH Field & Events
-- reads READ-ONLY). Program-scoped. `host_ambassador_id` FKs `ambassador`.
-- ---------------------------------------------------------------------------
CREATE TABLE ambassador_event (
    event_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    event_name            text NOT NULL,
    -- Nullable FK to the hosting ambassador (an event may have no tracked host).
    host_ambassador_id    uuid REFERENCES ambassador (ambassador_id),

    event_type            text NOT NULL DEFAULT 'coffee_chat'
        CHECK (event_type IN ('coffee_chat', 'qa', 'school_visit', 'virtual')),

    date                  date NOT NULL,
    -- An AGGREGATE location label (a city/area), never a precise address (INV-6).
    location_label        text NOT NULL DEFAULT '',

    rsvp_count            integer NOT NULL DEFAULT 0,
    attendance_count      integer NOT NULL DEFAULT 0,
    conversions_influenced integer NOT NULL DEFAULT 0,

    created_at            timestamptz NOT NULL DEFAULT now(),

    program_id            text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE ambassador ENABLE ROW LEVEL SECURITY;
ALTER TABLE ambassador FORCE ROW LEVEL SECURITY;
ALTER TABLE referral_sprint ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_sprint FORCE ROW LEVEL SECURITY;
ALTER TABLE market_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_node FORCE ROW LEVEL SECURITY;
ALTER TABLE ambassador_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE ambassador_event FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive read policies. Any authenticated, in-program principal may READ the
-- Grassroots surface (the cockpit reads via service_role; this null-guarded SELECT
-- is the RLS-compliant direct-read path). WRITES are privileged — service_role (the
-- API require_role/owner gate). Every policy carries the (SELECT auth.uid()) IS NOT
-- NULL guard (D-RLS-2/D-RLS-3): anon matches no row, and the global
-- one-guard-per-policy invariant stays green.
-- ===========================================================================
CREATE POLICY ambassador_authenticated_select ON ambassador
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY referral_sprint_authenticated_select ON referral_sprint
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY market_node_authenticated_select ON market_node
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY ambassador_event_authenticated_select ON ambassador_event
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0030 pattern): the caller must
-- be authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so
-- neither a read nor a write can cross the program boundary; AND-ed on top of the
-- permissive policies above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY ambassador_program_isolation ON ambassador
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

CREATE POLICY referral_sprint_program_isolation ON referral_sprint
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

CREATE POLICY market_node_program_isolation ON market_node
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

CREATE POLICY ambassador_event_program_isolation ON ambassador_event
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
-- PostgREST role grants. All four tables: SELECT for any authenticated, in-program
-- principal (the policy gates WHO). WRITES land via service_role (server-only,
-- BYPASSRLS — INV-5 / D-RLS-4); no client write grant. app_runtime is NOBYPASSRLS
-- (0024) so its reads stay bounded by the program-isolation policy.
-- ===========================================================================
GRANT SELECT ON ambassador TO authenticated, app_runtime;
GRANT SELECT ON referral_sprint TO authenticated, app_runtime;
GRANT SELECT ON market_node TO authenticated, app_runtime;
GRANT SELECT ON ambassador_event TO authenticated, app_runtime;

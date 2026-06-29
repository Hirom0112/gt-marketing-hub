-- 0037_summer_camp_v2.sql — Module 4 (Summer Camp) Phase 1: the dimensions the camp
-- cockpit needs on top of the 0032 registration tenant (signup channel, attendance,
-- registration recency) + the camp SESSION calendar (the weekly cohorts).
--
-- Authoritative source: app/core/program.py (the canonical Program.SUMMER_CAMP value),
-- 0032_summer_camp.sql (the camp tenant + program_id default + per-program RLS this
-- migration extends), 0035_grassroots.sql / 0036_content.sql (the deny-by-default,
-- null-guarded, program-scoped RLS doctrine mirrored EXACTLY here), CLAUDE.md §1
-- (INV-1 no PII, INV-5 deny-by-default RLS, INV-6 aggregate-only minors, INV-11 one
-- canonical home), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0032/0035/0036 doctrine.
-- ===========================================================================
--   (1) camp_registration gets THREE backfill-safe columns:
--         * registration_channel text — how the family signed up (word_of_mouth /
--           social / email / website). The LABELS' canonical home is
--           params.summer_camp.registration_channels (INV-11); no CHECK here.
--         * attended boolean DEFAULT false — whether the child attended. Camp is in
--           the FUTURE, so every row is honestly false in Phase 1 (the funnel surfaces
--           "Attended" as pending, never faked).
--         * registered_at timestamptz — WHEN the registration came in, for the
--           "registrations this week" recent-window count. Nullable + no default so a
--           backfill leaves historic rows untouched; the seed stamps a real spread.
--       ADD COLUMN IF NOT EXISTS so re-applying is a no-op. RLS on the existing table
--       is untouched (an ALTER adds no table, so the create==enable==force invariant
--       and the per-policy null-guard invariant — test_migrations_rls — stay green).
--
--   (2) camp_session — one weekly camp cohort (a campus running Aug 3–14 etc.). The
--       four sessions (3× two-week + 1× one-week; San Antonio is the one-week) power
--       the camp countdown (days_to_camp_start = earliest starts_on − now) and the
--       session calendar. PROGRAM-SCOPED exactly like 0032's camp tables:
--         (A) program_id text NOT NULL DEFAULT 'summer_camp' — Program.SUMMER_CAMP
--             (INV-11's one home; a SQL migration cannot read params/params.yaml,
--             exactly as 0032 pins the same literal inline).
--         (B) RLS ENABLE *and* FORCE (D-RLS-1) + TWO null-guarded policies
--             (D-RLS-2): a PERMISSIVE authenticated-read (the seat universe both camp
--             seats may read — there is no per-applicant owner column; these are
--             aggregate program rows the server reads) AND a RESTRICTIVE
--             program-isolation policy keyed on app_metadata.program_id (USING +
--             WITH CHECK), AND-ed on top — the IDENTICAL shape as 0032.
--
-- D-RLS-7: no SECURITY DEFINER helper (inline predicates only). service_role
-- (BYPASSRLS, server-only) is the seed/reconcile write path and is unaffected.
-- INV-1/INV-6: camp_session carries NO PII (aggregate campus + dates + seats only).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (1) camp_registration — net-new dimensions (backfill-safe, idempotent).
-- ---------------------------------------------------------------------------
ALTER TABLE camp_registration
    ADD COLUMN IF NOT EXISTS registration_channel text;          -- word_of_mouth | social | email | website (labels: params, INV-11)
ALTER TABLE camp_registration
    ADD COLUMN IF NOT EXISTS attended boolean NOT NULL DEFAULT false;  -- camp is future ⇒ honestly false in Phase 1
ALTER TABLE camp_registration
    ADD COLUMN IF NOT EXISTS registered_at timestamptz;          -- when the registration arrived (recent-window count)

-- ---------------------------------------------------------------------------
-- (2) camp_session — one weekly camp cohort per campus (the session calendar +
-- the countdown source). Program-scoped. Synthetic/aggregate (INV-1).
-- ---------------------------------------------------------------------------
CREATE TABLE camp_session (
    session_id   uuid PRIMARY KEY,
    campus       text NOT NULL REFERENCES campus (campus),
    starts_on    date NOT NULL,                          -- cohort start (e.g. 2026-08-03)
    ends_on      date NOT NULL,                          -- cohort end
    duration     text NOT NULL,                          -- '1wk' | '2wk'
    capacity     integer NOT NULL,                       -- seats for the cohort
    status       text NOT NULL DEFAULT 'scheduled',      -- scheduled | running | done
    program_id   text NOT NULL DEFAULT 'summer_camp',    -- Program.SUMMER_CAMP (INV-11)
    created_at   timestamptz DEFAULT now()
);

ALTER TABLE camp_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE camp_session FORCE ROW LEVEL SECURITY;

-- D-RLS-2: authenticated-read reference (the cohort calendar every camp seat may
-- read), null-guarded — the same shape as 0032's campus. anon matches no row (D-RLS-3).
CREATE POLICY camp_session_authenticated_read ON camp_session
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- D-RLS-2: RESTRICTIVE program isolation (AND-ed on top), mirroring 0032 exactly.
CREATE POLICY camp_session_program_isolation ON camp_session
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
-- PostgREST role grants. SELECT to anon/authenticated (policy-gated, null-guarded —
-- anon matches no row, D-RLS-3). NO INSERT/UPDATE/DELETE grant: session seeding is
-- the server-only service_role (BYPASSRLS) path, so rows stay deny-by-default for
-- clients (INV-5).
-- ===========================================================================
GRANT SELECT ON camp_session TO anon, authenticated;

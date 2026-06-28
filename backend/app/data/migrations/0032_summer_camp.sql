-- 0032_summer_camp.sql — D2: the summer-camp registration tenant tables.
--
-- Authoritative source: app/core/program.py (the canonical Program.SUMMER_CAMP value),
-- 0024_program_isolation.sql (the program_id + RESTRICTIVE per-program RLS pattern),
-- CLAUDE.md §1 (INV-1 no PII, INV-5 deny-by-default RLS, INV-6 aggregate-only minors,
-- INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0009/0013/0024 doctrine.
-- ===========================================================================
-- Summer camp is a SEPARATE program tenant (Program.SUMMER_CAMP) of the single
-- hardened database. Its registrations arrive from two overlapping sources
-- (summer.gt.school + a standalone registration form) and are reconciled WITHOUT
-- double-counting by the deterministic core (app/core/summer_reconcile.py). This
-- migration adds the two NET-NEW tenant tables that store those rows + the campus
-- capacity reference, each:
--
--   (A) tagged `program_id text NOT NULL DEFAULT 'summer_camp'` — the canonical
--       Program.SUMMER_CAMP token (INV-11's one home for the program vocabulary; a
--       SQL migration cannot read params/params.yaml, exactly as 0024 pins the
--       'fall_enrollment' literal inline). New rows default into the camp program.
--
--   (B) RLS `ENABLE` AND `FORCE` (D-RLS-1: a new table MUST do both so the global
--       CREATE==ENABLE==FORCE relation-count invariant — test_migrations_rls — stays
--       satisfied and even the table-owner role obeys the policies), carrying TWO
--       null-guarded policies each (one-guard-per-policy, D-RLS-2):
--         * a PERMISSIVE FOR SELECT policy gated only on `auth.uid() IS NOT NULL` —
--           an authenticated-read reference both program seats may read, the SAME
--           shape as 0013's sales_agent / 0015's security_event registries (there is
--           no per-applicant owner column here: these are aggregate program rows the
--           server reads, never per-child-owned rows), AND
--         * a RESTRICTIVE program-isolation policy, FOR ALL (USING + WITH CHECK),
--           requiring the caller's `app_metadata.program_id` claim to equal the row's
--           `program_id` — the IDENTICAL clause shape as 0024, AND-ed on top so a
--           caller needs BOTH "authenticated" AND "in MY program". WITH CHECK isolates
--           writes too — a caller cannot insert/relabel a row into another program.
--
-- INV-1 / INV-6 (no PII, aggregate-only minors): `camp_registration` carries ONLY a
-- synthetic household contact (synthetic_email/synthetic_phone) and an AGGREGATE
-- `child_grade_band` — NO child name / DOB / precise geo. All values are synthetic.
--
-- D-RLS-7: no SECURITY DEFINER helper (inline predicates only). service_role
-- (BYPASSRLS, server-only) is the seed/reconcile write path and is unaffected.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (1) campus — the per-campus capacity reference (the seat universe).
-- ---------------------------------------------------------------------------
CREATE TABLE campus (
    campus      text PRIMARY KEY,        -- campus name (e.g. 'Austin')
    city        text NOT NULL,           -- aggregate location label (no precise geo)
    capacity    integer NOT NULL,        -- seat capacity for the campus
    duration    text NOT NULL,           -- '1wk' | '2wk'
    program_id  text NOT NULL DEFAULT 'summer_camp'  -- Program.SUMMER_CAMP (INV-11)
);

ALTER TABLE campus ENABLE ROW LEVEL SECURITY;
ALTER TABLE campus FORCE ROW LEVEL SECURITY;

-- D-RLS-2: authenticated-read reference (the seat universe every camp seat may read),
-- null-guarded — the same shape as 0013's registry. anon matches no row (D-RLS-3).
CREATE POLICY campus_authenticated_read ON campus
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- D-RLS-2: RESTRICTIVE program isolation (AND-ed on top), mirroring 0024 exactly.
CREATE POLICY campus_program_isolation ON campus
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

-- ---------------------------------------------------------------------------
-- (2) camp_registration — one row per registration AS SEEN IN ONE SOURCE. The two
-- overlapping sources are deduped on the household identity key by the reconcile
-- core; this table is the storage grain (source + external_id is the source's own
-- opaque id). INV-1/INV-6: synthetic household contact + AGGREGATE grade band only —
-- NO child name / DOB / precise geo.
-- ---------------------------------------------------------------------------
CREATE TABLE camp_registration (
    registration_id   uuid PRIMARY KEY,
    source            text NOT NULL,   -- 'summer_site' | 'registration_form'
    external_id       text NOT NULL,   -- the source's own opaque id (NEVER child PII)
    campus            text NOT NULL REFERENCES campus (campus),
    child_grade_band  text NOT NULL,   -- AGGREGATE band ('K-2' …) — NEVER a child key (INV-6)
    synthetic_email   text,            -- household contact (synthetic; INV-1) — dedup key
    synthetic_phone   text,            -- household contact (synthetic; INV-1) — fallback key
    paid              boolean NOT NULL DEFAULT false,
    program_id        text NOT NULL DEFAULT 'summer_camp',  -- Program.SUMMER_CAMP (INV-11)
    created_at        timestamptz DEFAULT now()
);

ALTER TABLE camp_registration ENABLE ROW LEVEL SECURITY;
ALTER TABLE camp_registration FORCE ROW LEVEL SECURITY;

-- D-RLS-2: authenticated-read (the camp rollup is read across registrations server-
-- side; no per-applicant owner column), null-guarded. anon matches no row (D-RLS-3).
CREATE POLICY camp_registration_authenticated_read ON camp_registration
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- D-RLS-2: RESTRICTIVE program isolation (AND-ed on top), mirroring 0024 exactly —
-- one camp seat's rows can never leak into another program's view, reads or writes.
CREATE POLICY camp_registration_program_isolation ON camp_registration
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
-- anon matches no row, D-RLS-3). NO INSERT/UPDATE/DELETE grant to anon/authenticated:
-- registration ingest + seeding is the server-only service_role (BYPASSRLS) path, so
-- the rows stay deny-by-default for clients (INV-5).
-- ===========================================================================
GRANT SELECT ON campus TO anon, authenticated;
GRANT SELECT ON camp_registration TO anon, authenticated;

-- ---------------------------------------------------------------------------
-- Campus capacity seed (idempotent) — the four campuses (3× two-week, 1× one-week)
-- whose seats roll up to the fixed total of 350. Matches CAMPUS_CAPACITY in
-- app/data/synthetic_summer.py (INV-11: the seed source's one home; this DDL pins
-- the same numbers inline as a migration cannot read params/params.yaml).
-- ---------------------------------------------------------------------------
INSERT INTO campus (campus, city, capacity, duration) VALUES
    ('Austin',      'Mueller campus',          100, '2wk'),
    ('Dallas',      'Knox-Henderson campus',   100, '2wk'),
    ('Houston',     'Heights campus',           90, '2wk'),
    ('San Antonio', 'Pearl campus',             60, '1wk')
ON CONFLICT (campus) DO NOTHING;

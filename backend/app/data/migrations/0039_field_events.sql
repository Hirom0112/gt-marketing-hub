-- 0039_field_events.sql — Module 8 (Field Marketing & Events): the program-scoped
-- `field_event` table behind the GT-organized field-events surface (MANUAL ENTRY).
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real
-- PII, INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical
-- home — the event-type LABELS + the upcoming WINDOW live in params/params.yaml, NOT
-- here), THREAT_MODEL.md §6 (D-RLS-1…7), app/core/program.py (the canonical Program
-- enum), and 0035 (the program-tenancy + deny-by-default RLS doctrine this migration
-- mirrors EXACTLY).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0035 doctrine.
-- ===========================================================================
--   (A) `field_event` — one GT-ORGANIZED field-marketing event (a shadow day, a chess
--       tournament, an AMA, a community event, a festival, or a webinar). MANUAL
--       ENTRY: no external API feeds this — the Field & Events Owner logs each row and
--       its attendance/consults by hand. Aggregate venue label only (a city/area),
--       never a precise address (INV-6). DISTINCT from `ambassador_event` (0035, the
--       parent-led grassroots events the Field & Events module reads READ-ONLY): this
--       module OWNS + WRITES `field_event`, and only READS `ambassador_event`.
--
--   (B) PROGRAM-SCOPED: `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the
--       canonical Program.FALL_ENROLLMENT — GT-organized field events are part of the
--       main org, NOT summer_camp) + the 0024 `AS RESTRICTIVE` program-isolation
--       policy keyed on the caller's `app_metadata.program_id` claim WITH the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3).
--
--   (C) RLS: ENABLE AND FORCE row-level security (D-RLS-1), and EVERY policy carries
--       the auth.uid() null guard (D-RLS-2) — anon (auth.uid() = NULL) matches no row.
--       This migration adds +1 table / +1 enable / +1 force / +2 null-guarded policies
--       so the global create==enable==force + one-guard-per-policy invariants
--       (test_migrations_rls) stay green.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + field-event
-- write path (the API require_role/owner gate) and is unaffected by RLS/force; it is
-- never client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights
-- function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) field_event — the GT-organized field-marketing event. Synthetic/aggregate
-- data only (INV-1/INV-6): aggregate venue label, no PII, no precise address.
-- Program-scoped. MANUAL ENTRY — every counter (rsvp/attendance/consults) is hand-
-- logged, so the event-to-consult conversion is a MANUAL figure, never instrumented.
-- ---------------------------------------------------------------------------
CREATE TABLE field_event (
    event_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    event_name        text NOT NULL,

    -- The event type. CHECK mirrors the app-layer LABELS (the INV-11 home is
    -- params.field_events.event_types — this CHECK is the DB backstop, like 0035).
    event_type        text NOT NULL DEFAULT 'community_event'
        CHECK (event_type IN (
            'shadow_day', 'chess_tournament', 'ama', 'community_event', 'festival', 'webinar'
        )),

    -- An AGGREGATE venue label (a city/area/campus), never a precise address (INV-6).
    venue             text NOT NULL DEFAULT '',

    event_date        date NOT NULL,

    -- Hand-logged counters (MANUAL ENTRY). consults_booked is the manually-entered
    -- conversion figure; the event-to-consult rate is COMPUTED from it, never tracked.
    rsvp_count        integer NOT NULL DEFAULT 0,
    attendance_count  integer NOT NULL DEFAULT 0,
    consults_booked   integer NOT NULL DEFAULT 0,

    -- The lifecycle status. CHECK is the closed wire set (app-layer is the INV-11 home).
    status            text NOT NULL DEFAULT 'planning'
        CHECK (status IN ('planning', 'confirmed', 'completed', 'cancelled')),

    -- The owning workstream/operator label (a routing token, not PII). The field-events
    -- owner token is 'events' (the API owner gate compares against it).
    owner             text NOT NULL DEFAULT 'events',

    notes             text NOT NULL DEFAULT '',
    materials         text NOT NULL DEFAULT '',
    budget_usd        integer NOT NULL DEFAULT 0,

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE field_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE field_event FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive read policy. Any authenticated, in-program principal may READ the
-- Field & Events surface (the cockpit reads via service_role; this null-guarded
-- SELECT is the RLS-compliant direct-read path). WRITES are privileged — service_role
-- (the API require_role/owner gate). The (SELECT auth.uid()) IS NOT NULL guard
-- (D-RLS-2/D-RLS-3) keeps anon matching no row and the one-guard-per-policy invariant
-- green.
-- ===========================================================================
CREATE POLICY field_event_authenticated_select ON field_event
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policy (the 0024/0035 pattern): the caller must be
-- authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so neither
-- a read nor a write can cross the program boundary; AND-ed on top of the permissive
-- policy above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY field_event_program_isolation ON field_event
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
-- PostgREST role grants. SELECT for any authenticated, in-program principal (the
-- policy gates WHO). WRITES land via service_role (server-only, BYPASSRLS — INV-5 /
-- D-RLS-4); no client write grant. app_runtime is NOBYPASSRLS (0024) so its reads
-- stay bounded by the program-isolation policy.
-- ===========================================================================
GRANT SELECT ON field_event TO authenticated, app_runtime;

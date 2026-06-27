-- 0031_app_runtime_program_read.sql — A-38: enforce program isolation on the
-- app's OWN read path at the RLS layer (not just the app-layer filter).
--
-- Authoritative source: ASSUMPTIONS.md A-38 ("the connection swap"), PLAN_v2.md §A1
-- task 7, THREAT_MODEL.md §6 (D-RLS-4). CLAUDE.md §1 INV-5.
--
-- ===========================================================================
-- WHY THIS MIGRATION EXISTS
-- ===========================================================================
-- 0024 created the non-`BYPASSRLS` `app_runtime` role + a RESTRICTIVE per-program
-- isolation policy, but the backend still read over the `service_role` key (which
-- BYPASSES RLS), so program isolation on the app's own read path was enforced only
-- by the app-layer `program_id=eq.<active>` filter in `supabase_repository.py` —
-- NOT by RLS. A-38 closes that: the app read path moves onto a connection
-- authenticated as `app_runtime` carrying a JWT with the `app_metadata.program_id`
-- claim, so RLS is the boundary.
--
-- The cockpit is a TRUSTED SERVER that legitimately reads ACROSS families within
-- its program (the leadership/operator console — D-RLS-4's cross-family read path).
-- The existing owner-scoped PERMISSIVE policies (0001, `auth.uid() = user_id`) grant
-- the server NOTHING (it owns no rows), so under `app_runtime` + RLS the read path
-- would return ZERO rows. This migration adds the missing piece: a PERMISSIVE
-- SELECT policy, scoped TO the `app_runtime` role only, that grants read of every
-- row IN THE CALLER'S PROGRAM (the JWT `app_metadata.program_id` claim) — and only
-- that program. The 0024 RESTRICTIVE policy is AND-ed on top, so cross-PROGRAM rows
-- stay invisible even to `app_runtime`. The owner policies are untouched, so the
-- anon/authenticated end-user client paths (the apply SPA) keep their owner-only
-- isolation (D-RLS-2/5, the closed IDOR).
--
--   - PERMISSIVE, FOR SELECT, TO app_runtime  ⇒ applies ONLY to the server role,
--     never to anon/authenticated (their owner policies are unchanged).
--   - USING ((SELECT auth.uid()) IS NOT NULL  ⇒ the D-RLS-2 null guard (a tokenless
--     caller reads nothing) AND the claim matches the row's program_id.
--   - No INSERT/UPDATE/DELETE policy for app_runtime here: A-38 moves only the READ
--     path; writes remain on the server-only service_role seam (INV-5 / D-RLS-4).
--   - No SECURITY DEFINER, no change to the exposed-schema definer posture (D-RLS-7).
--
-- `GRANT app_runtime TO authenticator` lets PostgREST `SET ROLE app_runtime` when a
-- request's JWT carries `"role": "app_runtime"` (the Supabase request pipeline). The
-- 0024 table/sequence grants already give the role its CRUD privileges; RLS gates
-- the rows. Idempotent — safe to re-run.

-- ---------------------------------------------------------------------------
-- (A) Let the PostgREST authenticator assume the least-privilege server role.
-- ---------------------------------------------------------------------------
GRANT app_runtime TO authenticator;

-- ---------------------------------------------------------------------------
-- (B) One PERMISSIVE program-scoped read policy per A-37 program-scoped tenant
--     table, TO app_runtime only. Mirrors the 9-table set tagged in 0024.
-- ---------------------------------------------------------------------------
CREATE POLICY family_record_appruntime_program_read ON family_record
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY leads_new_appruntime_program_read ON leads_new
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY app_form_appruntime_program_read ON app_form
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY enrollment_forms_appruntime_program_read ON enrollment_forms
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY apply_events_appruntime_program_read ON apply_events
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY student_appruntime_program_read ON student
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY voucher_event_appruntime_program_read ON voucher_event
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY sis_status_appruntime_program_read ON sis_status
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY lead_assignment_appruntime_program_read ON lead_assignment
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

-- ---------------------------------------------------------------------------
-- (C) community_profiles is NOT program-tagged (no program_id) — ownership is
--     via its family (0001: family_id -> family_record.user_id). The live repo
--     reads it as an EMBED on the family/student select, so app_runtime needs a
--     program-scoped read here too, keyed through the owning family's program_id.
--     Mirrors the shape of the existing owner-scoped subquery policy, so it is a
--     proven-safe (non-recursive) reference to family_record.
-- ---------------------------------------------------------------------------
CREATE POLICY community_profiles_appruntime_program_read ON community_profiles
    AS PERMISSIVE FOR SELECT TO app_runtime
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.program_id = (SELECT auth.jwt() -> 'app_metadata' ->> 'program_id')
        )
    );

-- 0024_program_isolation.sql — A1: program isolation via a single hardened database.
--
-- Authoritative source: PLAN_v2.md §A1, TODO_v2.md §A1, CLAUDE.md §1 (INV-5
-- deny-by-default RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7),
-- app/core/program.py (the canonical Program enum), ASSUMPTIONS.md A-37 (the
-- program-scoped vs operational/global partition decision).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0001/0003 doctrine.
-- ===========================================================================
-- The cockpit runs MULTIPLE programs (fall enrollment, summer camp, …) out of ONE
-- hardened database. A1 makes a leaked *client* key provably containable ACROSS
-- programs: every family/enrollment tenant row is tagged with a `program_id`, and
-- a RESTRICTIVE policy keyed on the caller's `app_metadata.program_id` JWT claim
-- isolates one program's rows from another's. RESTRICTIVE policies are AND-ed with
-- the existing owner-scoped permissive policies (0001/0003/0007/…): a caller now
-- needs BOTH "I own this row (auth.uid())" AND "this row is in MY program" — the
-- isolation tightens, never loosens, the boundary.
--
--   (A) `program_id text NOT NULL DEFAULT 'fall_enrollment'` on each tenant table.
--       The NOT NULL DEFAULT atomically backfills every existing synthetic row to
--       the Fall program (existing data IS the Fall program). The literal
--       'fall_enrollment' is the canonical Program.FALL_ENROLLMENT value
--       (app/core/program.py — INV-11's one home for the program vocabulary; a SQL
--       migration cannot read params/params.yaml, exactly as 0014's bucket vocab
--       and 0017's income-tier vocab are pinned inline).
--
--   (B) One `AS RESTRICTIVE` policy per tenant table, FOR ALL (USING + WITH CHECK),
--       requiring the caller's `app_metadata.program_id` claim to equal the row's
--       `program_id`. Each clause ALSO carries the `(SELECT auth.uid()) IS NOT NULL`
--       null guard (D-RLS-2/D-RLS-3): the rule is "authenticated AND in-program",
--       and the global one-guard-per-policy invariant (test_migrations_rls) stays
--       satisfied. WITH CHECK isolates writes too — a caller cannot insert/relabel a
--       row into another program.
--
--   (C) `app_runtime` — a least-privilege Postgres role created WITH **NOBYPASSRLS**
--       that the API/app connects as. Because it does NOT bypass RLS, even the
--       SERVER path is RLS-bounded to its own program — a leaked or mis-scoped app
--       connection cannot cross programs. The true `service_role`/superuser (the
--       BYPASSRLS cross-program read path) is reserved for MIGRATIONS ONLY, kept in
--       a secrets manager and rotated (audit S4); it is never the app connection and
--       never lands in `.env` (LOCKED DECISION #1).
--
-- Doctrine preserved: this migration adds NO new relation (no table creation), so
-- the global CREATE==ENABLE==FORCE relation counts are unperturbed; it toggles no
-- RLS and drops no policy; it uses no security-definer helper (D-RLS-7). The owner-scoped
-- permissive policies from 0001/0003/0007/0009/0010/0011/0017 remain intact — the
-- RESTRICTIVE program policy is purely additive (AND-ed) on top.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) program_id tenancy tag on each family/enrollment tenant table. The NOT NULL
-- DEFAULT 'fall_enrollment' backfills existing synthetic rows to the Fall program.
-- ---------------------------------------------------------------------------
ALTER TABLE family_record    ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE leads_new        ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE app_form         ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE enrollment_forms ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE apply_events     ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE student          ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE voucher_event    ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE sis_status       ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';
ALTER TABLE lead_assignment  ADD COLUMN program_id text NOT NULL DEFAULT 'fall_enrollment';

-- ---------------------------------------------------------------------------
-- (B) RESTRICTIVE program-isolation policies. AND-ed on top of the owner-scoped
-- permissive policies: the caller must be authenticated (auth.uid() null guard,
-- D-RLS-3) AND in the row's program (app_metadata.program_id == program_id). FOR
-- ALL with USING (read/update/delete visibility) + WITH CHECK (insert/update
-- post-image) so neither a read nor a write can cross the program boundary.
-- ---------------------------------------------------------------------------
CREATE POLICY family_record_program_isolation ON family_record
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

CREATE POLICY leads_new_program_isolation ON leads_new
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

CREATE POLICY app_form_program_isolation ON app_form
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

CREATE POLICY enrollment_forms_program_isolation ON enrollment_forms
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

CREATE POLICY apply_events_program_isolation ON apply_events
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

CREATE POLICY student_program_isolation ON student
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

CREATE POLICY voucher_event_program_isolation ON voucher_event
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

CREATE POLICY sis_status_program_isolation ON sis_status
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

CREATE POLICY lead_assignment_program_isolation ON lead_assignment
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
-- (C) The least-privilege API connection role. NOBYPASSRLS ⇒ the server path is
-- RLS-bounded; NOLOGIN here because the connection wiring (granting a login/
-- password or SET ROLE membership + pointing `settings` at it) is a SEPARATE task
-- (TODO_v2 §A1 task 7). Idempotent so re-running the migration is safe. The true
-- service_role/superuser (BYPASSRLS) is MIGRATIONS-ONLY — secrets-managed, rotated,
-- never the app connection, never in `.env`.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
        CREATE ROLE app_runtime WITH NOBYPASSRLS NOLOGIN;
    END IF;
END
$$;

-- Minimal grants the API CRUD surface needs — all still bounded by RLS (the role
-- does NOT bypass it). service_role remains the only BYPASSRLS path (migrations).
GRANT USAGE ON SCHEMA public TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;

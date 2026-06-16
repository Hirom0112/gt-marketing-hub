-- 0003_apply_events.sql — S14 apply-stack replica: direct-write intake + drop-off
-- telemetry (ASSUMPTIONS.md A-24).
--
-- Authoritative source: ASSUMPTIONS.md A-24 (decision + apply_events), CLAUDE.md
-- §1 (INV-1/INV-5/INV-6), THREAT_MODEL.md §6 (D-RLS-1…7), §9 (minors/COPPA).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), all consistent with the 0001 doctrine.
-- ===========================================================================
-- The S14 mock apply SPA mirrors gtschool's REAL pattern: an *authenticated*
-- applicant (Supabase Auth — anon/email, the analogue of their OTP gate, which
-- yields an `auth.uid()`) writes their own application rows DIRECTLY to Supabase.
-- 0001 granted only SELECT, so writes were deny-by-default. This migration adds
-- the missing capability the apply flow needs, WITHOUT weakening the doctrine:
--
--   1. Owner-scoped, NULL-GUARDED *INSERT* policies on the spine + the four
--      gtschool source tables, so an authenticated applicant may insert ONLY
--      rows they own (`auth.uid() = user_id`, or family_id → an owned family).
--      The same `auth.uid() IS NOT NULL` guard as 0001 (D-RLS-2) — this is the
--      only doctrine-legal direct-write path (an unauthenticated anon-insert
--      policy would fail `test_migrations_rls`'s null-guard assertion).
--   2. A new `apply_events` table for per-field / per-screen DROP-OFF telemetry
--      — the depth HubSpot can't show. METADATA ONLY: which step, which field,
--      which event, how long. NO typed values / content column, and NO child
--      key (INV-1/INV-6/COPPA; we deliberately do not add to GT's already-heavy
--      webcam/keystroke monitoring footprint). Owner-scoped + null-guarded like
--      everything else.
--   3. Synthetic-email CHECK constraints (INV-1 backstop): persisted identity
--      emails MUST be the synthetic reserved domain, so even a malformed client
--      write cannot land a real address. The generator already emits
--      `@example.invalid`, so existing seed data satisfies the constraint.
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) remains the cockpit's only
-- read path and reads across families; anon (unauthenticated) still matches no
-- rows under any policy (D-RLS-3).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (3) INV-1 backstop: persisted identity emails must be synthetic.
-- ---------------------------------------------------------------------------
ALTER TABLE family_record
    ADD CONSTRAINT family_record_synthetic_email
    CHECK (primary_contact_synthetic_email LIKE '%@example.invalid');

ALTER TABLE leads_new
    ADD CONSTRAINT leads_new_synthetic_email
    CHECK (synthetic_email LIKE '%@example.invalid');

-- ---------------------------------------------------------------------------
-- (1) Owner-scoped, null-guarded INSERT policies (D-RLS-2). The authenticated
--     applicant may insert ONLY rows they own.
-- ---------------------------------------------------------------------------

-- Spine: the applicant creates their own family_record (user_id = auth.uid()).
-- Stored `current_stage` is a write-time placeholder; the cockpit re-derives
-- stage on read (A-24 M2), so it is not authoritative here.
CREATE POLICY family_record_owner_insert ON family_record
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- Source tables: ownership scoped through the owned family_record, null-guarded.
CREATE POLICY leads_new_owner_insert ON leads_new
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY app_form_owner_insert ON app_form
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY enrollment_forms_owner_insert ON enrollment_forms
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY community_profiles_owner_insert ON community_profiles
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ---------------------------------------------------------------------------
-- (2) apply_events — per-field / per-screen drop-off telemetry. METADATA ONLY.
-- ---------------------------------------------------------------------------

-- Closed token set (house style: deterministic enums). No "value"/content token.
CREATE TYPE apply_event_type AS ENUM (
    'step_viewed',
    'step_completed',
    'field_focused',
    'field_left_empty',
    'validation_error_shown',
    'last_step_before_exit'
);

CREATE TABLE apply_events (
    event_id        uuid PRIMARY KEY,
    -- Ownership scoping (D-RLS-2), NOT a child key: family_id is the parent/
    -- applicant family, never a student. There is intentionally NO student/child
    -- column and NO typed-value/content column (INV-1/INV-6/COPPA).
    family_id       uuid NOT NULL REFERENCES family_record (family_id),
    step            text NOT NULL,              -- which screen/step (e.g. 'enroll.form3')
    field_key       text,                       -- which field (nullable for step-level events)
    event_type      apply_event_type NOT NULL,  -- interaction kind (closed set above)
    time_on_step_ms int,                        -- how long on the step before this event
    occurred_at     timestamptz DEFAULT now()
);

ALTER TABLE apply_events ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: owner-scoped read via family_record.user_id, null-guarded (D-RLS-3).
CREATE POLICY apply_events_owner_select ON apply_events
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- D-RLS-2: the authenticated applicant emits events ONLY for their own family.
CREATE POLICY apply_events_owner_insert ON apply_events
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- PostgREST role grants. INSERT is granted to `authenticated` ONLY (anon, being
-- unauthenticated, has auth.uid() = NULL and matches no WITH CHECK — and is not
-- granted INSERT at all). SELECT on apply_events follows the 0001 pattern
-- (policy-gated, null-guarded). `service_role` (server-only) is unaffected and
-- remains the cockpit's cross-family read path.
-- ===========================================================================

GRANT INSERT ON
    family_record, leads_new, app_form, enrollment_forms, community_profiles,
    apply_events
TO authenticated;

GRANT SELECT ON apply_events TO anon, authenticated;

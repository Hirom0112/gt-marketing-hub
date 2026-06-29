-- 0040_nurture.sql — Module 5 (Nurture & Lifecycle): the program-scoped synthetic
-- mirror behind the Nurture surface — segments, sequence mirror, SMS inbox, and the
-- SLA contact log.
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real
-- PII, INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical
-- home — the tier/seq-type/status LABELS + the SLA window live in params, NOT here),
-- THREAT_MODEL.md §6 (D-RLS-1…7), app/core/program.py (the canonical Program enum),
-- and 0035/0039 (the program-tenancy + deny-by-default RLS doctrine this migration
-- mirrors EXACTLY).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0035/0039 doctrine.
-- ===========================================================================
--   (A) `nurture_segment`   — a saved audience SEGMENT (a T1/T2/T3 tier + sub-bucket
--       with attribute filters + a sized, reachability-tagged audience). Aggregate
--       only (INV-1/INV-6): attribute_filters carry bucket LABELS, never a person.
--   (B) `nurture_sequence`  — the READ-ONLY synthetic MIRROR of a HubSpot Sales-Hub
--       sequence (the Sequences API is NOT available in this portal, so the cockpit
--       mirrors per-step open/click/conversion rates as synthetic, clearly labeled).
--   (C) `sms_thread`        — one SMS inbox thread. ``contact_label`` is a SYNTHETIC
--       token (e.g. "Family #A12"), NEVER a real name/phone (INV-1/INV-6).
--   (D) `sla_contact`       — one first-contact SLA timer row (entered_at → contacted_at).
--       ``applicant_label`` is a SYNTHETIC token, never PII.
--
--   PROGRAM-SCOPED: every table carries `program_id text NOT NULL DEFAULT
--   'fall_enrollment'` (the canonical Program.FALL_ENROLLMENT) + the 0024/0035
--   `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--   `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL` null
--   guard (D-RLS-2/D-RLS-3).
--
--   RLS: ENABLE AND FORCE row-level security on EVERY table (D-RLS-1), and EVERY
--   policy carries the auth.uid() null guard (D-RLS-2). This migration adds +4 tables
--   / +4 enable / +4 force / +8 null-guarded policies so the global
--   create==enable==force + one-guard-per-policy invariants (test_migrations_rls)
--   stay green.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + nurture write
-- path (the API require_role/owner gate) and is unaffected by RLS/force; it is never
-- client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) nurture_segment — a saved audience segment (aggregate; INV-1/INV-6).
-- ---------------------------------------------------------------------------
CREATE TABLE nurture_segment (
    segment_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The planning tier. CHECK is the DB backstop; the app-layer is the INV-11 home.
    tier              text NOT NULL DEFAULT 'T3'
        CHECK (tier IN ('T1', 'T2', 'T3')),

    sub_bucket        text NOT NULL DEFAULT '',
    label             text NOT NULL DEFAULT '',

    -- Aggregate attribute filters (bucket labels only, never a person; INV-6).
    attribute_filters jsonb NOT NULL DEFAULT '{}'::jsonb,

    size              integer NOT NULL DEFAULT 0,
    reachability_pct  numeric NOT NULL DEFAULT 0,

    -- The owning workstream/operator routing token (not PII); 'nurture'.
    owner             text NOT NULL DEFAULT 'nurture',
    notes             text NOT NULL DEFAULT '',

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE nurture_segment ENABLE ROW LEVEL SECURITY;
ALTER TABLE nurture_segment FORCE ROW LEVEL SECURITY;

CREATE POLICY nurture_segment_authenticated_select ON nurture_segment
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY nurture_segment_program_isolation ON nurture_segment
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

GRANT SELECT ON nurture_segment TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (B) nurture_sequence — the READ-ONLY synthetic mirror of a Sales-Hub sequence.
-- ---------------------------------------------------------------------------
CREATE TABLE nurture_sequence (
    sequence_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    name              text NOT NULL,

    -- The sequence type. CHECK mirrors the app-layer LABELS (INV-11 home in params).
    seq_type          text NOT NULL DEFAULT 'nurture'
        CHECK (seq_type IN ('welcome', 'nurture', 're_engagement', 'event', 'waitlist')),

    audience_size     integer NOT NULL DEFAULT 0,
    step_count        integer NOT NULL DEFAULT 0,

    -- Per-step perf: [{step, open_pct, click_pct, conversion_pct}] (synthetic mirror).
    steps             jsonb NOT NULL DEFAULT '[]'::jsonb,

    health_flag       boolean NOT NULL DEFAULT false,
    status            text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'draft')),

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE nurture_sequence ENABLE ROW LEVEL SECURITY;
ALTER TABLE nurture_sequence FORCE ROW LEVEL SECURITY;

CREATE POLICY nurture_sequence_authenticated_select ON nurture_sequence
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY nurture_sequence_program_isolation ON nurture_sequence
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

GRANT SELECT ON nurture_sequence TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (C) sms_thread — one SMS inbox thread. contact_label is a SYNTHETIC token (INV-1).
-- ---------------------------------------------------------------------------
CREATE TABLE sms_thread (
    thread_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A SYNTHETIC contact token (e.g. "Family #A12"), NEVER a real name/phone (INV-1/6).
    contact_label     text NOT NULL DEFAULT '',
    last_message      text NOT NULL DEFAULT '',

    -- Keyword/LLM-derived theme tags (synthetic content labels; INV-2 proposal-only).
    theme_tags        text[] NOT NULL DEFAULT ARRAY[]::text[],

    status            text NOT NULL DEFAULT 'unread'
        CHECK (status IN ('unread', 'no_reply', 'objection', 'hot_family', 'ready')),

    replied           boolean NOT NULL DEFAULT false,
    inbound_at        timestamptz,

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE sms_thread ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_thread FORCE ROW LEVEL SECURITY;

CREATE POLICY sms_thread_authenticated_select ON sms_thread
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY sms_thread_program_isolation ON sms_thread
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

GRANT SELECT ON sms_thread TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (D) sla_contact — one first-contact SLA timer (entered_at → contacted_at). The
-- applicant_label is a SYNTHETIC token, never PII (INV-1/INV-6).
-- ---------------------------------------------------------------------------
CREATE TABLE sla_contact (
    contact_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    applicant_label   text NOT NULL DEFAULT '',

    entered_at        timestamptz NOT NULL DEFAULT now(),
    contacted_at      timestamptz,

    owner             text NOT NULL DEFAULT 'nurture',

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE sla_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE sla_contact FORCE ROW LEVEL SECURITY;

CREATE POLICY sla_contact_authenticated_select ON sla_contact
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY sla_contact_program_isolation ON sla_contact
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

GRANT SELECT ON sla_contact TO authenticated, app_runtime;

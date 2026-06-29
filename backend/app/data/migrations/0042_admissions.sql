-- 0042_admissions.sql — Module 9 (Admissions & Voice of Customer): the program-scoped
-- persistence behind the listening-post surface — the objection log, the voice/quote
-- feed, the feedback→marketing loop, the weekly admission stats, and the
-- objection→content bridge tracker.
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real
-- PII: objection example_quote + voice quotes are SYNTHETIC text, never real families;
-- INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical home — the
-- theme/category/status LABELS live in params/app-layer, NOT here), THREAT_MODEL.md §6
-- (D-RLS-1…7), app/core/program.py (the canonical Program enum), and 0040/0041 (the
-- program-tenancy + deny-by-default RLS doctrine this migration mirrors EXACTLY).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0041 doctrine.
-- ===========================================================================
--   (A) `objection_log`   — one themed, frequency-counted, trended objection (the 9b
--       log). `example_quote` is a SYNTHETIC verbatim, never a real family (INV-1/INV-6).
--   (B) `voice_quote`     — one notable family verbatim (the 9d voice feed). SYNTHETIC
--       text; one row may be the rotating quote-of-the-week.
--   (C) `feedback_item`   — one "marketing needs to know X" item (the 9e loop), optionally
--       linked to a Decision-Queue row (`decision_id`) when actionable.
--   (D) `admission_stat`  — one week's admission funnel counters (the 9a numbers).
--   (E) `content_bridge`  — one objection→content-brief bridge row (the 9c tracker):
--       the brief calendar entry it produced + the before/after objection frequency, so
--       the bridge hit-rate + objection-to-resolution time are computed, never faked.
--
--   PROGRAM-SCOPED: every table carries `program_id text NOT NULL DEFAULT
--   'fall_enrollment'` (the canonical Program.FALL_ENROLLMENT) + the 0024/0035
--   `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--   `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL` null
--   guard (D-RLS-2/D-RLS-3).
--
--   RLS: ENABLE AND FORCE row-level security on EVERY table (D-RLS-1), and EVERY
--   policy carries the auth.uid() null guard (D-RLS-2). This migration adds +5 tables
--   / +5 enable / +5 force / +10 null-guarded policies so the global
--   create==enable==force + one-guard-per-policy invariants (test_migrations_rls)
--   stay green.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + admissions
-- write path (the API require_role/owner gate) and is unaffected by RLS/force; it is
-- never client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights
-- function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) objection_log — one themed, frequency-counted, trended objection.
-- ---------------------------------------------------------------------------
CREATE TABLE objection_log (
    objection_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The objection THEME. CHECK is the DB backstop; the app-layer (params.admissions
    -- .themes) is the canonical home (INV-11).
    theme             text NOT NULL DEFAULT 'other'
        CHECK (theme IN (
            'accreditation', 'cost', 'gifted_enough', 'scheduling',
            'curriculum', 'social', 'tech_requirements', 'other'
        )),

    week_count        int NOT NULL DEFAULT 0,
    cumulative_count  int NOT NULL DEFAULT 0,

    trend             text NOT NULL DEFAULT 'stable'
        CHECK (trend IN ('up', 'stable', 'down')),

    source            text NOT NULL DEFAULT 'other'
        CHECK (source IN ('bdr_call', 'sms', 'event', 'form', 'other')),

    -- A SYNTHETIC example verbatim — NEVER a real family (INV-1/INV-6).
    example_quote     text NOT NULL DEFAULT '',
    -- A synthetic aggregate persona label (not PII).
    persona           text NOT NULL DEFAULT '',

    urgency           text NOT NULL DEFAULT 'normal'
        CHECK (urgency IN ('low', 'normal', 'high')),

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE objection_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE objection_log FORCE ROW LEVEL SECURITY;

CREATE POLICY objection_log_authenticated_select ON objection_log
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY objection_log_program_isolation ON objection_log
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

GRANT SELECT ON objection_log TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (B) voice_quote — one notable SYNTHETIC family verbatim (the voice feed).
-- ---------------------------------------------------------------------------
CREATE TABLE voice_quote (
    quote_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A SYNTHETIC verbatim, never a real family (INV-1/INV-6).
    quote             text NOT NULL DEFAULT '',

    sentiment         text NOT NULL DEFAULT 'neutral'
        CHECK (sentiment IN ('positive', 'neutral', 'negative')),

    -- A free aggregate theme tag (not enum-bound; complements objection themes).
    theme             text NOT NULL DEFAULT '',
    -- A synthetic aggregate source label (e.g. tour / form / enrolled), not PII.
    source            text NOT NULL DEFAULT '',

    is_quote_of_week  boolean NOT NULL DEFAULT false,
    week_of           date,

    created_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE voice_quote ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_quote FORCE ROW LEVEL SECURITY;

CREATE POLICY voice_quote_authenticated_select ON voice_quote
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY voice_quote_program_isolation ON voice_quote
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

GRANT SELECT ON voice_quote TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (C) feedback_item — one "marketing needs to know X" loop item.
-- ---------------------------------------------------------------------------
CREATE TABLE feedback_item (
    item_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    summary           text NOT NULL DEFAULT '',

    category          text NOT NULL DEFAULT 'messaging_gap'
        CHECK (category IN (
            'messaging_gap', 'persona_mismatch', 'objection_pattern',
            'positive_signal', 'urgent'
        )),

    status            text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'actioned', 'closed')),

    actionable        boolean NOT NULL DEFAULT false,

    -- The owning workstream/operator routing token (not PII); 'admissions'.
    owner             text NOT NULL DEFAULT 'admissions',

    -- The Decision-Queue row this item escalated into when actionable (NULL otherwise).
    -- NOT an FK (the decision lives in a separate program-scoped table); a soft link.
    decision_id       uuid,

    created_at        timestamptz NOT NULL DEFAULT now(),
    actioned_at       timestamptz,

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE feedback_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback_item FORCE ROW LEVEL SECURITY;

CREATE POLICY feedback_item_authenticated_select ON feedback_item
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY feedback_item_program_isolation ON feedback_item
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

GRANT SELECT ON feedback_item TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (D) admission_stat — one week's admission funnel counters (the 9a numbers).
-- ---------------------------------------------------------------------------
CREATE TABLE admission_stat (
    stat_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    week_of           date NOT NULL,
    applicants        int NOT NULL DEFAULT 0,
    shadow_days       int NOT NULL DEFAULT 0,
    offers            int NOT NULL DEFAULT 0,
    deposits          int NOT NULL DEFAULT 0,

    created_at        timestamptz NOT NULL DEFAULT now(),

    program_id        text NOT NULL DEFAULT 'fall_enrollment',

    -- One row per (program, week) so a re-seed UPSERTS, never duplicates.
    UNIQUE (program_id, week_of)
);

ALTER TABLE admission_stat ENABLE ROW LEVEL SECURITY;
ALTER TABLE admission_stat FORCE ROW LEVEL SECURITY;

CREATE POLICY admission_stat_authenticated_select ON admission_stat
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY admission_stat_program_isolation ON admission_stat
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

GRANT SELECT ON admission_stat TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (E) content_bridge — one objection→content-brief bridge row (the 9c tracker).
-- ---------------------------------------------------------------------------
CREATE TABLE content_bridge (
    bridge_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    objection_theme   text NOT NULL DEFAULT '',

    -- The content calendar entry this bridge produced (a soft link into the 0036
    -- content_calendar_entry table; not an FK across the program-scoped boundary).
    brief_entry_id    uuid,

    produced          boolean NOT NULL DEFAULT false,

    surfaced_at       timestamptz NOT NULL DEFAULT now(),
    published_at      timestamptz,

    -- The objection frequency BEFORE the brief and (once published) AFTER, so a
    -- frequency drop is a computed signal, never faked.
    freq_before       int NOT NULL DEFAULT 0,
    freq_after        int,

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE content_bridge ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_bridge FORCE ROW LEVEL SECURITY;

CREATE POLICY content_bridge_authenticated_select ON content_bridge
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY content_bridge_program_isolation ON content_bridge
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

GRANT SELECT ON content_bridge TO authenticated, app_runtime;

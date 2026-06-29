-- 0043_website.sql — Module 13 (Website & Digital Analytics): the program-scoped
-- persistence behind the website-analytics surface.
--
-- IMPORTANT — what does and does NOT live here. The site/page/traffic-source/download/
-- conversion-path METRICS are read off the GA4 boundary (app/adapters/analytics) — in v1
-- a STOOD-IN simulated adapter (no live GA4 credentials in this portal; INV-9). Those
-- aggregate reads are NOT persisted to Supabase — they come "from GA4" exactly like the
-- §7.5 sentiment summary comes from the sentiment feed. What the HUB owns and writes —
-- and therefore what this migration persists — is the LEADERSHIP-input state only:
--   (A) `page_flag`         — a page leadership flagged as underperforming for a content
--       refresh (the spec's "Flag underperforming pages for content refresh"), optionally
--       linked to the Content brief it produced (CROSS-LINK → Module 3) and/or the
--       Decision-Queue card it raised (CROSS-LINK → Module 11).
--   (B) `analysis_request`  — a leadership request for analysis on a specific page or
--       campaign (the spec's "Request analysis on specific pages or campaigns"), raised
--       into the Decision Queue (CROSS-LINK → Module 11).
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real PII;
-- INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical home — the
-- site/page-type/status LABELS live in params/app-layer, NOT here), THREAT_MODEL.md §6
-- (D-RLS-1…7), app/core/program.py (the canonical Program enum), and 0041/0042 (the
-- program-tenancy + deny-by-default RLS doctrine this migration mirrors EXACTLY).
--
-- PROGRAM-SCOPED: every table carries `program_id text NOT NULL DEFAULT 'fall_enrollment'`
-- + the `AS RESTRICTIVE` program-isolation policy keyed on the caller's
-- `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL` null guard
-- (D-RLS-2/D-RLS-3). RLS is ENABLEd AND FORCEd on EVERY table (D-RLS-1), and EVERY policy
-- carries the auth.uid() null guard (D-RLS-2): +2 tables / +2 enable / +2 force / +4
-- null-guarded policies so the global create==enable==force + one-guard-per-policy
-- invariants (test_migrations_rls) stay green.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + leadership write
-- path (the API require_role gate) and is unaffected by RLS/force; it is never
-- client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights function.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) page_flag — a page leadership flagged as underperforming for a refresh.
-- ---------------------------------------------------------------------------
CREATE TABLE page_flag (
    flag_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The page path (e.g. '/tuition') and the site it lives on. The site CHECK is the
    -- DB backstop; the app-layer (params.website.sites) is the canonical home (INV-11).
    page_path         text NOT NULL DEFAULT '',
    site              text NOT NULL DEFAULT 'gt.school'
        CHECK (site IN ('gt.school', 'anywhere.gt.school')),

    -- Why the page was flagged (a synthetic aggregate reason, not PII).
    reason            text NOT NULL DEFAULT '',

    status            text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'resolved')),

    -- The Content calendar entry this flag produced (a soft link into the 0036
    -- content_calendar_entry table; not an FK across the program-scoped boundary).
    brief_entry_id    uuid,
    -- The Decision-Queue row this flag escalated into (NULL when none); a soft link.
    decision_id       uuid,

    -- The owning workstream routing token (not PII); 'website'.
    owner             text NOT NULL DEFAULT 'website',

    created_at        timestamptz NOT NULL DEFAULT now(),
    resolved_at       timestamptz,

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE page_flag ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_flag FORCE ROW LEVEL SECURITY;

CREATE POLICY page_flag_authenticated_select ON page_flag
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY page_flag_program_isolation ON page_flag
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

GRANT SELECT ON page_flag TO authenticated, app_runtime;

-- ---------------------------------------------------------------------------
-- (B) analysis_request — a leadership request for analysis on a page/campaign.
-- ---------------------------------------------------------------------------
CREATE TABLE analysis_request (
    request_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- What analysis was requested on (a page path or a campaign label) + the question.
    target            text NOT NULL DEFAULT '',
    question          text NOT NULL DEFAULT '',

    -- 'page' or 'campaign' — what `target` refers to (the DB backstop; app-layer canon).
    target_kind       text NOT NULL DEFAULT 'page'
        CHECK (target_kind IN ('page', 'campaign')),

    status            text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'resolved')),

    -- The Decision-Queue row this request raised (NULL when none); a soft link.
    decision_id       uuid,

    owner             text NOT NULL DEFAULT 'website',

    created_at        timestamptz NOT NULL DEFAULT now(),
    resolved_at       timestamptz,

    program_id        text NOT NULL DEFAULT 'fall_enrollment'
);

ALTER TABLE analysis_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_request FORCE ROW LEVEL SECURITY;

CREATE POLICY analysis_request_authenticated_select ON analysis_request
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY analysis_request_program_isolation ON analysis_request
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

GRANT SELECT ON analysis_request TO authenticated, app_runtime;

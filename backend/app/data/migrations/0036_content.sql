-- 0036_content.sql — Module 3 (Content & Thought Leadership): the three
-- program-scoped tables behind the Content analytics surface (overview / editorial
-- calendar / channel + per-piece performance).
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic/aggregate data only — NO real
-- PII, INV-5 deny-by-default RLS + service_role server-only, INV-11 one canonical
-- home — the channel LABELS + the conflict threshold + the 42% X conversion-engine
-- figure live in params/params.yaml, NOT here), THREAT_MODEL.md §6 (D-RLS-1…7),
-- app/core/program.py (the canonical Program enum), and 0024/0030/0035 (the
-- program-tenancy + deny-by-default RLS doctrine this migration mirrors exactly).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0035 doctrine.
-- ===========================================================================
--   (A) `content_calendar_entry` — one editorial-calendar slot (a piece scheduled on
--       a day, on a channel). Feeds the month grid + drag-reschedule + same-day
--       CONFLICT detection. `piece_ref` is a nullable link to a kanban/library item.
--   (B) `content_channel_metric` — one channel's reach/clicks/conversions for a
--       period. `source_kind` drives the HONESTY label (a channel without a real
--       adapter is labeled 'stood_in'/'manual', never dressed up as a live feed).
--   (C) `content_piece_perf`     — one piece's reach/clicks/conversions, with
--       `utm_attributed` flagging whether its conversions are UTM-attributable (the
--       broken-UTM reality stays visible). Feeds top/bottom + content-to-conversion.
--
--   (D) All three are PROGRAM-SCOPED: each carries
--       `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the canonical
--       Program.FALL_ENROLLMENT, app/core/program.py, INV-11) and the 0024
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` claim WITH the `(SELECT auth.uid()) IS NOT NULL`
--       null guard (D-RLS-2/D-RLS-3) — AND-ed on top of the permissive read policy.
--
--   (E) RLS: each table both ENABLEs AND FORCEs row-level security (D-RLS-1), and
--       EVERY policy carries the auth.uid() null guard (D-RLS-2). This keeps the
--       global create==enable==force + one-guard-per-policy invariants
--       (test_migrations_rls) green (this migration adds +3 tables / +3 enable /
--       +3 force / +6 null-guarded policies) while anon (auth.uid() = NULL) matches
--       no row.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's seed + content
-- write path (the API require_role/owner gate) and is unaffected by RLS/force; it is
-- never client-exposed (INV-5). D-RLS-7: this migration defines NO definer-rights
-- function. INV-1/INV-6: synthetic/aggregate only — no real PII ever enters here.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) content_calendar_entry — one editorial-calendar slot. Program-scoped. The
-- same-day CONFLICT is DERIVED app-side (the threshold is params, INV-11), so this
-- table only stores the slots. Synthetic/aggregate (INV-1).
-- ---------------------------------------------------------------------------
CREATE TABLE content_calendar_entry (
    entry_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A synthetic editorial title (a content piece, never PII; INV-1).
    title           text NOT NULL,
    -- The publishing channel label (a content.channels token; the LABELS' canonical
    -- home is params, so no CHECK here — INV-11).
    channel         text NOT NULL,
    scheduled_date  date NOT NULL,

    -- The slot lifecycle. CHECK mirrors the app-layer statuses (INV-11 home is app).
    status          text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'scheduled', 'published', 'draft')),

    -- A nullable link to a kanban card / library asset (a routing ref, not PII).
    piece_ref       text,

    -- The owning workstream/operator label (a routing token, not PII).
    owner           text NOT NULL DEFAULT 'content',

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    program_id      text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (B) content_channel_metric — one channel's reach/clicks/conversions for a period.
-- `source_kind` is the provenance label (INV-9 honesty). Program-scoped. The
-- (program_id, channel, period_start) natural key is UNIQUE so the seed/upsert is
-- idempotent (PostgREST on_conflict).
-- ---------------------------------------------------------------------------
CREATE TABLE content_channel_metric (
    metric_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    channel         text NOT NULL,
    period_start    date NOT NULL,

    reach           integer NOT NULL DEFAULT 0,
    clicks          integer NOT NULL DEFAULT 0,
    conversions     integer NOT NULL DEFAULT 0,

    -- The provenance label that drives the honesty badge: a channel without a real
    -- adapter is 'stood_in'/'manual', never dressed up as a live feed (INV-9).
    source_kind     text NOT NULL DEFAULT 'stood_in',

    created_at      timestamptz NOT NULL DEFAULT now(),

    program_id      text NOT NULL DEFAULT 'fall_enrollment',

    UNIQUE (program_id, channel, period_start)
);

-- ---------------------------------------------------------------------------
-- (C) content_piece_perf — one piece's reach/clicks/conversions + UTM attribution.
-- `utm_attributed` keeps the broken-UTM reality visible (a piece whose conversions
-- are NOT UTM-attributable reads honestly as unattributable). Program-scoped. The
-- (program_id, piece_title, channel) natural key is UNIQUE (idempotent upsert).
-- ---------------------------------------------------------------------------
CREATE TABLE content_piece_perf (
    perf_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A synthetic piece title (content, never PII; INV-1).
    piece_title     text NOT NULL,
    channel         text NOT NULL,

    reach           integer NOT NULL DEFAULT 0,
    clicks          integer NOT NULL DEFAULT 0,
    conversions     integer NOT NULL DEFAULT 0,

    -- Whether this piece's conversions are UTM-attributable (the honesty flag).
    utm_attributed  boolean NOT NULL DEFAULT false,

    created_at      timestamptz NOT NULL DEFAULT now(),

    program_id      text NOT NULL DEFAULT 'fall_enrollment',

    UNIQUE (program_id, piece_title, channel)
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE content_calendar_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_calendar_entry FORCE ROW LEVEL SECURITY;
ALTER TABLE content_channel_metric ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_channel_metric FORCE ROW LEVEL SECURITY;
ALTER TABLE content_piece_perf ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_piece_perf FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive read policies. Any authenticated, in-program principal may READ the
-- Content analytics surface (the cockpit reads via service_role; this null-guarded
-- SELECT is the RLS-compliant direct-read path). WRITES are privileged — service_role
-- (the API require_role/owner gate). Every policy carries the (SELECT auth.uid()) IS
-- NOT NULL guard (D-RLS-2/D-RLS-3): anon matches no row, and the global
-- one-guard-per-policy invariant stays green.
-- ===========================================================================
CREATE POLICY content_calendar_entry_authenticated_select ON content_calendar_entry
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY content_channel_metric_authenticated_select ON content_channel_metric
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

CREATE POLICY content_piece_perf_authenticated_select ON content_piece_perf
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0035 pattern): the caller must
-- be authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so
-- neither a read nor a write can cross the program boundary; AND-ed on top of the
-- permissive policies above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY content_calendar_entry_program_isolation ON content_calendar_entry
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

CREATE POLICY content_channel_metric_program_isolation ON content_channel_metric
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

CREATE POLICY content_piece_perf_program_isolation ON content_piece_perf
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
-- PostgREST role grants. All three tables: SELECT for any authenticated, in-program
-- principal (the policy gates WHO). WRITES land via service_role (server-only,
-- BYPASSRLS — INV-5 / D-RLS-4); no client write grant. app_runtime is NOBYPASSRLS
-- (0024) so its reads stay bounded by the program-isolation policy.
-- ===========================================================================
GRANT SELECT ON content_calendar_entry TO authenticated, app_runtime;
GRANT SELECT ON content_channel_metric TO authenticated, app_runtime;
GRANT SELECT ON content_piece_perf TO authenticated, app_runtime;

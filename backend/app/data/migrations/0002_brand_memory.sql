-- 0002_brand_memory.sql — persistent brand-memory table (S4, FR-3.2).
--
-- Authoritative source: CONTENT_SPEC.md §8.3 (BrandMemoryItem — kept/curated
-- memory that conditions the next generation batch) + TECH_STACK D-8 (brand
-- memory is server-side PERSISTENT, never browser localStorage). The
-- application-side shape lives in `backend/app/ai/schemas/brand.py`
-- (`BrandMemoryItem`); the v1 LOCAL impl is stdlib-`sqlite3`-backed
-- (ASSUMPTIONS A-11, no Postgres in this env per A-3). THIS migration is the
-- PRODUCTION Postgres target.
--
-- ===========================================================================
-- RLS DOCTRINE — THREAT_MODEL.md §6 (D-RLS-1 … D-RLS-7), CLAUDE.md §1 (INV-5).
-- ===========================================================================
-- Mirrors `0001_init.sql`'s locked doctrine exactly:
--
--   D-RLS-1  Deny-by-default. This `public`-schema table ENABLES row level
--            security at creation time. RLS on + no matching policy = no rows.
--   D-RLS-2  The owner-scoped policy uses the null guard
--              (SELECT auth.uid()) IS NOT NULL AND (SELECT auth.uid()) = user_id
--            — the explicit `auth.uid() IS NOT NULL` check ANDed with the owner
--            match closes the `null = user_id`-is-always-NULL IDOR trap.
--   D-RLS-3  Brand memory is org/marketing-owned, server-side (like the §4.9
--            observability spine): rows carry a NULL `user_id` and are therefore
--            UNREADABLE under anon/authenticated — reachable ONLY by the trusted
--            server using `service_role` (BYPASSRLS). No anon/authenticated
--            policy ever matches a NULL-owner row.
--   D-RLS-4  `service_role` is server-only, never client-side.
--   D-RLS-7  NO security-definer helper functions in this exposed schema; the
--            ownership logic is inlined into the policy as a plain predicate.
-- ===========================================================================

CREATE TABLE brand_memory (
    id            text PRIMARY KEY,
    -- Ownership column (D-RLS-2). NULL = server-only, org/marketing-owned brand
    -- memory that is unreadable under anon/authenticated (D-RLS-3) — brand
    -- memory is not a per-family row; it is curated by the marketing org and
    -- read by the trusted server (service_role).
    user_id       uuid,

    kind          text NOT NULL,
    content       text NOT NULL,
    signal        text,
    source_ref    text,
    weight        numeric NOT NULL,
    channel_scope jsonb NOT NULL DEFAULT '[]'::jsonb,
    active        boolean NOT NULL,
    version       int NOT NULL,
    provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at    timestamptz DEFAULT now(),
    updated_at    timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation time.
ALTER TABLE brand_memory ENABLE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: owner-scoped read, null-guarded. Brand-memory rows are
-- server-only (NULL user_id) — this policy matches no one under anon/
-- authenticated, so the rows are reachable only via service_role (BYPASSRLS).
CREATE POLICY brand_memory_owner_select ON brand_memory
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- ===========================================================================
-- PostgREST role grants. The table is reachable by anon/authenticated ONLY via
-- the SELECT policy above; the absence of write policies makes all
-- INSERT/UPDATE/DELETE from those roles deny-by-default (D-RLS-1). `service_role`
-- (BYPASSRLS, server-only — D-RLS-4) is the only role that reads these
-- NULL-owner rows (D-RLS-3).
-- ===========================================================================

GRANT SELECT ON brand_memory TO anon, authenticated;

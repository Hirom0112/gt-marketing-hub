-- 0013_sales_agents.sql — MULTI_AGENT_COCKPIT.md §3, PLAN.md M0 (R1): the
-- DB-authoritative ownership seam — a `sales_agent` registry + a DISTINCT
-- `family_record.assigned_rep_id` FK + `assigned_at`.
--
-- Authoritative source: MULTI_AGENT_COCKPIT.md §3 (sales_agent registry +
-- family_record.assigned_rep_id), PLAN.md M0 (R1: assigned_rep_id is NOT a reuse
-- of user_id), CLAUDE.md §1 (INV-1 synthetic-only, INV-5 deny-by-default RLS,
-- INV-9 simulated owner mirror), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0001/0009/0010 doctrine.
-- ===========================================================================
--   1. `sales_agent` — an N-CONFIGURABLE registry of demo sales agents. The shape
--      supports any number of agents; the DEMO seeds exactly 2 deterministically:
--        * rank 1 = `closer` (the founder's seat — `closer_rank_max=1` in
--          params, so rank ≤ 1 is the closer tier);
--        * rank 2 = `setter` (the average/setter seat).
--      Each row carries a STABLE per-rank uuid (fixed literal, NOT random — so
--      rank→agent is a static lookup that survives re-seeding), a `rank`, a
--      `synthetic_name` (INV-1 — synthetic only, never a real person), a `tier`
--      (`closer`|`setter`), and a `hubspot_owner_id` (the live owner mirror,
--      SIMULATED in v1 per INV-9 — a placeholder owner-id string).
--
--   2. `family_record.assigned_rep_id` — a NEW NULLABLE column, FK → sales_agent
--      (agent_id). This is DB-authoritative deal ownership and is DISTINCT from
--      `user_id` (R1, the M0 risk):
--        * `family_record.user_id`      = the APPLICANT family's RLS owner
--          (auth.uid()) — who may read/write their own application rows;
--        * `family_record.assigned_rep_id` = the SALESPERSON who owns the deal in
--          the cockpit. `NULL` ⇒ unassigned (the intake pool / unowned alarm).
--      These are two different identities on the same row and must never be
--      conflated (the IDOR-class confusion the threat model forbids). Plus
--      `assigned_at timestamptz` — when the rep was assigned.
--
--   3. RLS: `sales_agent` is a REGISTRY (not family-owned data) — every
--      AUTHENTICATED app user may read the roster. We express that as a
--      NULL-GUARDED policy (`auth.uid() IS NOT NULL`) — the SAME guard shape the
--      source-table policies use — so the global one-guard-per-policy invariant
--      (test_one_null_guard_per_policy) stays green and anon (auth.uid() = NULL)
--      still matches no row (D-RLS-3). ENABLE *and* FORCE RLS (D-RLS-1) so the
--      table-owner role obeys the policy too and the CREATE==ENABLE==FORCE counts
--      stay equal (test_migrations_rls).
--
--   4. GRANTs: SELECT on sales_agent to `authenticated` (policy-gated). Writes to
--      the registry stay deny-by-default (seeded server-side / by migration; no
--      anon/authenticated INSERT/UPDATE/DELETE policy or grant).
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) is the cockpit's cross-agent
-- read/assign path (it sets assigned_rep_id) and is unaffected by RLS/FORCE.
--
-- CRITICAL (test_migrations_rls): a new table MUST ENABLE *and* FORCE RLS (the
-- CREATE-table count must equal the ENABLE count and the FORCE count across all
-- migrations) and carry a null-guarded policy.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- §3 sales_agent — the N-configurable demo agent registry. agent_id is stable
-- per rank (fixed uuid literals below), so rank→agent is a static lookup.
-- ---------------------------------------------------------------------------
CREATE TABLE sales_agent (
    agent_id         uuid PRIMARY KEY,
    -- The agent's rank (1 = top). rank→tier is governed by params
    -- (closer_rank_max): rank ≤ closer_rank_max ⇒ closer, else setter.
    rank             integer NOT NULL,
    -- Synthetic display name (INV-1) — NEVER a real person's name.
    synthetic_name   text NOT NULL,
    -- The agent's tier: 'closer' (high-rank) or 'setter' (everyone else).
    tier             text NOT NULL CHECK (tier IN ('closer', 'setter')),
    -- The live HubSpot owner this agent mirrors (INV-9: SIMULATED in v1 — a
    -- placeholder owner-id string; the live owner id lands here in prod).
    hubspot_owner_id text NOT NULL,

    created_at       timestamptz DEFAULT now(),
    updated_at       timestamptz DEFAULT now(),

    -- One agent per rank (keeps rank→agent a unique static lookup).
    UNIQUE (rank)
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the owner-scoped policy (the test asserts FORCE-count == table-count).
ALTER TABLE sales_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_agent FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: the registry is readable by ANY authenticated app user (it
-- is not family-owned data), expressed as a NULL-GUARDED policy — the same
-- `auth.uid() IS NOT NULL` guard shape as the source-table policies. anon
-- (auth.uid() = NULL) matches no row. No write policy: registry writes stay
-- deny-by-default (seeded by migration / service_role only).
CREATE POLICY sales_agent_authenticated_select ON sales_agent
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- family_record.assigned_rep_id — DB-authoritative DEAL ownership (the
-- salesperson), DISTINCT from user_id (the applicant family's RLS owner, R1).
-- NULLABLE: NULL ⇒ unassigned (the intake pool / unowned alarm). Plus assigned_at.
-- ---------------------------------------------------------------------------
ALTER TABLE family_record ADD COLUMN assigned_rep_id uuid REFERENCES sales_agent (agent_id);
ALTER TABLE family_record ADD COLUMN assigned_at timestamptz;

-- ===========================================================================
-- DETERMINISTIC DEMO SEED — exactly 2 agents (the registry shape is
-- N-configurable; the demo seeds 2). STABLE uuid literals per rank (NOT random)
-- so rank→agent survives re-seeding. Idempotent via ON CONFLICT DO NOTHING.
-- Synthetic names only (INV-1); hubspot_owner_id is a SIMULATED placeholder
-- owner id (INV-9 — replaced by the live owner id in prod).
-- ===========================================================================
INSERT INTO sales_agent (agent_id, rank, synthetic_name, tier, hubspot_owner_id)
VALUES
    -- rank 1 = closer (the founder's seat; closer_rank_max = 1 in params).
    ('a0000000-0000-4000-8000-000000000001', 1, 'Riley Carter', 'closer', 'sim-owner-0001'),
    -- rank 2 = setter (the average/setter seat).
    ('a0000000-0000-4000-8000-000000000002', 2, 'Jordan Avery', 'setter', 'sim-owner-0002')
ON CONFLICT (agent_id) DO NOTHING;

-- ===========================================================================
-- PostgREST role grants. SELECT on sales_agent to `authenticated` (policy-gated,
-- null-guarded — anon matches no row, D-RLS-3). No registry write grants:
-- assignment is performed server-side via `service_role` (D-RLS-4).
-- ===========================================================================
GRANT SELECT ON sales_agent TO authenticated;

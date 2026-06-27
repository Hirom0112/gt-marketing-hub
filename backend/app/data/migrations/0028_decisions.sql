-- 0028_decisions.sql — B2: the cross-module human Decision Queue. Two tables that
-- let ANY module flag an item for a human, and gate VIEW/DECIDE behind leadership.
--
-- Authoritative source: TODO_v2.md §B2, PLAN_v2.md §B2, CLAUDE.md §1 (INV-2 the
-- deterministic core owns writes / human approval; INV-5 deny-by-default RLS;
-- INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7 — notably D-RLS-7: no
-- definer-rights helper in the exposed schema), 0027_rbac.sql (the private.authorize
-- role→permission helper this migration REUSES), 0024 (the program-tenancy
-- doctrine) and 0010/0015 (the append-only-audit doctrine).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0027 + 0010/0015
-- doctrine.
-- ===========================================================================
--   (A) `decision` — one row per open/decided item in the queue. A module flags an
--       item by inserting a row: `source` is which module flagged it
--       (nurture / budget / field / seam …), `payload` is the jsonb context, and
--       `state` is open / decided / in_flight (open at submit). The DISTINCTIVE
--       split: SUBMIT is OPEN to any authenticated user (any module may flag an
--       item), but VIEW and DECIDE are LEADER-gated — only a role that binds
--       `decision_queue.view` / `decision_queue.decide` may read or act. This is
--       the in-DB backstop behind the FastAPI route guard (a separate unit); the
--       gate is expressed via the 0027 private.authorize() helper (REUSED, not
--       redefined — so NO new definer-rights helper lands in the exposed schema,
--       D-RLS-7).
--
--   (B) `decision_event` — the APPEND-ONLY audit of every action taken on a
--       decision: `action` (approve / reject / need_info), an optional `comment`,
--       the `actor`, and the create stamp. One fact per action, immutable once
--       written (the 0010/0015 posture): GRANT only SELECT + INSERT, NO
--       UPDATE/DELETE grant or policy. Reading the audit is LEADER-gated (the same
--       `decision_queue.view` permission); appending is open to the authenticated
--       actor recording its own action.
--
--   (C) BOTH tables are PROGRAM-SCOPED (a decision belongs to a program): each
--       carries `program_id text NOT NULL DEFAULT 'fall_enrollment'` (the canonical
--       Program.FALL_ENROLLMENT, app/core/program.py, INV-11) and the 0024
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` JWT claim AND carrying the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3) — AND-ed on
--       top of the permissive policies (isolation tightens, never loosens).
--
--   (D) RLS: each table both turns on AND forces row-level security (D-RLS-1), and
--       EVERY policy carries the auth.uid() null guard (D-RLS-2). This keeps the
--       global CREATE==enable==force + one-guard-per-policy invariants
--       (test_migrations_rls) green (this migration adds +2 tables / +2 enable /
--       +2 force) while anon (auth.uid() = NULL) matches no row.
--
--   (E) role_permissions seed (the leader-gate's data). 0027 created the
--       role_permissions lookup but seeded no rows; this migration binds
--       `decision_queue.view` AND `decision_queue.decide` to admin + leader (and to
--       NEITHER for operator), so private.authorize() returns true for the right
--       roles. Idempotent (ON CONFLICT DO NOTHING). The permission VOCABULARY's
--       canonical home stays the app layer (params.rbac / app/core/authz.py,
--       INV-11); this is the in-DB backstop's copy of the same grid.
--
-- service_role (BYPASSRLS, server-only, D-RLS-4) is the cockpit's cross-program
-- read/seed path and is unaffected by RLS/force; it is never client-exposed (INV-5).
-- D-RLS-7: this migration defines NO definer-rights function — it REUSES the 0027
-- private.authorize() helper, which already lives in the non-exposed `private`
-- schema.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) decision — one row per open/decided queue item. Program-scoped. Operational
-- queue state (not family-owned data): the leader-gate, not owner-scoping, governs
-- reads/decides.
-- ---------------------------------------------------------------------------
CREATE TABLE decision (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which module flagged the item (e.g. 'nurture', 'budget', 'field', 'seam').
    source     text NOT NULL,

    -- The jsonb context the deciding human needs (PII-free, synthetic — INV-1).
    payload    jsonb NOT NULL,

    -- The lifecycle state. open at submit; a leader's decide transitions it.
    state      text NOT NULL DEFAULT 'open'
        CHECK (state IN ('open', 'decided', 'in_flight')),

    created_at timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). NOT NULL DEFAULT pins existing/new rows
    -- to the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (B) decision_event — the APPEND-ONLY audit of every action on a decision.
-- Program-scoped. Immutable once written (the 0010/0015 posture).
-- ---------------------------------------------------------------------------
CREATE TABLE decision_event (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The decision this action was taken on.
    decision_id uuid REFERENCES decision (id),

    -- WHAT the actor did.
    action      text NOT NULL
        CHECK (action IN ('approve', 'reject', 'need_info')),

    -- An optional free-form comment (PII-free, synthetic — INV-1).
    comment     text,

    -- WHO took the action (an actor reference — a uid/role token, never a name).
    actor       text NOT NULL,

    created_at  timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024).
    program_id  text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner role
-- obeys the policies (the test asserts force-count == table-count).
ALTER TABLE decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision FORCE ROW LEVEL SECURITY;
ALTER TABLE decision_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_event FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- Permissive policies. The leader-gate: VIEW/DECIDE require the caller's role to
-- bind the permission (private.authorize, 0027); SUBMIT is open to authenticated.
-- Every policy carries the (SELECT auth.uid()) IS NOT NULL guard (D-RLS-2/D-RLS-3):
-- anon matches no row, and the global one-guard-per-policy invariant stays green.
-- ===========================================================================

-- decision: VIEW is leader-gated (only a role binding decision_queue.view reads).
CREATE POLICY decision_leader_select ON decision
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND private.authorize('decision_queue.view')
    );

-- decision: SUBMIT (insert an OPEN decision) is OPEN to any authenticated user —
-- any module may flag an item. No authorize gate; just the null guard (the program
-- RESTRICTIVE policy below additionally pins the row into the caller's program).
CREATE POLICY decision_submit_insert ON decision
    FOR INSERT
    TO authenticated
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
    );

-- decision: DECIDE (state change) is leader-gated (only decision_queue.decide).
-- USING gates the rows visible to update; WITH CHECK gates the post-image — both
-- null-guarded and both behind the decide permission.
CREATE POLICY decision_leader_update ON decision
    FOR UPDATE
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND private.authorize('decision_queue.decide')
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND private.authorize('decision_queue.decide')
    );

-- decision_event: reading the audit is leader-gated (same decision_queue.view).
CREATE POLICY decision_event_leader_select ON decision_event
    FOR SELECT
    TO authenticated
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND private.authorize('decision_queue.view')
    );

-- decision_event: appending an action is open to the authenticated actor recording
-- its OWN action. Append-only — there is deliberately NO UPDATE/DELETE policy.
CREATE POLICY decision_event_append_insert ON decision_event
    FOR INSERT
    TO authenticated
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0026 pattern): the caller must
-- be authenticated (null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING + WITH CHECK so
-- neither a read nor a write can cross the program boundary; AND-ed on top of the
-- permissive policies above (isolation tightens, never loosens).
-- ---------------------------------------------------------------------------
CREATE POLICY decision_program_isolation ON decision
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

CREATE POLICY decision_event_program_isolation ON decision_event
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
-- (E) role_permissions seed — the leader-gate's data. 0027 created the lookup but
-- seeded nothing; bind VIEW + DECIDE to admin + leader (operator gets NEITHER), so
-- private.authorize() returns true for exactly the right roles. Idempotent.
-- ===========================================================================
INSERT INTO role_permissions (role, permission) VALUES
    ('admin',  'decision_queue.view'),
    ('leader', 'decision_queue.view'),
    ('admin',  'decision_queue.decide'),
    ('leader', 'decision_queue.decide')
ON CONFLICT (role, permission) DO NOTHING;

-- ===========================================================================
-- PostgREST role grants. decision: SELECT/INSERT/UPDATE (the policies above gate
-- WHO — leader for select/update, any authenticated for insert). decision_event:
-- APPEND-ONLY — SELECT + INSERT only, NO UPDATE/DELETE grant (the audit row is
-- immutable). All still bounded by RLS (app_runtime is NOBYPASSRLS, 0024).
-- service_role (server-only, BYPASSRLS) is the cross-program read/seed path and is
-- unaffected.
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE ON decision TO authenticated, app_runtime;
GRANT SELECT, INSERT ON decision_event TO authenticated, app_runtime;

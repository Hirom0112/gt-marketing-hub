-- 0015_security_event.sql — M7 (MULTI_AGENT_COCKPIT.md §3, §7): the append-only
-- `security_event` audit table — the DETECTION (defense-in-depth) spine that
-- feeds the Suspicious-activity feed (Panel B). DEFENSE-IN-DEPTH, NOT a
-- replacement for RLS: RLS (0001/0004/0008…0013) is the INLINE owner boundary;
-- this table only RECORDS suspicious signals it observes — it never blocks.
--
-- Authoritative source: MULTI_AGENT_COCKPIT.md §3 (security_event table shape),
-- §7 (the two security panels + the OWASP mapping), CLAUDE.md §1 (INV-1 synthetic,
-- INV-5 deny-by-default RLS + service_role server-only, INV-9 simulated v1 feed),
-- THREAT_MODEL.md §6 (D-RLS-1…7 — notably D-RLS-7: NO definer-rights helper in
-- the exposed schema).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0010/0013 doctrine.
-- ===========================================================================
--   1. `security_event` — one APPEND-ONLY row per suspicious-signal observation.
--      Each row records WHO (actor_kind / actor_ref), WHERE (surface — the
--      table/route), WHAT (signal — e.g. `anon_read_attempt`,
--      `user_id_reassign_attempt`, `auth_failure_burst`, `oversized_result`,
--      `rls_posture_regression`), how bad (severity), the OWASP category it maps
--      to (owasp — the §7 category id), and a free-form detail. INV-1: synthetic
--      only — `detail` carries NO PII and NO child key; `actor_ref` is a uid
--      string or NULL, never a name/email. This is the §7 Panel B feed; the v1
--      populate path is the app-layer middleware writing server-side via the
--      `service_role` repository (a SIMULATED/labeled stream, INV-9) — there is
--      deliberately NO public definer-rights helper (D-RLS-7).
--   2. RLS: `ENABLE` AND `FORCE` (D-RLS-1). `security_event` is a SYSTEM AUDIT
--      table (admin-read via service_role), NOT family-owned data, so — exactly
--      like 0013's `sales_agent` registry — its policy is the NULL-GUARDED
--      `auth.uid() IS NOT NULL` shape (the same guard the source-table policies
--      use). This keeps the global CREATE==ENABLE==FORCE + one-guard-per-policy
--      invariants (test_migrations_rls) green while anon (auth.uid() = NULL)
--      matches no row (D-RLS-3).
--   3. APPEND-ONLY: GRANT only SELECT + INSERT (NEVER UPDATE / DELETE to
--      anon/authenticated). An audit row is a FACT, immutable once written — so
--      there is deliberately NO UPDATE/DELETE policy and NO UPDATE/DELETE grant
--      (the identical immutable-once-written posture as 0010's voucher_event).
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) is the cockpit's append path
-- (the edge middleware records each observed signal server-side) AND the admin
-- cross-actor read path (the SecurityTab reads the feed); both are unaffected by
-- RLS/FORCE. service_role is NEVER client-exposed (INV-5).
--
-- CRITICAL (test_migrations_rls): a new table MUST ENABLE *and* FORCE RLS (the
-- table count must equal the ENABLE count and the FORCE count across all
-- migrations) and every policy must carry the `auth.uid()` null guard. D-RLS-7:
-- this migration contains NO definer-rights function in the public schema.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- security_event — the append-only suspicious-signal audit log (§3). Not
-- family-owned: a system audit table, admin-read via service_role. Every column
-- is metadata only — NO PII, NO child key (INV-1/INV-6).
-- ---------------------------------------------------------------------------
CREATE TABLE security_event (
    event_id     uuid PRIMARY KEY,

    -- When the signal was observed + the row's own create stamp. Append-only:
    -- neither is ever updated after insert.
    occurred_at  timestamptz DEFAULT now(),
    created_at   timestamptz DEFAULT now(),

    -- WHO triggered it: the principal's kind (anon | authenticated | service_role)
    -- and an optional actor reference (a uid string or NULL — NEVER a name/email).
    actor_kind   text NOT NULL CHECK (actor_kind IN ('anon', 'authenticated', 'service_role')),
    actor_ref    text,

    -- WHERE: the table/route the signal was observed on (e.g. '/security/posture').
    surface      text,

    -- WHAT: the signal class (e.g. 'anon_read_attempt', 'user_id_reassign_attempt',
    -- 'auth_failure_burst', 'oversized_result', 'rls_posture_regression').
    signal       text NOT NULL,

    -- How bad + the OWASP category id this signal maps to (§7 — e.g.
    -- 'API1:2023', 'A07:2021'). A free-form, PII-free detail string.
    severity     text,
    owasp        text,
    detail       text
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the policy (the test asserts FORCE-count == table-count).
ALTER TABLE security_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_event FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: a SYSTEM audit table (admin-read via service_role), not
-- family-owned data — expressed as a NULL-GUARDED policy (the same
-- `auth.uid() IS NOT NULL` guard shape as 0013's registry), so the global
-- one-guard-per-policy invariant stays green and anon (auth.uid() = NULL) matches
-- no row. No UPDATE/DELETE policy: the audit log is append-only (immutable once
-- written). The cross-actor admin read happens via service_role (BYPASSRLS).
CREATE POLICY security_event_authenticated_select ON security_event
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ===========================================================================
-- PostgREST role grants. APPEND-ONLY: SELECT (policy-gated, null-guarded — anon
-- matches no row, D-RLS-3) + INSERT (to `authenticated` only). NO UPDATE / DELETE
-- grant — the audit row is immutable. `service_role` (server-only, BYPASSRLS) is
-- the cockpit's append + cross-actor read path and is unaffected. No public
-- definer-rights helper exists (D-RLS-7): the feed populate path is the
-- app-layer service_role repository, NOT a definer-rights function in this schema.
-- ===========================================================================
GRANT SELECT ON security_event TO authenticated;
GRANT INSERT ON security_event TO authenticated;

-- 0004_force_rls.sql — S14 W1 hardening: FORCE ROW LEVEL SECURITY everywhere.
--
-- Authoritative source: THREAT_MODEL.md §6 (D-RLS-1), CLAUDE.md §1 (INV-5),
-- TODO.md S14 W1 ("ENABLE *and* FORCE ROW LEVEL SECURITY").
--
-- ===========================================================================
-- WHY (defense-in-depth, no behavior change for the app paths).
-- ===========================================================================
-- 0001/0003 already ENABLE RLS on every public table. ENABLE subjects all
-- non-owner roles to the policies, but the *table owner* role (the `postgres`
-- migration/admin role) is exempt unless RLS is also FORCED. FORCE closes that
-- gap: it makes even the owner role obey the owner-scoped, null-guarded policies,
-- so there is no "connect as the table owner and read every family" bypass.
--
-- This does NOT affect the real app paths:
--   * `service_role` has the BYPASSRLS role attribute (server-only, D-RLS-4),
--     which is independent of FORCE — the cockpit's cross-family read is intact.
--   * `authenticated` / `anon` are not the table owner, so they were already
--     fully governed by the policies; FORCE leaves them unchanged.
-- Net effect: the only thing FORCE removes is an implicit owner-role escape
-- hatch. Deny-by-default, all the way down.
-- ===========================================================================

ALTER TABLE family_record       FORCE ROW LEVEL SECURITY;
ALTER TABLE leads_new           FORCE ROW LEVEL SECURITY;
ALTER TABLE app_form            FORCE ROW LEVEL SECURITY;
ALTER TABLE enrollment_forms    FORCE ROW LEVEL SECURITY;
ALTER TABLE community_profiles  FORCE ROW LEVEL SECURITY;
ALTER TABLE apply_events        FORCE ROW LEVEL SECURITY;
ALTER TABLE proposals           FORCE ROW LEVEL SECURITY;
ALTER TABLE evals               FORCE ROW LEVEL SECURITY;
ALTER TABLE decisions           FORCE ROW LEVEL SECURITY;

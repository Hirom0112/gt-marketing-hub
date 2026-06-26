-- 0027_rbac.sql — B1: the RLS defense-in-depth leg of the three-role auth (audit
-- S1 fix). Two GLOBAL role-mapping tables + an authorize() helper and a custom
-- access-token hook, BOTH placed in a NON-exposed schema (`private`).
--
-- Authoritative source: TODO_v2.md §B1, PLAN_v2.md §B1, CLAUDE.md §1 (INV-5
-- deny-by-default RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7
-- — notably D-RLS-7: no definer-rights helper in the exposed schema),
-- ASSUMPTIONS.md A-37 (operational/global tables are NOT program-partitioned),
-- RESEARCH_v2 §II.5 (the Supabase RBAC pattern: role_permissions + user_roles +
-- an authorize() function in a private schema), and 0013/0015 (the global-registry
-- null-guarded-policy doctrine).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0013/0015 doctrine.
-- ===========================================================================
-- The cockpit's three operator roles (admin / leader / operator) need a tested,
-- in-database backstop behind the PRIMARY enforcement (the FastAPI principal — a
-- separate unit that reads the role off the JWT). This migration is that backstop:
--
--   (A) `app_role` — the three-role vocabulary as a Postgres enum.
--
--   (B) `user_roles` — maps an auth user to a role. A user may read their OWN role
--       rows (the user-scoped, null-guarded policy below). GLOBAL/cross-program:
--       roles are NOT program-scoped (ASSUMPTIONS A-37), so — unlike the 0024
--       tenant tables — this table carries NO program_id tag and NO restrictive
--       program-isolation policy. It is the same global posture as 0013's
--       sales_agent registry and 0015's security_event audit table.
--
--   (C) `role_permissions` — maps a role to a permission string. Every
--       authenticated app user may read the lookup (a null-guarded read policy).
--       `permission` is `text` (NOT an enum): the permission VOCABULARY's one
--       canonical home is the app layer (app/core/authz.py — a separate unit);
--       pinning an enum here would fork that vocabulary (INV-11). The table is the
--       data; the words live in core.
--
--   (D) `private.authorize(requested_permission)` — a definer-rights helper that
--       reads the caller's role off the JWT app_metadata claim and returns whether
--       that role binds the requested permission. It is defined in the NON-exposed
--       `private` schema (NOT `public`): a definer-rights helper in the
--       PostgREST-reachable public schema would be an internet-facing privilege
--       bypass (D-RLS-7). `set search_path = ''` forces every reference to be
--       schema-qualified (no search-path hijack).
--
--   (E) `private.custom_access_token_hook(event jsonb)` — the Supabase
--       Custom-Access-Token hook: it injects the user's role into the JWT
--       `app_metadata` so the FastAPI principal (and the in-DB policies) can read
--       it. Also in the `private` schema; granted EXECUTE to `supabase_auth_admin`
--       only, revoked from everyone else.
--
-- Doctrine: each table both enables and forces row-level security (D-RLS-1) and
-- carries a null-guarded policy (D-RLS-2/D-RLS-3), keeping the global
-- CREATE==ENABLE==FORCE + one-guard-per-policy invariants (test_migrations_rls)
-- green (this migration adds +2 tables / +2 enable / +2 force). The definer-rights
-- helpers live OUTSIDE the exposed schema (D-RLS-7). `service_role` (server-only,
-- bypass-rls, D-RLS-4) is unaffected and is never client-exposed (INV-5).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) The three-role enum vocabulary. Idempotent (re-running the migration is a
-- no-op) so a re-applied migration does not error on an existing type.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'app_role') THEN
        CREATE TYPE app_role AS ENUM ('admin', 'leader', 'operator');
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- (B) user_roles — auth user → role. GLOBAL (cross-program, A-37): no program_id.
-- A user may read their OWN role rows. UNIQUE(user_id, role) ⇒ no duplicate grant.
-- ---------------------------------------------------------------------------
CREATE TABLE user_roles (
    id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The auth user the role is granted to (auth.uid()). No FK to auth.users here,
    -- matching the codebase's family_record.user_id convention (RLS keys on
    -- auth.uid(), not a DB FK).
    user_id uuid NOT NULL,
    role    app_role NOT NULL,
    UNIQUE (user_id, role)
);

-- ---------------------------------------------------------------------------
-- (C) role_permissions — role → permission. GLOBAL lookup. `permission` is text:
-- the permission vocabulary's canonical home is app/core/authz.py (INV-11).
-- UNIQUE(role, permission) ⇒ no duplicate binding.
-- ---------------------------------------------------------------------------
CREATE TABLE role_permissions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role       app_role NOT NULL,
    permission text NOT NULL,
    UNIQUE (role, permission)
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the policies (the test asserts force-count == table-count).
ALTER TABLE user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles FORCE ROW LEVEL SECURITY;
ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Null-guarded policies (D-RLS-2/D-RLS-3). anon (auth.uid() = NULL) matches no
-- row. Writes stay deny-by-default (role grants are performed server-side via
-- service_role / migrations — no anon/authenticated write policy).
-- ---------------------------------------------------------------------------
-- A user reads ONLY their own role rows (user-scoped + null-guarded).
CREATE POLICY user_roles_owner_select ON user_roles
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND user_id = (SELECT auth.uid())
    );

-- The role→permission lookup is readable by any authenticated app user
-- (null-guarded — the same guard shape as 0013's registry).
CREATE POLICY role_permissions_authenticated_select ON role_permissions
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- ---------------------------------------------------------------------------
-- (D) The private (non-exposed) schema that holds the definer-rights helpers.
-- Keeping them OUT of `public` is the D-RLS-7 requirement: a definer-rights
-- function in the PostgREST-reachable schema would be an internet-facing bypass.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS private;

-- private.authorize(requested_permission) — returns whether the caller's JWT role
-- binds the requested permission. Definer-rights (so it can read the role tables
-- regardless of the caller's grants) but placed in the NON-exposed `private`
-- schema; `set search_path = ''` forces fully-qualified references (no hijack).
CREATE OR REPLACE FUNCTION private.authorize(requested_permission text)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    bind_permissions integer;
    user_role public.app_role;
BEGIN
    -- The caller's role, read off the JWT app_metadata claim the hook injected.
    user_role := (SELECT (auth.jwt() -> 'app_metadata' ->> 'role')::public.app_role);

    SELECT count(*)
      INTO bind_permissions
      FROM public.role_permissions
     WHERE role_permissions.permission = requested_permission
       AND role_permissions.role = user_role;

    RETURN bind_permissions > 0;
END;
$$;

-- (E) private.custom_access_token_hook(event) — the Supabase Custom-Access-Token
-- hook. It injects the user's role into the JWT app_metadata so the FastAPI
-- principal (and the in-DB policies) can read it. Definer-rights so it can read
-- user_roles; in the NON-exposed `private` schema; search_path pinned empty.
CREATE OR REPLACE FUNCTION private.custom_access_token_hook(event jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    claims jsonb;
    user_role public.app_role;
BEGIN
    SELECT role
      INTO user_role
      FROM public.user_roles
     WHERE user_id = (event ->> 'user_id')::uuid
     LIMIT 1;

    claims := event -> 'claims';

    IF user_role IS NOT NULL THEN
        claims := jsonb_set(claims, '{app_metadata, role}', to_jsonb(user_role));
    ELSE
        claims := jsonb_set(claims, '{app_metadata, role}', 'null'::jsonb);
    END IF;

    event := jsonb_set(event, '{claims}', claims);
    RETURN event;
END;
$$;

-- ===========================================================================
-- Grants. authorize() is callable by the app roles (it self-gates on the JWT
-- role); the access-token hook is callable ONLY by supabase_auth_admin (the auth
-- server), revoked from everyone else. The role tables grant SELECT only (writes
-- stay deny-by-default / service_role). app_runtime (NOBYPASSRLS, 0024) reads
-- under RLS like the other tables.
-- ===========================================================================
GRANT USAGE ON SCHEMA private TO authenticated, anon;
GRANT EXECUTE ON FUNCTION private.authorize(text) TO authenticated, anon;

GRANT USAGE ON SCHEMA private TO supabase_auth_admin;
GRANT EXECUTE ON FUNCTION private.custom_access_token_hook(jsonb) TO supabase_auth_admin;
REVOKE EXECUTE ON FUNCTION private.custom_access_token_hook(jsonb) FROM authenticated, anon, public;

GRANT SELECT ON user_roles TO authenticated;
GRANT SELECT ON role_permissions TO authenticated;

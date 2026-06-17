-- 0007_owner_delete.sql — S18 apply-replica "My Applications" dashboard: let an
-- authenticated applicant DELETE their OWN application (ASSUMPTIONS.md A-24).
--
-- Authoritative source: ASSUMPTIONS.md A-24 (apply-replica → cockpit), CLAUDE.md
-- §1 (INV-1/INV-5/INV-6), THREAT_MODEL.md §6 (D-RLS-1…7, deny-by-default RLS).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), all consistent with the 0001/0003 doctrine.
-- ===========================================================================
-- The S18 dashboard ("My Applications") lets a returning applicant manage and
-- DELETE an application they created. 0001 + 0003 granted `authenticated` only
-- SELECT + INSERT (writes were deny-by-default; no DELETE), so a client delete
-- would be denied — correct under deny-by-default, but it blocks the legitimate
-- owner-scoped delete the dashboard needs. This migration adds the missing
-- capability WITHOUT weakening the doctrine:
--
--   1. Owner-scoped, NULL-GUARDED *DELETE* policies on the spine + the source
--      tables the apply flow writes, so an authenticated applicant may delete
--      ONLY rows they own — exactly the same ownership predicate as the existing
--      INSERT/SELECT policies:
--        * family_record  → `(SELECT auth.uid()) = user_id`           (the spine)
--        * leads_new / app_form / enrollment_forms / community_profiles /
--          apply_events → `family_id IN (owned families)`             (children)
--      Each carries the same `(SELECT auth.uid()) IS NOT NULL` guard as 0001
--      (D-RLS-2/D-RLS-3) — the explicit guard that closes the `null = user_id`
--      IDOR trap. NO `FOR ALL` policy is used (DELETE only; INSERT/SELECT remain
--      governed by their own 0001/0003 policies, untouched).
--   2. `GRANT DELETE` to `authenticated` ONLY on those six tables. `anon`
--      (unauthenticated, auth.uid() = NULL) matches no WITH-CHECK/USING clause
--      and is NOT granted DELETE at all (D-RLS-3).
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) is UNAFFECTED — it never
-- relied on these policies and remains the cockpit's cross-family path. This
-- migration adds NO table, does NOT re-toggle RLS, and weakens nothing: it is
-- purely additive owner-scoped DELETE policies + grants. The application's
-- delete order (dependents first, family_record last) respects the family_id FKs.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Spine: the applicant deletes their own family_record (user_id = auth.uid()),
-- null-guarded (D-RLS-2/D-RLS-3). Children are removed first by the client, so
-- the family_id FKs are satisfied when the spine row is deleted.
-- ---------------------------------------------------------------------------
CREATE POLICY family_record_owner_delete ON family_record
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- ---------------------------------------------------------------------------
-- Source tables: ownership scoped through the owned family_record, null-guarded.
-- (Identical ownership predicate to the 0001/0003 SELECT/INSERT policies.)
-- ---------------------------------------------------------------------------
CREATE POLICY leads_new_owner_delete ON leads_new
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY app_form_owner_delete ON app_form
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY enrollment_forms_owner_delete ON enrollment_forms
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY community_profiles_owner_delete ON community_profiles
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE POLICY apply_events_owner_delete ON apply_events
    FOR DELETE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- PostgREST role grants. DELETE is granted to `authenticated` ONLY (anon, being
-- unauthenticated, has auth.uid() = NULL and matches no USING clause — and is
-- not granted DELETE at all). `service_role` (server-only) is unaffected.
-- ===========================================================================
GRANT DELETE ON
    family_record, leads_new, app_form, enrollment_forms, community_profiles,
    apply_events
TO authenticated;

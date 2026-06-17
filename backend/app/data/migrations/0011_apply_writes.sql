-- 0011_apply_writes.sql — the two apply-SPA write paths that were deny-by-default:
--   (A) the per-child `student` INSERT path 0009 deliberately DEFERRED, and
--   (B) the `family_record` UPDATE path the apply SPA needs to set `funding_type`.
--
-- Authoritative source: TODO.md (apply-SPA child write + funding_type update),
-- ASSUMPTIONS.md A-24 (apply-replica → cockpit), ARCHITECTURE.md §4.1/§4.1b,
-- CLAUDE.md §1 (INV-1/INV-5/INV-6/INV-11), THREAT_MODEL.md §6 (D-RLS-1…7), §9.
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), all consistent with the 0001/0003/0007/0009
-- doctrine — purely additive owner-scoped write policies + grants.
-- ===========================================================================
-- The apply SPA now (1) inserts `student` rows (anon+RLS) and (2) UPDATEs
-- `family_record.funding_type` (anon+RLS), but the matching write policies did
-- NOT exist, so both were denied-by-default in production (INV-5). 0009 added
-- `student` SELECT+DELETE only and EXPLICITLY deferred the child-write INSERT
-- policy to "a separate task" (its §2 note) — that task is this migration. 0001/
-- 0007 gave `family_record` INSERT+SELECT+DELETE only (no UPDATE). This migration
-- adds the missing capability WITHOUT weakening the doctrine:
--
--   (A) `student` owner-scoped, NULL-GUARDED *INSERT* policy — the doctrine-legal
--       child-write path 0009 deferred. Ownership is scoped through
--       family_id → family_record.user_id (the IDENTICAL ownership subquery
--       0009's SELECT/DELETE policies use), with a null-guarded WITH CHECK so the
--       authenticated applicant may insert a child ONLY into a family they own.
--       NO student/child key targeting — family_id is the parent/household owner
--       (INV-6/COPPA). GRANT INSERT(student) to `authenticated` only.
--
--   (B) `family_record` owner-scoped, NULL-GUARDED *UPDATE* policy — so the SPA
--       can write the family's `funding_type`. Mirrors 0007's owner-DELETE
--       predicate but FOR UPDATE, which needs BOTH clauses:
--         * USING       gates the rows visible to update (the owner's own rows);
--         * WITH CHECK  gates the POST-IMAGE so the owner cannot reassign
--                       `user_id` away (privilege escalation guard).
--       Both carry the same `(SELECT auth.uid()) IS NOT NULL` guard as 0001
--       (D-RLS-2/D-RLS-3) — the explicit guard that closes the `null = user_id`
--       IDOR trap. NO `FOR ALL` policy (UPDATE only; SELECT/INSERT/DELETE remain
--       governed by their own 0001/0003/0007 policies, untouched).
--
-- LEAST-PRIVILEGE NOTE (the GRANT UPDATE choice): Postgres column-scoped UPDATE
-- grants (`GRANT UPDATE (funding_type) ON family_record`) are possible, but a
-- COLUMN grant only narrows WHICH columns a role may name in SET — it does NOT
-- add per-row scoping (the RLS USING/WITH CHECK policy is what gates rows). A
-- column grant would silently break if the SPA later legitimately updates another
-- owner-writable column, and it splits the privilege story across two mechanisms.
-- We pick the more boring/safe option: a TABLE-level `GRANT UPDATE ON
-- family_record TO authenticated`, fully gated by the owner-scoped UPDATE policy
-- below — the policy (not the grant) is the security boundary, and it forbids
-- touching any row the caller does not own AND forbids re-owning a row away. This
-- matches how 0003/0007 grant table-level INSERT/DELETE gated by their policies.
--
-- RLS + FORCE stay AS-IS: this migration adds NO table and does NOT re-toggle or
-- re-FORCE RLS on the existing tables — the CREATE==ENABLE==FORCE counts are
-- unchanged (test_migrations_rls). `service_role` (BYPASSRLS, server-only,
-- D-RLS-4) is the cockpit's cross-family path and is unaffected. anon
-- (unauthenticated, auth.uid() = NULL) matches no WITH CHECK/USING clause and is
-- granted NEITHER write (D-RLS-3): writes from anon stay deny-by-default.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) student owner-scoped, null-guarded INSERT (the child-write path 0009
--     deferred). Ownership scoped through family_id → family_record.user_id —
--     the IDENTICAL predicate as 0009's student SELECT/DELETE policies.
-- ---------------------------------------------------------------------------
CREATE POLICY student_owner_insert ON student
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ---------------------------------------------------------------------------
-- (B) family_record owner-scoped, null-guarded UPDATE (so the SPA can set
--     funding_type). Mirrors 0007's owner-DELETE predicate but FOR UPDATE, with
--     BOTH USING (rows visible to update) and WITH CHECK (post-image owner guard).
-- ---------------------------------------------------------------------------
CREATE POLICY family_record_owner_update ON family_record
    FOR UPDATE
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- ===========================================================================
-- PostgREST role grants. INSERT(student) + UPDATE(family_record) are granted to
-- `authenticated` ONLY (anon, being unauthenticated, has auth.uid() = NULL and
-- matches no WITH CHECK/USING clause — and is not granted either write at all,
-- D-RLS-3). `service_role` (server-only, BYPASSRLS) is unaffected. The table-
-- level UPDATE grant is fully gated by the owner-scoped policy above (see the
-- least-privilege note in the header).
-- ===========================================================================
GRANT INSERT ON student TO authenticated;
GRANT UPDATE ON family_record TO authenticated;

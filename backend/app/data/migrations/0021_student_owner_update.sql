-- 0021_student_owner_update.sql — let an applicant LINK a child to its own
-- application/enrollment packet (the per-child apply write path, A-24).
--
-- Authoritative source: A-24 (per-child grain), THREAT_MODEL.md §6 (D-RLS owner
-- scoping), CLAUDE.md §1 (INV-5 owner-scoped, null-guarded RLS; INV-6 a child is
-- only ever written by the household that owns it).
--
-- ===========================================================================
-- WHY THIS MIGRATION EXISTS
-- ===========================================================================
-- 0011 gave the applicant student INSERT (add a child) but NOT student UPDATE, so
-- the SPA could create a child yet never link it to its own app_form / enrollment
-- packet (set student.app_form_id / enrollment_form_id, or its per-child
-- funding_type). The per-child apply flow needs that link so the cockpit can embed
-- each child's OWN funnel. This adds the owner-scoped, null-guarded UPDATE policy +
-- grant — the SAME ownership predicate as 0009/0011's student INSERT/SELECT/DELETE
-- (a child belongs to a family the applicant owns). No column-scoping: the policy,
-- not the grant, is the security boundary (the 0011 least-privilege note). anon is
-- NOT granted UPDATE (auth.uid() NULL matches no USING/CHECK). No new table; RLS on
-- student stays deny-by-default (INV-5).
-- ===========================================================================

create policy student_owner_update on student
    for update
    using (
        (select auth.uid()) is not null
        and family_id in (
            select fr.family_id
            from family_record fr
            where fr.user_id = (select auth.uid())
        )
    )
    with check (
        (select auth.uid()) is not null
        and family_id in (
            select fr.family_id
            from family_record fr
            where fr.user_id = (select auth.uid())
        )
    );

grant update on student to authenticated;

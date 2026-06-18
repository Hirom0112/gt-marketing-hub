-- 0019_student_packet_fks.sql — add the per-child packet foreign keys so the
-- cockpit can embed each child's own application + enrollment under the student.
--
-- Authoritative source: A-24 (per-child grain), ARCHITECTURE.md §4 (data model),
-- CLAUDE.md §1 (INV-5 owner-scoped RLS; this only adds referential integrity).
--
-- ===========================================================================
-- WHY THIS MIGRATION EXISTS
-- ===========================================================================
-- 0009 created `student.app_form_id` / `student.enrollment_form_id` as PLAIN uuid
-- columns (only `family_id` got a foreign key). The cockpit's per-child board reads
-- each child WITH its own application/enrollment as a to-one PostgREST embed
-- (`student … app_form(*), enrollment_forms(*)`). PostgREST resolves an embed ONLY
-- through a declared foreign key — with none present it returns
-- `PGRST200: could not find a relationship between 'student' and 'app_form'`, so
-- `GET /students` 500s against the live database and the deal view's per-child
-- section silently shows nothing (it fails safe on the error). The board only ever
-- worked against the in-memory repo (which joins in Python, no PostgREST embed).
--
-- This adds the two missing to-one foreign keys so the embed resolves. There is no
-- ambiguity: the only `student`↔`app_form` relationship is `student.app_form_id →
-- app_form` (the `app_form.student_id` grain marker from 0018 carries NO foreign
-- key, by design), and likewise for enrollment_forms. ON DELETE SET NULL keeps a
-- child row if its packet is ever removed. Idempotent (guarded). No new table /
-- policy: RLS on `student` is unchanged (INV-5).
-- ===========================================================================

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'student_app_form_id_fkey'
    ) then
        alter table student
            add constraint student_app_form_id_fkey
            foreign key (app_form_id) references app_form (app_form_id) on delete set null;
    end if;

    if not exists (
        select 1 from pg_constraint where conname = 'student_enrollment_form_id_fkey'
    ) then
        alter table student
            add constraint student_enrollment_form_id_fkey
            foreign key (enrollment_form_id)
            references enrollment_forms (enrollment_form_id) on delete set null;
    end if;
end $$;

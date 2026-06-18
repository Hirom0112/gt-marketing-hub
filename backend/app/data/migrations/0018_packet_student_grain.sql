-- 0018_packet_student_grain.sql — mark per-CHILD application/enrollment packets
-- on the household source tables so the family (household) deal grain never
-- borrows a child's progress.
--
-- Authoritative source: ARCHITECTURE.md §4.1/§4.3/§4.4 (the source tables),
-- A-24 (the per-child student grain), CLAUDE.md §1 (INV-1 synthetic-only, INV-5
-- deny-by-default RLS, INV-6 no child-keyed targeting).
--
-- ===========================================================================
-- WHY THIS MIGRATION EXISTS
-- ===========================================================================
-- 0009 added the per-child `student` table, where each child references its OWN
-- application + enrollment packet via `student.app_form_id` / `enrollment_form_id`.
-- Those child packets live in the SAME `app_form` / `enrollment_forms` tables as
-- the household packet — all sharing `family_id`. The tables had no way to mark a
-- row as a child's, so a PostgREST family embed (`enrollment_forms(*)`) returned
-- the household packet AND every child's packet together. For a MULTI-CHILD
-- household the read layer could then surface a child's (possibly fully-signed)
-- packet as the household's — making the household misderive as forms-cleared /
-- `tuition` / `recovered`. (Symptom: the one 2-child demo household dropped out of
-- the active triage queue.)
--
-- This adds a nullable `student_id` grain marker to both source tables — already
-- present on the `app_form` / `enrollment_forms` MODELS — so the household grain is
-- exactly the rows with `student_id IS NULL`, and a child's packet is the row whose
-- `student_id` is set. It is a plain nullable column (NOT a hard FK): the seed
-- writes packets before students, and the existing FK direction is
-- student → packet, so a reverse hard FK would be circular. NULL = household grain.
--
-- All synthetic (INV-1). No new table, no new policy: the columns inherit the
-- existing owner-scoped, deny-by-default RLS on `app_form` / `enrollment_forms`
-- (INV-5) — nothing about row visibility changes, this only labels grain.
-- ===========================================================================

alter table app_form add column if not exists student_id uuid;
alter table enrollment_forms add column if not exists student_id uuid;

comment on column app_form.student_id is
    'NULL = household application packet; set = the child''s own packet (A-24 grain).';
comment on column enrollment_forms.student_id is
    'NULL = household enrollment packet; set = the child''s own packet (A-24 grain).';

-- Backfill the marker on any child packet already referenced by a student row, so
-- existing data (incl. the live demo cohort) is corrected without a re-seed.
update app_form a
   set student_id = s.student_id
  from student s
 where s.app_form_id = a.app_form_id
   and a.student_id is null;

update enrollment_forms e
   set student_id = s.student_id
  from student s
 where s.enrollment_form_id = e.enrollment_form_id
   and e.student_id is null;

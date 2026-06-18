-- 0020_sis_status_student_grain.sql — make the SIS reconcile verdict PER-CHILD.
--
-- Authoritative source: A-24 (per-child grain), THREAT_MODEL.md §6/§9 (the SIS PII
-- firewall — only family_id/student_id/present/confirmed_at/bucket cross, never a
-- child name/DOB/grade), CLAUDE.md §1 (INV-5 owner-scoped RLS, INV-6 no child-keyed
-- targeting — student_id is an OPAQUE uuid, not child PII).
--
-- ===========================================================================
-- WHY THIS MIGRATION EXISTS
-- ===========================================================================
-- 0014 keyed sis_status by family_id alone (ONE verdict per household). The
-- reconcile now attributes a household's verdict to EACH enrolled child under it
-- (the match is still household-contact-only — a child is never matched on its own
-- data, INV-6), so a paid household with two children carries two per-child
-- verdicts. That needs a per-(family_id, student_id) row.
--
-- This adds a nullable `student_id` grain marker (NULL = a household-grain verdict,
-- back-compat) and replaces the single-column family_id primary key with two
-- PARTIAL unique indexes: one row per (family_id, student_id) for child verdicts,
-- and at most one household-grain row per family. The old household-grain rows are
-- cleared (nothing READS the table — the admin panel recomputes live; the rows are
-- re-seeded per-child). No new table, no new policy: the owner-scoped, deny-by-
-- default RLS on sis_status (family_id-based) is unchanged (INV-5) — student_id only
-- labels grain, it does not widen visibility.
-- ===========================================================================

alter table sis_status add column if not exists student_id uuid;

comment on column sis_status.student_id is
    'NULL = household-grain SIS verdict; set = the verdict attributed to one enrolled child (A-24, opaque uuid — not child PII).';

-- Clear the pre-per-child household rows (re-seeded per child; no live reader).
delete from sis_status;

-- Drop the single-column primary key; replace with partial uniques covering both
-- grains. (PostgREST reads/inserts fine without a single PK; the seed clears+inserts.)
do $$
begin
    if exists (select 1 from pg_constraint where conname = 'sis_status_pkey') then
        alter table sis_status drop constraint sis_status_pkey;
    end if;
end $$;

create unique index if not exists sis_status_family_student
    on sis_status (family_id, student_id) where student_id is not null;
create unique index if not exists sis_status_family_household
    on sis_status (family_id) where student_id is null;

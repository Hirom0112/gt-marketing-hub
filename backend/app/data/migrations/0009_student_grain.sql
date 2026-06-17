-- 0009_student_grain.sql — TODO.md R1: the live per-child `student` grain.
--
-- Authoritative source: TODO.md R1 (household→child grain), ASSUMPTIONS.md A-24
-- (per-child funnel), ARCHITECTURE.md §4.1b (the `student` model), CLAUDE.md §1
-- (INV-1/INV-5/INV-6), THREAT_MODEL.md §6 (D-RLS-1…7), §9 (minors/COPPA).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0001/0003/0007 doctrine.
-- ===========================================================================
-- Today the live Supabase DB is FAMILY-level: there is no `student` table, and the
-- apply SPA's "Add Another Child" forks a NEW `family_record`. This migration
-- introduces the real household→child grain so children are CHILDREN of a
-- household, not separate families. The household identity key is
-- `family_record.user_id` (the existing ownership root, ARCHITECTURE.md §4.1 /
-- D-RLS-2) — NO new `household_id` column is added (least churn). A `student` is
-- a child of one `family_record` (its `family_id`); all children sharing a
-- `user_id` are one household.
--
--   1. `student` — one row per CHILD, carrying the per-child funnel state that
--      §4.1b (`models.py` :class:`Student`) models: its own `current_stage`
--      placeholder + `stall_reason`/`stalled_since`, `funding_type`/
--      `funding_state`, and its own `app_form_id`/`enrollment_form_id`. The
--      stored `current_stage` is a WRITE-TIME PLACEHOLDER; the cockpit re-derives
--      each child's stage on read with the same pure stage machine the family
--      path uses (A-24 M2). Child identity fields are synthetic-named (INV-1):
--      `synthetic_first_name`. `grade` and `display_label` are non-PII labels.
--      Minimal + synthetic-shaped (INV-1); NO precise geo / behavioral key of a
--      minor (INV-6/COPPA).
--   2. RLS: `ENABLE` AND `FORCE` (D-RLS-1) + owner-scoped, null-guarded SELECT
--      and owner DELETE policies, scoped through the owned `family_record.user_id`
--      subquery — the IDENTICAL ownership predicate as the 0001 SELECT / 0007
--      DELETE policies on the other `family_id`-owned source tables. (No INSERT
--      policy here: the apply-SPA child-write path is a SEPARATE task; until then
--      writes from anon/authenticated stay deny-by-default, D-RLS-1.)
--   3. GRANTs: SELECT + DELETE to `authenticated` (and SELECT to `anon`, matching
--      0001 — anon still matches no row under the null guard, D-RLS-3).
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) is the cockpit's cross-family
-- read path for the per-child board and is unaffected by RLS/FORCE.
--
-- CRITICAL (test_migrations_rls): a new table MUST ENABLE *and* FORCE RLS
-- (the table count must equal the ENABLE count and the FORCE count across all
-- migrations) and carry a null-guarded policy.
--
-- BACKFILL (cloud-side, run by the director — NOT applied here): existing live
-- "one family_record per child" rows are migrated by, for each set of
-- family_record rows sharing a `user_id` (the household), keeping the earliest
-- (by created_at) family_record as the household spine and inserting a `student`
-- row per child that points its `family_id` at that surviving spine — carrying
-- the child's stage/funding/app/enrollment keys. The redundant per-child
-- family_record rows (and their orphaned source rows) are then re-parented or
-- pruned. No PII moves (all fields are already synthetic, INV-1).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- §4.1b student — one child's per-child funnel within a household (A-24).
-- Ownership scoped through family_id → family_record.user_id (the household key).
-- ---------------------------------------------------------------------------
CREATE TABLE student (
    student_id          uuid PRIMARY KEY,
    -- The household this child belongs to (FamilyRecord.family_id). Ownership is
    -- scoped through this FK to family_record.user_id — NOT a separate household
    -- column (the user_id IS the household key).
    family_id           uuid NOT NULL REFERENCES family_record (family_id),

    -- Non-PII labels (INV-1): a human display label + grade band. The child's
    -- name field is synthetic-named so a real value can never silently land here.
    display_label        text NOT NULL,
    synthetic_first_name text NOT NULL,
    grade                text NOT NULL,

    -- Per-child funnel state. `current_stage` is a WRITE-TIME PLACEHOLDER; the
    -- cockpit re-derives each child's stage on read (A-24 M2).
    current_stage       stage NOT NULL,
    stall_reason        stall_reason,
    stalled_since       timestamptz,

    funding_type        funding_type,
    funding_state       funding_state NOT NULL DEFAULT 'none',

    -- One application + one enrollment packet PER CHILD (A-24); nullable until
    -- the related per-child row exists.
    app_form_id         uuid,
    enrollment_form_id  uuid,

    crm_seam_status     seam_status NOT NULL DEFAULT 'unsynced',
    crm_synced_at       timestamptz,
    work_queue_score    numeric,

    created_at          timestamptz DEFAULT now(),
    updated_at          timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the owner-scoped policies (the test asserts FORCE-count == table-count).
ALTER TABLE student ENABLE ROW LEVEL SECURITY;
ALTER TABLE student FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: owner-scoped read via family_record.user_id, null-guarded.
-- Identical ownership predicate to the 0001 source-table SELECT policies.
CREATE POLICY student_owner_select ON student
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- D-RLS-2 / D-RLS-3: the authenticated owner may delete their OWN child row
-- (mirrors 0007's owner DELETE on the other family_id-owned tables), null-guarded.
CREATE POLICY student_owner_delete ON student
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
-- PostgREST role grants. SELECT to anon/authenticated (policy-gated, null-guarded
-- — anon matches no row, D-RLS-3); DELETE to `authenticated` ONLY (anon is not
-- granted DELETE). `service_role` (server-only, BYPASSRLS) is the cockpit's
-- cross-family read path and is unaffected.
-- ===========================================================================
GRANT SELECT ON student TO anon, authenticated;
GRANT DELETE ON student TO authenticated;

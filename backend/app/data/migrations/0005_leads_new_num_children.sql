-- 0005_leads_new_num_children.sql — reconcile leads_new with the A-23 value model.
--
-- Authoritative source: backend/app/data/models.py (LeadsNew.num_children),
-- ASSUMPTIONS.md A-23 (enrollment value = num_children × tuition), TODO.md S14.
--
-- ===========================================================================
-- WHY.
-- ===========================================================================
-- `leads_new` carries `num_children` in the data model (models.py) and the A-23
-- enrollment value model multiplies it by tuition to rank families. But the
-- 0001 baseline migration predates A-23, so the column was never in the DDL —
-- a real repo/model drift. The S14 cloud project surfaced it: an insert that
-- mirrors models.py needs the column to exist. This migration adds it so the
-- migrations are a faithful, reproducible source of truth for the schema
-- (INV-11 single canonical home).
--
-- Shape mirrors models.py: `num_children: int = Field(default=1, ge=1)` ⇒
-- NOT NULL DEFAULT 1 with a `>= 1` CHECK. Idempotent (ADD COLUMN IF NOT EXISTS +
-- guarded ADD CONSTRAINT) so it is safe to apply to a DB that already has it
-- (the S14 cloud project) and to a fresh provision alike. No RLS/policy change.
-- ===========================================================================

ALTER TABLE leads_new
    ADD COLUMN IF NOT EXISTS num_children integer NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_new_num_children_positive'
    ) THEN
        ALTER TABLE leads_new
            ADD CONSTRAINT leads_new_num_children_positive CHECK (num_children >= 1);
    END IF;
END$$;

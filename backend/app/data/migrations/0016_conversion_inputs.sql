-- 0016_conversion_inputs.sql — reconcile the schema with the DH-1/DH-2 conversion signal.
--
-- Authoritative source: backend/app/data/models.py (LeadsNew.neighborhood,
-- AppForm.self_reported_income), TODO.md DEMO-HARDENING DH-1/DH-2, CLAUDE.md §1
-- (INV-1 synthetic, INV-6/P-4 no precise minor geo), ARCHITECTURE.md §8.
--
-- ===========================================================================
-- WHY.
-- ===========================================================================
-- DH-1 added the deterministic conversion-likelihood signal (deal view), scored
-- over neighborhood affluence + self-reported income + #children + funding +
-- depth. DH-2 added the two raw inputs to the data model — `LeadsNew.neighborhood`
-- and `AppForm.self_reported_income` — but, like 0005's `num_children`, the 0001
-- baseline predates them, so the columns were never in the DDL: a real repo/model
-- drift. A cloud-backed cockpit (COCKPIT_REPO=supabase) reads `*`, so without these
-- columns the signal silently falls back to the pydantic defaults
-- (neighborhood='Unspecified', income=NULL) and scores everyone the same. This
-- migration adds them so the migrations remain a faithful, reproducible source of
-- truth for the schema (INV-11 single canonical home) and the live conversion
-- signal computes on the real seeded values.
--
-- Shapes mirror models.py exactly:
--   * LeadsNew.neighborhood: str = "Unspecified"  ⇒ text NOT NULL DEFAULT 'Unspecified'.
--     A coarse AGGREGATE area LABEL only — never precise geo of a minor (P-4/INV-6).
--   * AppForm.self_reported_income: int | None = None  ⇒ integer NULL (no default;
--     NULL = not yet provided, the documented "unknown, not low" state).
-- Idempotent (ADD COLUMN IF NOT EXISTS) so it is safe on a fresh provision and on a
-- DB that already has the columns. No RLS/policy change: the new columns inherit
-- the existing FORCE-RLS, owner-scoped, null-guarded policies on their tables
-- (leads_new, app_form) from 0001/0004 — they carry no new ownership surface.
-- ===========================================================================

ALTER TABLE leads_new
    ADD COLUMN IF NOT EXISTS neighborhood text NOT NULL DEFAULT 'Unspecified';

ALTER TABLE app_form
    ADD COLUMN IF NOT EXISTS self_reported_income integer;

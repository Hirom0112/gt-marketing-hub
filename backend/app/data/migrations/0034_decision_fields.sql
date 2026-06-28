-- 0034_decision_fields.sql — Module 11 Phase 1: promote the Decision-Queue
-- spec-fields from inside the `payload` jsonb to FIRST-CLASS columns on the
-- existing `decision` table (0028). A manual "raise" carries structured fields a
-- leader reads at a glance (the question, who raised it, the workstream, the
-- recommendation, an optional dollar ask, a due date, a priority); auto-flag
-- sources (budget variance, open-data enrichment) keep writing `payload`, so this
-- is purely ADDITIVE and back-compatible.
--
-- Authoritative source: CLAUDE.md §1 (INV-1 synthetic-only, INV-5 deny-by-default
-- RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-*), 0028_decisions.sql
-- (the table + its leader-gated RLS this migration EXTENDS), and the additive
-- ALTER-only posture of 0006/0013/0023.
--
-- DOCTRINE / RLS: this is an ALTER-only change on the already RLS-enabled,
-- already-FORCEd `decision` table (0028). It adds no relation, toggles no
-- row-security, and defines no new policy — so 0028's leader-gated read/decide +
-- program-isolation rules govern the new columns UNCHANGED (the additive posture
-- of 0006/0023). A plain ADD COLUMN inherits the table's existing PostgREST grants
-- (0028 granted SELECT/INSERT/UPDATE on `decision` to authenticated, app_runtime),
-- so the new columns are readable/writable by the same roles with NO redundant
-- re-grant. service_role (server-only, BYPASSRLS, D-RLS-4) is unaffected.
--
-- Every column is NULLABLE or carries a sensible DEFAULT so existing rows stay
-- valid (the back-compat mandate). All values are synthetic / operational (INV-1):
-- `raised_by` is the VERIFIED principal's uid/role token stamped server-side at the
-- route layer — NEVER a client-supplied name.

-- The decision's name / question (the headline a leader reads). Nullable: an
-- auto-flag row leaves it NULL and derives a display question from `payload`.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS question text;

-- WHO raised it — the verified principal's uid/role token (server-stamped at the
-- route, never a client claim; INV-1). Nullable for rows predating the field.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS raised_by text;

-- The workstream the decision belongs to (content / grassroots / field_events /
-- budget / admissions / nurture — the valid set's canonical home is the app layer,
-- app/data/decisions_store.py WORKSTREAMS, INV-11). Nullable.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS workstream text;

-- The raiser's recommendation (what they propose the leader do). Nullable.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS recommendation text;

-- An OPTIONAL dollar ask (double precision to match the budget/goals money columns
-- of 0030/0033). NULL ⇒ no money attached to this decision.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS budget_ask double precision;

-- An OPTIONAL due date (date-only; the deadline a leader should decide by). NULL ⇒
-- no deadline.
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS due_date date;

-- The decision's priority. NOT NULL with a 'normal' default so existing rows are
-- valid; constrained to the same valid set the app enforces (urgent | normal —
-- canonical home app/data/decisions_store.py PRIORITIES, INV-11).
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('urgent', 'normal'));

-- When the decision LEFT the open state (the deciding instant). NULL while open;
-- set by the store on the first transition out of open (decided / in_flight).
ALTER TABLE decision
    ADD COLUMN IF NOT EXISTS resolution_date timestamptz;

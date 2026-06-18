-- 0017_lead_assignment.sql — LEAD_ASSIGNMENT.md (§10): the deterministic
-- lead-assignment data model. Adds (1) synthetic routing attributes on the
-- family spine (`state`, `income_tier`, `reported_rep_id`), (2) an append-only
-- ownership history timeline (`lead_assignment`), and (3) a server-only
-- round-robin cursor (`assignment_cursor`).
--
-- Authoritative source: LEAD_ASSIGNMENT.md (§3 owner-match, §7 weighted RR +
-- cursor, §10 data model, §13 privacy reconciliation), CLAUDE.md §1 (INV-1
-- synthetic-only, INV-5 deny-by-default RLS, INV-6 no child-keyed data, INV-11
-- one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0010/0013/0014 doctrine.
-- ===========================================================================
--   (A) family_record.state — a SYNTHETIC, coarse US-state code (e.g. 'FL'/'CA')
--       the territory rule routes on (§4). It is an aggregate region label, NOT a
--       ZIP / lat-long / precise geo of a minor (INV-6) — a 2-letter state never
--       trips the PII-scan ZIP/geo regex (§13). Synthetic only (INV-1).
--
--   (B) family_record.income_tier — a SYNTHETIC, 3-value BUCKET enum mirroring
--       GT's real household-income segmentation (<$65K / $65K–$160K / >$160K).
--       Deliberately a BUCKET, never the raw `household_income` figure: it cannot
--       form the C-SYN-2 PII cluster signature (a real name + household_income +
--       ZIP on one row) the PII-scan gate forbids, because there is no raw income
--       column and no `household_income` token here (§13; THREAT_MODEL.md §5.2).
--
--   (C) family_record.reported_rep_id — the SELF-REPORTED prior agent the
--       applicant names on the apply form (§3). FK → sales_agent. The applicant
--       may set this on their OWN row (anon+RLS, via the existing 0011
--       family_record owner-UPDATE policy, like funding_type); the SERVER then
--       PROMOTES a resolved value to `assigned_rep_id` via service_role. The
--       client never writes `assigned_rep_id` directly — that stays the cockpit's
--       service-role write (INV-5 IDOR guard; the two identities stay distinct).
--
--   (D) lead_assignment — an APPEND-ONLY ownership history (who→who, when, why),
--       modeled on the 0010 voucher_event timeline. The mutable
--       family_record.assigned_rep_id answers "who owns it now?"; this timeline
--       answers "what is the ownership history?" — two questions, two homes. A
--       reassignment is a fact in time, never an overwrite (the audit trail the
--       old in-memory-only spine could not durably keep). Owner-scoped SELECT
--       (null-guarded) via family_record.user_id; INSERT is service_role-only
--       (the cockpit assigns) — NO authenticated INSERT (unlike voucher_event, an
--       applicant never appends an assignment event). NO UPDATE/DELETE: immutable.
--
--   (E) assignment_cursor — the per-pool round-robin cursor (§7). Server-only
--       routing state (pool_key → next_index), written by the cockpit under
--       service_role. RLS is enabled AND forced with NO policy and NO client
--       grant ⇒ deny-all to anon/authenticated (it is not family-owned data);
--       service_role (BYPASSRLS) is the only reader/writer.
--
-- RLS doctrine (test_migrations_rls): each new relation ENABLEs AND FORCEs RLS so
-- the table-owner role obeys the policy too (the table/ENABLE/FORCE counts stay
-- equal), and every policy carries exactly one `auth.uid() IS NOT NULL` null
-- guard. The two family_record columns are ALTERs (no new relation), so they do
-- not change the relation count and reuse the existing 0011 owner-UPDATE policy.
-- `service_role` (server-only, BYPASSRLS, D-RLS-4) performs every assignment
-- write and is unaffected by RLS/FORCE.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A)(B)(C) Synthetic routing attributes on the family spine. NULLABLE so rows
-- that predate the field stay valid (non-breaking). income_tier is CHECK-bounded
-- to the three GT buckets (the enum vocabulary, INV-11 — not a magic string set).
-- ---------------------------------------------------------------------------
ALTER TABLE family_record ADD COLUMN state text;
ALTER TABLE family_record ADD COLUMN income_tier text
    CHECK (income_tier IN ('lt_65k', '65k_160k', 'gt_160k'));
ALTER TABLE family_record ADD COLUMN reported_rep_id uuid REFERENCES sales_agent (agent_id);

-- ---------------------------------------------------------------------------
-- (D) lead_assignment — append-only ownership history. One row per assignment /
-- reassignment event. from_rep_id NULL ⇒ first assignment out of the intake
-- pool; to_rep_id NULL ⇒ unassigned back to the pool. routed_role records the
-- closer|qualifier role the family routed as (a structural label, not PII).
-- ---------------------------------------------------------------------------
CREATE TABLE lead_assignment (
    assignment_id uuid PRIMARY KEY,
    family_id     uuid NOT NULL REFERENCES family_record (family_id),
    from_rep_id   uuid REFERENCES sales_agent (agent_id),
    to_rep_id     uuid REFERENCES sales_agent (agent_id),
    routed_role   text CHECK (routed_role IN ('closer', 'qualifier')),
    -- WHO performed it (the operator/admin or 'router') and WHY (the §2 reason
    -- string — the human-readable rule trace). reason is NOT NULL: every
    -- assignment is explainable (the deterministic-and-explainable mandate).
    assigned_by   text NOT NULL,
    reason        text NOT NULL,
    -- Correlates one auto-route / bulk-assign group (mirrors enrollment.py
    -- _batch_id). NULL for a single SLA reassignment.
    batch_id      text,
    occurred_at   timestamptz DEFAULT now(),
    created_at    timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation AND force so the table-owner role obeys
-- the policy too (the test asserts the relation/ENABLE/FORCE counts stay equal).
ALTER TABLE lead_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_assignment FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: a family may read ITS OWN ownership history (owner-scoped
-- via the spine's user_id), null-guarded so anon (auth.uid() = NULL) matches no
-- row. Same child-table owner-scope shape as 0010/0014. No INSERT policy: the
-- cockpit appends via service_role (BYPASSRLS) — an applicant never writes an
-- assignment event. No UPDATE/DELETE: the timeline is immutable.
CREATE POLICY lead_assignment_owner_select ON lead_assignment
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ---------------------------------------------------------------------------
-- (E) assignment_cursor — per-pool round-robin cursor (§7). Server-only routing
-- state: pool_key (a stable hash of the sorted eligible-agent-id list) → the next
-- ring index. Written by the cockpit under service_role. RLS enabled AND forced
-- with NO policy and NO client grant ⇒ deny-all to anon/authenticated (this is
-- not family-owned data); service_role (BYPASSRLS) is the only accessor.
-- ---------------------------------------------------------------------------
CREATE TABLE assignment_cursor (
    pool_key   text PRIMARY KEY,
    next_index integer NOT NULL DEFAULT 0,
    updated_at timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default AND force. No policy ⇒ no anon/authenticated row is
-- ever visible or writable; only service_role (BYPASSRLS) reads/writes the cursor.
ALTER TABLE assignment_cursor ENABLE ROW LEVEL SECURITY;
ALTER TABLE assignment_cursor FORCE ROW LEVEL SECURITY;

-- ===========================================================================
-- PostgREST role grants. lead_assignment: SELECT to `authenticated` only
-- (policy-gated, null-guarded — anon matches no row, D-RLS-3); NO write grant
-- (service_role appends, D-RLS-4); append-only (no UPDATE/DELETE). assignment_cursor:
-- NO grant at all (service_role-only server state). The family_record column
-- writes reuse the existing 0011 table-level UPDATE grant + owner-scoped policy.
-- ===========================================================================
GRANT SELECT ON lead_assignment TO authenticated;

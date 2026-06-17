-- 0010_voucher_events.sql — TODO.md R2: the append-only `voucher_event` timeline.
--
-- Authoritative source: TODO.md R2 (voucher_event append-only timeline),
-- ENROLLMENT_REFACTOR.md §6 Phase 2 ("voucher_event append-only timeline
-- (time-in-state, feeds work-queue deadline ranking)"), ARCHITECTURE.md §10
-- (observability — every state transition logged + queryable), CLAUDE.md §1
-- (INV-1/INV-5/INV-6/INV-10), THREAT_MODEL.md §6 (D-RLS-1…7), §9 (minors/COPPA).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0001/0003/0009 doctrine.
-- ===========================================================================
-- Today `family_record.funding_state` is a single scalar — the funding lifecycle
-- has no per-(family/student) history, so "how long has this family sat AWARDED
-- but not reconfirmed?" (the §6 reconfirm-gap pain) cannot be answered, and the
-- §10 observability spine has no record of WHEN a voucher state changed or WHICH
-- GT-controlled signal drove it. This migration introduces the missing TIMELINE:
--
--   1. `voucher_event` — one APPEND-ONLY row per voucher state TRANSITION, per
--      (family / optionally student). It records the `from_state` → `to_state`
--      hop, the `program` (e.g. `tx_tefa`), and the GT-controlled `signal` name
--      that drove it (e.g. `family_selected`, `first_installment_received`) —
--      INV-10: GT-controlled signals only, NEVER an Odyssey/voucher API. The
--      timeline gives the work-queue its deadline/time-in-state ranking input and
--      gives §10 an audit trail of every funding-state change. NO PII, NO child
--      BEHAVIORAL key (INV-1/INV-6): `student_id` is a household→child FK only
--      (nullable: a household-level vs a per-child transition), never a behavioral
--      profile; there is no name/email/geo/typed-value column here.
--   2. RLS: `ENABLE` AND `FORCE` (D-RLS-1) + owner-scoped, null-guarded SELECT
--      and INSERT policies, scoped through the owned `family_record.user_id`
--      subquery — the IDENTICAL ownership predicate the 0003 INSERT / 0009 SELECT
--      policies use for the other `family_id`-owned tables. The INSERT policy
--      carries a null-guarded WITH CHECK (an authenticated owner may append ONLY
--      events for a family they own).
--   3. APPEND-ONLY: GRANT only SELECT + INSERT (NEVER UPDATE / DELETE to
--      anon/authenticated). The timeline is immutable once written — a state
--      transition is a fact, not an editable row — so there is deliberately NO
--      UPDATE/DELETE policy and NO UPDATE/DELETE grant.
--
-- `service_role` (BYPASSRLS, server-only, D-RLS-4) is the cockpit's append path
-- (the funding-transition API writes the event after a successful, legal advance,
-- INV-2: the deterministic core owns the transition; this only logs the fact) and
-- its cross-family read path; both are unaffected by RLS/FORCE.
--
-- CRITICAL (test_migrations_rls): a new table MUST ENABLE *and* FORCE RLS (the
-- table count must equal the ENABLE count and the FORCE count across all
-- migrations) and every policy must carry the `auth.uid()` null guard.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- voucher_event — append-only per-(family/student) voucher state-transition log.
-- Ownership scoped through family_id → family_record.user_id (the household key).
-- ---------------------------------------------------------------------------
CREATE TABLE voucher_event (
    voucher_event_id    uuid PRIMARY KEY,

    -- The household this transition belongs to (FamilyRecord.family_id). Ownership
    -- is scoped through this FK to family_record.user_id — the same household key
    -- 0009 uses (no separate household_id column).
    family_id           uuid NOT NULL REFERENCES family_record (family_id),
    -- Optional per-child scope (household-level transition when NULL). A
    -- household→child FK only, NEVER a behavioral key of a minor (INV-1/INV-6).
    student_id          uuid REFERENCES student (student_id),

    -- The state hop. `from_state` is NULL for an origin event (no prior state);
    -- `to_state` is the state the transition landed in (always present).
    from_state          funding_state,
    to_state            funding_state NOT NULL,

    -- The voucher program (e.g. 'tx_tefa') and the GT-controlled signal name that
    -- drove the transition (e.g. 'family_selected'). INV-10: GT-controlled signals
    -- only — never an Odyssey/voucher external feed.
    program             text,
    signal              text,

    -- When the transition occurred (time-in-state ranking input) + the row's own
    -- create stamp. Append-only: neither is ever updated after insert.
    occurred_at         timestamptz DEFAULT now(),
    created_at          timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the owner-scoped policies (the test asserts FORCE-count == table-count).
ALTER TABLE voucher_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE voucher_event FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: owner-scoped read via family_record.user_id, null-guarded.
-- Identical ownership predicate to the 0001/0009 source-table SELECT policies.
CREATE POLICY voucher_event_owner_select ON voucher_event
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- D-RLS-2 / D-RLS-3: the authenticated owner may APPEND an event ONLY for a family
-- they own (null-guarded WITH CHECK), mirroring 0003's owner INSERT policies. No
-- UPDATE/DELETE policy: the timeline is append-only (immutable once written).
CREATE POLICY voucher_event_owner_insert ON voucher_event
    FOR INSERT
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- PostgREST role grants. APPEND-ONLY: SELECT (policy-gated, null-guarded — anon
-- matches no row, D-RLS-3) + INSERT (to `authenticated` ONLY; anon, being
-- unauthenticated, has auth.uid() = NULL and matches no WITH CHECK). NO UPDATE /
-- DELETE grant — the timeline is immutable. `service_role` (server-only,
-- BYPASSRLS) is the cockpit's append + cross-family read path and is unaffected.
-- ===========================================================================
GRANT SELECT ON voucher_event TO anon, authenticated;
GRANT INSERT ON voucher_event TO authenticated;

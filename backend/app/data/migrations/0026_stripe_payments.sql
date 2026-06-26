-- 0026_stripe_payments.sql — A3: the Stripe webhook's two append-only,
-- program-scoped tables (the webhook itself is built separately; this migration
-- only adds the schema).
--
-- Authoritative source: PLAN_v2.md §A3, TODO_v2.md §A3, CLAUDE.md §1 (INV-5
-- deny-by-default RLS, INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7),
-- app/core/program.py (the canonical Program enum), RESEARCH_v2 §II.2 (Stripe
-- webhook idempotency — log processed event ids, don't reprocess), 0024 (the
-- program-tenancy doctrine) and 0010/0015 (the append-only-ledger doctrine).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0024/0025 + 0010/0015
-- doctrine.
-- ===========================================================================
--   (A) `stripe_events` — the inbound-event DEDUPE LEDGER (idempotency). The Stripe
--       webhook inserts the inbound `evt_…` id and SKIPS an already-seen id, so a
--       redelivered event is processed exactly once (RESEARCH_v2 §II.2: log the
--       event ids you've processed, don't reprocess logged events). The id is the
--       table's natural key — `event_id text PRIMARY KEY` — so a duplicate insert
--       conflicts on the PK and is a no-op. `object_id` (the `data.object.id`)
--       supports the rarer two-Event dedupe; `event_type`, `received_at`, and the
--       `program_id` tenancy tag round it out.
--
--   (B) `payment` — the money LEDGER: one row per fulfilled payment, keyed to the
--       source Stripe `event_id` and the family (`family_id` FK→family_record, the
--       same nullable-FK shape 0013 uses for assigned_rep_id). Records
--       `amount_cents`, `currency`, `status`, and the `program_id` tenancy tag.
--
--   (C) RLS: each table `ENABLE`s AND `FORCE`s row-level security (D-RLS-1).
--       Following A1/A2 (0024/0025), the single policy per table is the
--       `AS RESTRICTIVE` program-isolation policy keyed on the caller's
--       `app_metadata.program_id` JWT claim AND carrying the
--       `(SELECT auth.uid()) IS NOT NULL` null guard (D-RLS-2/D-RLS-3): the rule is
--       "authenticated AND in-program". This keeps the global
--       CREATE==ENABLE==FORCE + one-guard-per-policy invariants (test_migrations_rls)
--       green (this migration adds +2 tables / +2 ENABLE / +2 FORCE) while anon
--       (auth.uid() = NULL) matches no row.
--
--   (D) APPEND-ONLY (the 0010/0015 posture): GRANT only SELECT + INSERT — NEVER
--       UPDATE / DELETE — and NO UPDATE/DELETE policy. Both ledgers are facts,
--       immutable once written. The webhook writes server-side via the
--       `service_role` (BYPASSRLS, server-only, D-RLS-4), never client-exposed
--       (INV-5).
--
-- Doctrine preserved: no security-definer helper in the exposed schema (D-RLS-7).
--
-- CRITICAL (test_migrations_rls): these two new tables MUST each ENABLE *and*
-- FORCE row-level security (the table count must equal the enable count and the
-- force count across all migrations) and each policy carries the auth.uid() null
-- guard.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- (A) stripe_events — the inbound-event dedupe ledger (idempotency). Program-
-- tenanted like 0024/0025. `event_id` (the Stripe `evt_…` id) is the natural PK,
-- so a redelivered event conflicts and is skipped. Operational webhook state, not
-- family-owned data.
-- ---------------------------------------------------------------------------
CREATE TABLE stripe_events (
    -- The inbound Stripe event id (`evt_…`). PRIMARY KEY ⇒ a redelivered event
    -- conflicts on insert and is a no-op (exactly-once processing).
    event_id    text PRIMARY KEY,

    -- The Stripe event type (e.g. 'checkout.session.completed').
    event_type  text NOT NULL,

    -- The `data.object.id` (e.g. the PaymentIntent / Session id) — supports the
    -- rarer two-Event dedupe (two events for the same underlying object).
    object_id   text,

    -- When the webhook received/recorded the event.
    received_at timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). The NOT NULL DEFAULT pins existing/new
    -- rows to the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id  text NOT NULL DEFAULT 'fall_enrollment'
);

-- ---------------------------------------------------------------------------
-- (B) payment — the money ledger: one row per fulfilled payment, keyed to the
-- source Stripe event and the family. Program-tenanted like 0024/0025.
-- ---------------------------------------------------------------------------
CREATE TABLE payment (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The owning family (FK→family_record, nullable — same shape 0013 uses for
    -- assigned_rep_id; a payment may land before the family row is matched).
    family_id    uuid REFERENCES family_record (family_id),

    -- The source Stripe event that produced this payment row.
    event_id     text,

    -- The amount in the currency's minor unit (cents) + the ISO currency code.
    amount_cents bigint NOT NULL,
    currency     text NOT NULL,

    -- The payment status (e.g. 'succeeded').
    status       text NOT NULL,

    created_at   timestamptz NOT NULL DEFAULT now(),

    -- Program tenancy tag (matches 0024). The NOT NULL DEFAULT pins existing/new
    -- rows to the canonical Fall program (Program.FALL_ENROLLMENT, INV-11).
    program_id   text NOT NULL DEFAULT 'fall_enrollment'
);

-- D-RLS-1: deny-by-default at creation time, AND force so even the table-owner
-- role obeys the policy (the test asserts FORCE-count == table-count).
ALTER TABLE stripe_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE stripe_events FORCE ROW LEVEL SECURITY;
ALTER TABLE payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- RESTRICTIVE program-isolation policies (the 0024/0025 pattern): the caller must
-- be authenticated (auth.uid() null guard, D-RLS-3) AND in the row's program
-- (app_metadata.program_id == program_id). FOR ALL with USING (read/update/delete
-- visibility) + WITH CHECK (insert/update post-image) so neither a read nor a
-- write can cross the program boundary. service_role (BYPASSRLS, server-only) is
-- the webhook's write path and is unaffected.
-- ---------------------------------------------------------------------------
CREATE POLICY stripe_events_program_isolation ON stripe_events
    AS RESTRICTIVE
    FOR ALL
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

CREATE POLICY payment_program_isolation ON payment
    AS RESTRICTIVE
    FOR ALL
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    )
    WITH CHECK (
        (SELECT auth.uid()) IS NOT NULL
        AND ((SELECT auth.jwt() -> 'app_metadata' ->> 'program_id') = program_id)
    );

-- ===========================================================================
-- PostgREST role grants. APPEND-ONLY: SELECT + INSERT only — NO UPDATE / DELETE
-- grant on either ledger (both are immutable once written, the 0010/0015 posture).
-- All still bounded by RLS (app_runtime is NOBYPASSRLS, 0024). The webhook writes
-- via service_role (server-only, BYPASSRLS), which is unaffected.
-- ===========================================================================
GRANT SELECT, INSERT ON stripe_events TO app_runtime;
GRANT SELECT, INSERT ON payment TO app_runtime;

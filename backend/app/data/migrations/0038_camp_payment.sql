-- 0038_camp_payment.sql — Module 4 (Summer Camp) STRIPE CAMP-REVENUE: the collected
-- camp-revenue ledger. One row per camp PaymentIntent the Stripe webhook fulfills
-- (metadata.program == 'summer_camp'), so `GET /summer/reconcile` can read REAL
-- collected revenue (sum of succeeded payments, by campus) instead of the synthetic
-- paid × price estimate.
--
-- Authoritative source: app/core/program.py (the canonical Program.SUMMER_CAMP value),
-- 0032_summer_camp.sql / 0037_summer_camp_v2.sql (the camp tenant + program_id default
-- + per-program RLS this migration mirrors EXACTLY), 0026 (the payment-ledger doctrine),
-- CLAUDE.md §1 (INV-1 no PII, INV-5 deny-by-default RLS, INV-6 aggregate-only minors,
-- INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0032/0037 doctrine.
-- ===========================================================================
--   camp_payment — one row per fulfilled camp PaymentIntent. IDEMPOTENT on the Stripe
--   PaymentIntent id (the PK): recording the same PI twice (Stripe's at-least-once
--   redelivery) is a no-op merge, never a double-count. PROGRAM-SCOPED exactly like
--   0032/0037's camp tables:
--     (A) program_id text NOT NULL DEFAULT 'summer_camp' — Program.SUMMER_CAMP
--         (INV-11's one home; a SQL migration cannot read params/params.yaml, exactly
--         as 0032 pins the same literal inline).
--     (B) RLS ENABLE *and* FORCE (D-RLS-1) + TWO null-guarded policies (D-RLS-2): a
--         PERMISSIVE authenticated-read (the aggregate revenue rows the server reads;
--         no per-applicant owner column) AND a RESTRICTIVE program-isolation policy
--         keyed on app_metadata.program_id (USING + WITH CHECK), AND-ed on top — the
--         IDENTICAL shape as 0032/0037.
--
-- INV-1 / INV-6 (no PII): camp_payment carries ONLY synthetic/aggregate fields — the
-- Stripe PI id, an aggregate campus label, the amount (minor units), currency, status,
-- and the source Stripe event id. NO family/child/household identity is stored here.
--
-- D-RLS-7: no SECURITY DEFINER helper (inline predicates only). service_role
-- (BYPASSRLS, server-only) is the webhook write path and is unaffected.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- camp_payment — the collected camp-revenue ledger (idempotent on the PI id).
-- ---------------------------------------------------------------------------
CREATE TABLE camp_payment (
    payment_id      text PRIMARY KEY,                       -- the Stripe PaymentIntent id (pi_…); idempotent key
    campus          text,                                   -- aggregate campus label (from PI metadata.campus)
    amount_cents    integer,                                -- charge amount in the currency's minor unit
    currency        text,                                   -- ISO currency code (e.g. 'usd')
    status          text,                                   -- the PI status ('succeeded' …)
    stripe_event_id text,                                   -- the source Stripe event.id (evt_…) for the audit trail
    program_id      text NOT NULL DEFAULT 'summer_camp',    -- Program.SUMMER_CAMP (INV-11)
    created_at      timestamptz DEFAULT now()
);

ALTER TABLE camp_payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE camp_payment FORCE ROW LEVEL SECURITY;

-- D-RLS-2: authenticated-read reference (the aggregate revenue rows every camp seat may
-- read; no per-applicant owner column), null-guarded — the same shape as 0032's campus /
-- 0037's camp_session. anon matches no row (D-RLS-3).
CREATE POLICY camp_payment_authenticated_read ON camp_payment
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
    );

-- D-RLS-2: RESTRICTIVE program isolation (AND-ed on top), mirroring 0032/0037 exactly.
CREATE POLICY camp_payment_program_isolation ON camp_payment
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
-- PostgREST role grants. SELECT to anon/authenticated (policy-gated, null-guarded —
-- anon matches no row, D-RLS-3). NO INSERT/UPDATE/DELETE grant: the payment ledger is
-- written by the server-only service_role (BYPASSRLS) webhook path, so rows stay
-- deny-by-default for clients (INV-5).
-- ===========================================================================
GRANT SELECT ON camp_payment TO anon, authenticated;

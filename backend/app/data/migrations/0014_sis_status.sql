-- 0014_sis_status.sql — MULTI_AGENT_COCKPIT.md §6, TODO.md M5: the SIS reconcile
-- verdict table. The daily reconcile job (server-side, service_role) writes one
-- row per family with its bucket; the family-facing status page reads its OWN row
-- (anon+RLS) to show "Closed — pending SIS confirmation" (MD).
--
-- Authoritative source: MULTI_AGENT_COCKPIT.md §6 (SIS reconcile buckets),
-- CLAUDE.md §1 (INV-1 synthetic-only, INV-5 deny-by-default RLS, INV-6 no
-- child-keyed data), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- THE PII FIREWALL (INV-1/INV-6): this table is, BY DESIGN, the only thing that
-- crosses from the SIS roster into the cockpit. It carries ONLY the reconcile
-- outcome — `family_id`, `present`, `confirmed_at`, `bucket` — and NEVER a child
-- name / DOB / grade / any roster PII. The reconcile core matches on the
-- household contact (email/phone), never on a child key; nothing about a minor
-- is stored or surfaced (the test_sis_buckets PII-firewall test enforces this).
--
-- WRITES are server-side only: the daily reconcile job runs under `service_role`
-- (BYPASSRLS, D-RLS-4). There is NO anon/authenticated INSERT/UPDATE/DELETE
-- policy or grant — writes stay deny-by-default. The single SELECT policy lets a
-- family read its OWN status row (the family status page), owner-scoped exactly
-- like the source tables.
--
-- CRITICAL (test_migrations_rls): one new table ⇒ one ENABLE + one FORCE row-
-- level-security statement, and every policy carries exactly one auth.uid() guard.
-- ===========================================================================

CREATE TABLE sis_status (
    -- One verdict per family (re-run = upsert by PK). FK to the spine.
    family_id     uuid PRIMARY KEY REFERENCES family_record (family_id),
    -- Did the SIS roster carry a match for this paid family at all?
    present       boolean NOT NULL,
    -- When the SIS confirmed the enrollment (NULL until/unless confirmed).
    confirmed_at  timestamptz,
    -- The reconcile bucket — mirrors core SisBucket (INV-11 vocab, not a magic
    -- string set: the four reconcile outcomes).
    bucket        text NOT NULL
        CHECK (bucket IN ('confirmed', 'records_lag', 'paid_not_in_sis', 'ambiguous')),
    -- When the daily reconcile job last wrote this verdict.
    reconciled_at timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation AND force so the table-owner role obeys
-- the policy too (the test asserts CREATE==ENABLE==FORCE counts stay equal).
ALTER TABLE sis_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE sis_status FORCE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: a family may read ITS OWN status row (owner-scoped via the
-- spine's user_id), null-guarded so anon (auth.uid() = NULL) matches no row.
-- Same child-table owner-scope shape as 0009's student_owner_select.
CREATE POLICY sis_status_owner_select ON sis_status
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- PostgREST role grant. SELECT to `authenticated` (policy-gated, null-guarded —
-- anon matches no row, D-RLS-3). No write grant: the reconcile job writes via
-- service_role (D-RLS-4), writes stay deny-by-default for clients.
GRANT SELECT ON sis_status TO authenticated;

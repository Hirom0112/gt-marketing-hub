-- 0022_household_guardians.sql — persist BOTH parents/guardians on the ONE
-- household (A-36). The apply form has always collected a "Parent / Guardian #1"
-- relationship and an optional "Parent / Guardian #2"; until now those choices
-- were telemetry-only and never stored. This adds the columns so the household
-- spine carries guardian #1's relationship and a SECOND synthetic contact
-- (name + email + relationship) when the applicant lists one.
--
-- Authoritative source: ARCHITECTURE.md §4.1 (the family spine), CLAUDE.md §1
-- (INV-1 synthetic-only, INV-5 deny-by-default RLS, INV-6 no child-keyed data,
-- INV-11 one canonical home), THREAT_MODEL.md §6 (D-RLS-1…7).
--
-- ===========================================================================
-- WHAT THIS MIGRATION ADDS (and why), consistent with the 0017 doctrine.
-- ===========================================================================
--   (A) family_record.guardian_1_relationship — the SELF-REPORTED relationship of
--       the PRIMARY contact to the child (mother/father/guardian/…). A structural
--       pick from a closed option set on the apply form, never free text. NULL ⇒
--       a row that predates the field (e.g. seeded marketing leads).
--
--   (B) family_record.secondary_contact_name / secondary_contact_synthetic_email /
--       guardian_2_relationship — an OPTIONAL second parent on the SAME household.
--       Both name and email are SYNTHETIC (INV-1): the email mirrors the primary
--       contact's reserved @example.invalid domain so the same firewall covers it.
--       These are HOUSEHOLD-grained — keyed by family_id, NOT by a child (INV-6);
--       a guardian is never tied to a student_id. NULL ⇒ no second guardian listed.
--
-- This is an ALTER-only change on an existing, already owner-scoped table: no new
-- table, no new policy. The owner-scoped row-security on family_record (auth.uid()
-- = user_id) already governs these columns — a family edits its own guardians and
-- no others (INV-5). The deterministic core remains the only writer of derived
-- state; guardians are applicant-supplied inputs on the family's own row.

ALTER TABLE public.family_record
    ADD COLUMN IF NOT EXISTS guardian_1_relationship        text,
    ADD COLUMN IF NOT EXISTS secondary_contact_name         text,
    ADD COLUMN IF NOT EXISTS secondary_contact_synthetic_email text,
    ADD COLUMN IF NOT EXISTS guardian_2_relationship        text;

-- The second contact's email, when present, must sit in the reserved synthetic
-- domain — the same constraint 0003 placed on the primary contact (NULL allowed,
-- since the second guardian is optional).
ALTER TABLE public.family_record
    ADD CONSTRAINT family_record_secondary_synthetic_email
    CHECK (
        secondary_contact_synthetic_email IS NULL
        OR secondary_contact_synthetic_email LIKE '%@example.invalid'
    );

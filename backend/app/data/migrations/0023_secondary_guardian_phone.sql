-- 0023_secondary_guardian_phone.sql — add the SECOND guardian's synthetic phone
-- (DECISIONS.md D-6, admin-dashboard redesign). 0022_household_guardians added the
-- optional second contact's name / email / relationship on the household spine, but
-- not a phone. The redesigned admin detail panel (§2 Contact) surfaces BOTH parents'
-- phone numbers, so the second guardian needs one too.
--
-- Authoritative source: ARCHITECTURE.md §4.1 (the family spine), CLAUDE.md §1
-- (INV-1 synthetic-only, INV-5 deny-by-default RLS, INV-6 no child-keyed data).
--
-- Like the rest of the household-guardian columns this is HOUSEHOLD-grained (keyed
-- by family_id, never a student_id — INV-6), SYNTHETIC (INV-1; seeded from the
-- 555-01xx fictitious NANP block), and NULLABLE so rows predating the field stay
-- valid. ALTER-only on the already owner-scoped family_record table: the existing
-- auth.uid() = user_id row-security already governs it (INV-5), no new policy.

ALTER TABLE public.family_record
    ADD COLUMN IF NOT EXISTS secondary_contact_synthetic_phone text;

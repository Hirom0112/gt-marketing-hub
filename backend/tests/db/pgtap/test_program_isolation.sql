-- test_program_isolation.sql — A1 headline proof (pgTAP / basejump test helpers).
--
-- Runs against a LIVE local Postgres with the migrations + the basejump pgTAP
-- helpers installed: `pg_prove backend/tests/db/pgtap/test_program_isolation.sql`.
-- It is committed for the live demo and is NOT exercised by the Python suite (this
-- env has no local Postgres/pg_prove); the unconditional build-time guard is
-- tests/unit/test_migrations_rls.py::test_program_id_restrictive_isolation.
--
-- VERIFIED PASSING 2026-06-27 against the local Supabase stack (1..4 all ok).
-- Bootstrap (no pg_prove needed — run directly via psql):
--   psql "$DB" -c 'CREATE EXTENSION IF NOT EXISTS pgtap;'
--   # basejump helpers (the `tests.*` fns): fetch supabase_test_helpers--0.0.6.sql
--   # from github.com/usebasejump/supabase-test-helpers, strip the `\echo/\quit`
--   # extension guard, and apply the body:
--   #   grep -vE '^\\(echo|quit)' sth.sql | psql "$DB" -f -
--   psql "$DB" -Xqt -f backend/tests/db/pgtap/test_program_isolation.sql
--
-- Proves the two A1 boundaries hold for an authenticated CLIENT principal:
--   (1) PROGRAM isolation — a principal whose app_metadata.program_id =
--       'fall_enrollment' sees the seeded FALL row and ZERO summer_camp rows
--       (the RESTRICTIVE claim policy), and
--   (2) the retained OWNER isolation — a second user sees ZERO of the first
--       user's rows (D-RLS-5, the closed IDOR), even within the same program.
--
-- All identities/data are synthetic (@example.invalid) — INV-1.

BEGIN;

SELECT plan(4);

-- --- Two synthetic principals (basejump helper mints auth.users rows). --------
SELECT tests.create_supabase_user('prog_a_user');   -- the in-program (fall) family
SELECT tests.create_supabase_user('prog_b_user');   -- a second owner, same program

-- --- Seed, as the privileged migration/owner role, one row per program. -------
-- We bypass RLS for the seed only (service_role / table owner), then drop back to
-- the authenticated principal to PROVE the policy from the client's seat.
SELECT tests.clear_authentication();
SET LOCAL ROLE postgres;

INSERT INTO family_record
    (family_id, user_id, display_name, primary_contact_synthetic_email,
     current_stage, attribution_source, program_id)
VALUES
    -- A's FALL row (owned by prog_a_user, fall_enrollment program)
    ('00000000-0000-0000-0000-0000000000a1',
     tests.get_supabase_uid('prog_a_user'),
     'Synthetic Fall Family', 'iso-fall@example.invalid',
     'interest', 'a1-isolation-seed', 'fall_enrollment'),
    -- A CAMP row in the SAME ownership (so only program_id differs) — must be
    -- invisible to the fall principal purely on the program claim.
    ('00000000-0000-0000-0000-0000000000a2',
     tests.get_supabase_uid('prog_a_user'),
     'Synthetic Camp Family', 'iso-camp@example.invalid',
     'interest', 'a2-isolation-seed', 'summer_camp'),
    -- B's FALL row (owned by prog_b_user) — same program as A, different owner.
    ('00000000-0000-0000-0000-0000000000b1',
     tests.get_supabase_uid('prog_b_user'),
     'Synthetic B Family', 'iso-b@example.invalid',
     'interest', 'b1-isolation-seed', 'fall_enrollment');

-- ===========================================================================
-- (1) PROGRAM isolation — authenticate as A with a fall_enrollment program claim.
-- ===========================================================================
-- basejump authenticate_as sets role=authenticated + sub; we additionally inject
-- the app_metadata.program_id claim the RESTRICTIVE policy keys on.
SELECT tests.authenticate_as('prog_a_user');
SELECT set_config(
    'request.jwt.claims',
    json_build_object(
        'sub', tests.get_supabase_uid('prog_a_user')::text,
        'role', 'authenticated',
        'app_metadata', json_build_object('program_id', 'fall_enrollment')
    )::text,
    true
);

SELECT is(
    (SELECT count(*) FROM family_record WHERE program_id = 'summer_camp')::int,
    0,
    'program isolation: a fall principal sees ZERO summer_camp rows (RESTRICTIVE claim)'
);

SELECT ok(
    (SELECT count(*) FROM family_record WHERE family_id =
        '00000000-0000-0000-0000-0000000000a1') >= 1,
    'program isolation: a fall principal CAN see its own in-program (fall) row'
);

-- ===========================================================================
-- (2) OWNER isolation retained — A (fall claim) must see ZERO of B's rows even
-- though B's row is in the SAME (fall) program: owner-scoped permissive AND
-- RESTRICTIVE program policy are AND-ed.
-- ===========================================================================
SELECT is(
    (SELECT count(*) FROM family_record WHERE family_id =
        '00000000-0000-0000-0000-0000000000b1')::int,
    0,
    'owner isolation (D-RLS-5): user A sees ZERO of user B''s rows (the closed IDOR)'
);

-- Authenticate as B (also a fall principal) — B sees its own row, not A's.
SELECT set_config(
    'request.jwt.claims',
    json_build_object(
        'sub', tests.get_supabase_uid('prog_b_user')::text,
        'role', 'authenticated',
        'app_metadata', json_build_object('program_id', 'fall_enrollment')
    )::text,
    true
);

SELECT is(
    (SELECT count(*) FROM family_record WHERE user_id =
        tests.get_supabase_uid('prog_a_user'))::int,
    0,
    'owner isolation (D-RLS-5): user B sees ZERO of user A''s rows'
);

SELECT * FROM finish();

ROLLBACK;

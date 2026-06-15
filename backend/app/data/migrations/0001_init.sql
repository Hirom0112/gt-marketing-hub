-- 0001_init.sql — GT Growth Cockpit initial schema (S0).
--
-- Authoritative source: ARCHITECTURE.md §4.1–§4.5 (Family Record spine + source
-- tables), §4.8 (enumerations), §4.9 (proposals/evals/decisions observability
-- spine). Columns mirror those sections exactly; the application-side shapes
-- live in `backend/app/data/models.py`.
--
-- ===========================================================================
-- RLS DOCTRINE — THREAT_MODEL.md §6 (D-RLS-1 … D-RLS-7), CLAUDE.md §1 (INV-5).
-- ===========================================================================
-- We disclosed a CWE-639 / IDOR caused by a `public`-schema table created via
-- raw SQL with RLS *never enabled* — the only access-control boundary behind
-- the anon (publishable) key. We DO NOT reproduce it. This migration encodes
-- the locked doctrine:
--
--   D-RLS-1  Deny-by-default. EVERY table in this exposed (`public`) schema
--            enables row level security at creation time. RLS on + no policy =
--            no rows. This is the cure for the "raw SQL doesn't enable RLS"
--            footgun that caused the finding.
--   D-RLS-2  Owner-scoped SELECT policies use the null guard
--              (SELECT auth.uid()) IS NOT NULL AND (SELECT auth.uid()) = user_id
--            i.e. an explicit `auth.uid() IS NOT NULL` check ANDed with the
--            owner match. The subselect is evaluated once (perf); the explicit
--            null guard closes the `null = user_id`-is-always-NULL trap. Tables
--            owned via `family_id` scope ownership through `family_record.user_id`
--            with the same null guard.
--   D-RLS-3  Rows with a NULL `user_id` (orphaned marketing leads, server-only
--            observability rows) are UNREADABLE under anon/authenticated — they
--            are reachable only by the trusted server using `service_role`
--            (which carries BYPASSRLS). No anon/authenticated policy ever
--            matches a NULL-owner row.
--   D-RLS-4  `service_role` is server-only, never client-side (enforced in env
--            hygiene, TECH_STACK.md §Env — restated here as an RLS invariant).
--   D-RLS-7  NO security-definer helper functions in this exposed schema. All
--            ownership logic is inlined into the policies as plain subqueries.
--
-- The grants below expose tables to the `anon`/`authenticated` PostgREST roles
-- ONLY through these policies; with no INSERT/UPDATE/DELETE policy, writes from
-- those roles are denied by default (deny-by-default, D-RLS-1).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- §4.8 Enumerations (deterministic). Tokens match `models.py` StrEnums exactly.
-- ---------------------------------------------------------------------------

CREATE TYPE stage AS ENUM ('interest', 'apply', 'enroll', 'tuition');

CREATE TYPE stall_reason AS ENUM (
    'app_incomplete',
    'forms_partial',
    'funding_pending',
    'no_response',
    'info_session_no_show'
);

CREATE TYPE funding_type AS ENUM (
    'tefa_standard',
    'tefa_disability',
    'tefa_homeschool',
    'self_pay'
);

CREATE TYPE funding_state AS ENUM (
    'none',
    'applied',
    'awarded_selfreport',
    'gt_confirmed',
    'first_installment_received',
    'funded'
);

CREATE TYPE seam_status AS ENUM ('synced', 'unsynced', 'conflict');

CREATE TYPE product_interest AS ENUM ('campus', 'anywhere', 'summer_camp');

CREATE TYPE decision_action AS ENUM ('approve', 'edit', 'discard');

-- ===========================================================================
-- §4.1 family_record — the join spine. Ownership root: `user_id`.
-- ===========================================================================

CREATE TABLE family_record (
    family_id                       uuid PRIMARY KEY,
    -- Ownership column (D-RLS-2). NULL = server-only marketing-lead row that is
    -- unreadable under anon/authenticated (D-RLS-3).
    user_id                         uuid,
    display_name                    text NOT NULL,
    primary_contact_synthetic_email text NOT NULL,

    lead_id                         uuid,
    app_form_id                     uuid,
    enrollment_form_id              uuid,
    community_profile_id            uuid,

    current_stage                   stage NOT NULL,
    stall_reason                    stall_reason,
    stalled_since                   timestamptz,

    funding_type                    funding_type,
    funding_state                   funding_state NOT NULL DEFAULT 'none',

    attribution_source              text NOT NULL,
    attribution_utm                 jsonb NOT NULL DEFAULT '{}'::jsonb,

    crm_seam_status                 seam_status NOT NULL DEFAULT 'unsynced',
    crm_synced_at                   timestamptz,
    work_queue_score                numeric,

    created_at                      timestamptz DEFAULT now(),
    updated_at                      timestamptz DEFAULT now()
);

-- D-RLS-1: deny-by-default at creation time.
ALTER TABLE family_record ENABLE ROW LEVEL SECURITY;

-- D-RLS-2 / D-RLS-3: owner-scoped read, null-guarded. NULL-owner rows match no
-- one under anon/authenticated and are reachable only via service_role.
CREATE POLICY family_record_owner_select ON family_record
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND (SELECT auth.uid()) = user_id
    );

-- ===========================================================================
-- §4.2 leads_new — top-of-funnel lead. Ownership via family_id → family_record.
-- ===========================================================================

CREATE TABLE leads_new (
    lead_id              uuid PRIMARY KEY,
    family_id            uuid NOT NULL REFERENCES family_record (family_id),
    synthetic_first_name text NOT NULL,
    synthetic_last_name  text NOT NULL,
    synthetic_email      text NOT NULL,
    synthetic_phone      text NOT NULL,
    source               text NOT NULL,
    utm                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    product_interest     product_interest NOT NULL,
    grade_interest       text NOT NULL,
    region               text NOT NULL,
    created_at           timestamptz DEFAULT now()
);

ALTER TABLE leads_new ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership scoped through family_record.user_id, still null-guarded.
-- A NULL-owner parent family (D-RLS-3) yields no matching row.
CREATE POLICY leads_new_owner_select ON leads_new
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- §4.3 app_form — application. Ownership via family_id → family_record.
-- ===========================================================================

CREATE TABLE app_form (
    app_form_id      uuid PRIMARY KEY,
    family_id        uuid NOT NULL REFERENCES family_record (family_id),
    submitted_at     timestamptz,
    completion_pct   numeric,
    map_score        numeric,
    academic_signals jsonb NOT NULL DEFAULT '{}'::jsonb,
    extracted_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz DEFAULT now()
);

ALTER TABLE app_form ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership via family_record.user_id, null-guarded (D-RLS-3).
CREATE POLICY app_form_owner_select ON app_form
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- §4.4 enrollment_forms — six-signed-form gauntlet. Ownership via family_id.
-- ===========================================================================

CREATE TABLE enrollment_forms (
    enrollment_form_id    uuid PRIMARY KEY,
    family_id             uuid NOT NULL REFERENCES family_record (family_id),
    forms_total           int NOT NULL DEFAULT 6,
    forms_signed          int NOT NULL DEFAULT 0,
    forms_status          jsonb NOT NULL DEFAULT '[]'::jsonb,
    tuition_step_unlocked  boolean NOT NULL DEFAULT false,
    created_at            timestamptz DEFAULT now()
);

ALTER TABLE enrollment_forms ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership via family_record.user_id, null-guarded (D-RLS-3).
CREATE POLICY enrollment_forms_owner_select ON enrollment_forms
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- §4.5 community_profiles — community/network context. Ownership via family_id.
-- ===========================================================================

CREATE TABLE community_profiles (
    community_profile_id uuid PRIMARY KEY,
    family_id            uuid NOT NULL REFERENCES family_record (family_id),
    engagement_signals   jsonb NOT NULL DEFAULT '{}'::jsonb,
    referral_network     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz DEFAULT now()
);

ALTER TABLE community_profiles ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership via family_record.user_id, null-guarded (D-RLS-3).
CREATE POLICY community_profiles_owner_select ON community_profiles
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- §4.9 Observability spine — proposals / evals / decisions.
--
-- These hold LLM outputs and audit trail. A proposal is NEVER applied directly
-- to family_record (commitment §1.1). Proposals may have a NULL family_id
-- (content-level, not family-bound) — those rows are server-only (D-RLS-3).
-- evals/decisions inherit ownership from their parent proposal.
-- ===========================================================================

CREATE TABLE proposals (
    proposal_id    uuid PRIMARY KEY,
    family_id      uuid REFERENCES family_record (family_id),
    content_ref    uuid,
    flow           text NOT NULL,
    schema_version text NOT NULL,
    payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz DEFAULT now()
);

ALTER TABLE proposals ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership via the proposal's family_record.user_id, null-guarded.
-- A NULL family_id (or NULL-owner family) yields no match → server-only
-- (D-RLS-3).
CREATE POLICY proposals_owner_select ON proposals
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND family_id IN (
            SELECT fr.family_id
            FROM family_record fr
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE TABLE evals (
    eval_id     uuid PRIMARY KEY,
    proposal_id uuid NOT NULL REFERENCES proposals (proposal_id),
    eval_name   text NOT NULL,
    score       numeric,
    threshold   numeric,
    passed      boolean NOT NULL,
    created_at  timestamptz DEFAULT now()
);

ALTER TABLE evals ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership inherited from the parent proposal's family owner,
-- null-guarded (D-RLS-3).
CREATE POLICY evals_owner_select ON evals
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND proposal_id IN (
            SELECT p.proposal_id
            FROM proposals p
            JOIN family_record fr ON fr.family_id = p.family_id
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

CREATE TABLE decisions (
    decision_id    uuid PRIMARY KEY,
    proposal_id    uuid NOT NULL REFERENCES proposals (proposal_id),
    human          text NOT NULL,
    action         decision_action NOT NULL,
    edited_payload jsonb,
    created_at     timestamptz DEFAULT now()
);

ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;

-- D-RLS-2: ownership inherited from the parent proposal's family owner,
-- null-guarded (D-RLS-3).
CREATE POLICY decisions_owner_select ON decisions
    FOR SELECT
    USING (
        (SELECT auth.uid()) IS NOT NULL
        AND proposal_id IN (
            SELECT p.proposal_id
            FROM proposals p
            JOIN family_record fr ON fr.family_id = p.family_id
            WHERE fr.user_id = (SELECT auth.uid())
        )
    );

-- ===========================================================================
-- PostgREST role grants. Tables are reachable by anon/authenticated ONLY via
-- the SELECT policies above; the absence of write policies makes all
-- INSERT/UPDATE/DELETE from those roles deny-by-default (D-RLS-1). `service_role`
-- (BYPASSRLS) is the only role that may read NULL-owner rows (D-RLS-3, D-RLS-4)
-- and is server-only.
-- ===========================================================================

GRANT SELECT ON
    family_record, leads_new, app_form, enrollment_forms, community_profiles,
    proposals, evals, decisions
TO anon, authenticated;

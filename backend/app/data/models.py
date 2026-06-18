"""Pydantic v2 data models — the Family Record spine + synthetic source tables.

Mirrors the Postgres schema in ARCHITECTURE.md §4.1–§4.5 with the §4.8
enumerations enforced at the type level. These are the application-side shapes;
SQL migrations/DDL and the synthetic generator live elsewhere (separate S0
items). Per CLAUDE.md §3 this module stays free of LLM/adapter imports — it is
pure data, importable by the deterministic core and the edge alike.

Synthetic-only naming (INV-1 / NFR-1, CLAUDE.md §1): every PII-shaped column is
named `*synthetic*` so a real value can never silently land in a real-named
field. All values are synthetic, shaped like GT's real schema (FR-1.2).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# §4.8 Enumerations (deterministic). String-valued so they serialize to the
# exact tokens used by the Postgres enum types and the params/eval layers.
# ---------------------------------------------------------------------------


class Stage(StrEnum):
    """`family_record.current_stage` — funnel stage (§4.1, §4.8; FR-2.1)."""

    INTEREST = "interest"
    APPLY = "apply"
    ENROLL = "enroll"
    TUITION = "tuition"


class StallReason(StrEnum):
    """`family_record.stall_reason` — deterministic stall label (§4.8; FR-2.2)."""

    APP_INCOMPLETE = "app_incomplete"
    FORMS_PARTIAL = "forms_partial"
    FUNDING_PENDING = "funding_pending"
    NO_RESPONSE = "no_response"
    INFO_SESSION_NO_SHOW = "info_session_no_show"


class FundingType(StrEnum):
    """`family_record.funding_type` — funding tier (§4.1, §4.8; FR-1.4)."""

    TEFA_STANDARD = "tefa_standard"
    TEFA_DISABILITY = "tefa_disability"
    TEFA_HOMESCHOOL = "tefa_homeschool"
    SELF_PAY = "self_pay"


class IncomeTier(StrEnum):
    """`family_record.income_tier` — GT's synthetic household-income BUCKET
    (LEAD_ASSIGNMENT.md §6/§13; INV-1/INV-6).

    Mirrors GT's real income segmentation (<$65K / $65K–$160K / >$160K) as a
    THREE-VALUE bucket — deliberately NOT the raw `household_income` figure, so a
    synthetic family carrying it cannot form the C-SYN-2 PII cluster signature
    (a real name + household_income + ZIP on one row) the PII-scan gate forbids
    (THREAT_MODEL.md §5.2). The two lower tiers are the TEFA-eligible (voucher)
    band; the top tier correlates with self-pay/full-pay. Bucket vocabulary
    (INV-11 — not a magic string set), mirrored by the 0017 CHECK constraint.
    """

    LT_65K = "lt_65k"
    MID_65K_160K = "65k_160k"
    GT_160K = "gt_160k"


class FundingState(StrEnum):
    """`family_record.funding_state` — funding-gate progression (§4.1, §5.4)."""

    NONE = "none"
    APPLIED = "applied"
    AWARDED_SELFREPORT = "awarded_selfreport"
    # The voucher selection/reconfirm gap (TODO.md R2): SELECTED_GT = the family
    # indicates they picked GT but has NOT yet reconfirmed/locked in; RECONFIRMED
    # = the parent completed the lock-in. The at-risk "lost on a deadline" gap
    # lives between these two — both GT-controlled signals (INV-10), no Odyssey API.
    SELECTED_GT = "selected_gt"
    RECONFIRMED = "reconfirmed"
    GT_CONFIRMED = "gt_confirmed"
    FIRST_INSTALLMENT_RECEIVED = "first_installment_received"
    FUNDED = "funded"


class SeamStatus(StrEnum):
    """`family_record.crm_seam_status` — Supabase↔HubSpot seam (§4.7, §4.8)."""

    SYNCED = "synced"
    UNSYNCED = "unsynced"
    CONFLICT = "conflict"


class ProductInterest(StrEnum):
    """`leads_new.product_interest` — product line (§4.2)."""

    CAMPUS = "campus"
    ANYWHERE = "anywhere"
    SUMMER_CAMP = "summer_camp"


# ---------------------------------------------------------------------------
# §4.1 family_record — the join spine.
# ---------------------------------------------------------------------------


class FamilyRecord(BaseModel):
    """The one record both workspaces reference (§4.1; FR-1.1/1.3/1.4).

    `current_stage`, `crm_seam_status`, `funding_state`, and `work_queue_score`
    are **derived by the deterministic core** (§5.1/§5.4/§4.7) and are never
    written by an LLM (commitment §1.1). They are present on the model so the
    record round-trips; their derivation lives in the S0 derivers / later
    slices, not here.
    """

    model_config = ConfigDict(use_enum_values=False)

    family_id: UUID
    display_name: str
    primary_contact_synthetic_email: str

    # Ownership root (ASSUMPTIONS.md A-4 / THREAT_MODEL §6 D-RLS-2). Mirrors the
    # nullable `user_id uuid` column added to `family_record` in `0001_init.sql`:
    # NULL ⇒ a server-only marketing-lead row with no owning user (D-RLS-3). The
    # owner-scoped RLS policy keys on `auth.uid() = user_id`, so models and DDL
    # must agree on this column. Optional ⇒ adding it is non-breaking.
    user_id: UUID | None = None

    # Deal ownership — the assigned SALESPERSON (M1; 0013_sales_agents.sql). Mirrors
    # the nullable `assigned_rep_id uuid REFERENCES sales_agent` column. DISTINCT
    # from `user_id` (A-30 / R1): `user_id` = the applicant family's RLS owner
    # (auth.uid()); `assigned_rep_id` = the salesperson who owns the deal in the
    # cockpit. NULL ⇒ unassigned (the intake pool). The demo principal scopes reads
    # by THIS column (server-side owner scoping, INV-5/D-RLS-4); the two identities
    # must never be conflated (the IDOR-class confusion the threat model forbids).
    assigned_rep_id: UUID | None = None

    # When the rep was assigned (M4; mirrors the `assigned_at timestamptz` column
    # added alongside `assigned_rep_id` in 0013_sales_agents.sql). NULL ⇒ never
    # assigned. The owner-authority flip (A-30) makes `owner` DB-authoritative
    # driven by `assigned_rep_id`; this timestamp is the seam's conflict GUARD
    # anchor — a mirror owner that changed AFTER `assigned_at` is a post-assignment
    # HubSpot edit the seam flags rather than stomps (INV-4-style; app/core/seam.py).
    assigned_at: datetime | None = None

    # --- Lead-assignment routing attributes (LEAD_ASSIGNMENT.md §10). SYNTHETIC,
    # household-scoped, NULLABLE so rows predating the field stay valid. ---
    # A coarse, synthetic US-state code (e.g. "FL"/"CA") the territory rule routes
    # on (§4). An aggregate region label, NEVER a ZIP/lat-long/precise geo of a
    # minor (INV-6) — mirrors the nullable `state text` column (0017). NULL ⇒ no
    # state on file (routes via the territory fallback pool).
    state: str | None = None
    # GT's synthetic household-income BUCKET (§6/§13) — a 3-value enum, NEVER raw
    # income. One input to territory/eligibility prioritization (TEFA-eligible vs
    # full-pay). Mirrors the CHECK-bounded `income_tier text` column (0017).
    income_tier: IncomeTier | None = None
    # The SELF-REPORTED prior agent the applicant names on the apply form (§3).
    # The applicant writes this on their OWN row (anon+RLS); the SERVER promotes a
    # resolved value to `assigned_rep_id` (service_role). DISTINCT from
    # `assigned_rep_id`: this is an unverified self-report INPUT, never an
    # authoritative ownership write by the client (INV-5). Mirrors the nullable
    # `reported_rep_id uuid REFERENCES sales_agent` column (0017).
    reported_rep_id: UUID | None = None

    # Join keys, nullable until the related record exists (§4.1).
    lead_id: UUID | None = None
    app_form_id: UUID | None = None
    enrollment_form_id: UUID | None = None
    community_profile_id: UUID | None = None

    # Derived by the deterministic core (§5.1); seeded explicitly here.
    current_stage: Stage
    stall_reason: StallReason | None = None
    stalled_since: datetime | None = None

    funding_type: FundingType | None = None
    funding_state: FundingState = FundingState.NONE  # DERIVED (§5.4).

    # Attribution — required (FR-1.4): how they heard + raw utm/click IDs.
    attribution_source: str
    attribution_utm: dict[str, object]

    crm_seam_status: SeamStatus = SeamStatus.UNSYNCED  # DERIVED (§4.7).
    crm_synced_at: datetime | None = None
    work_queue_score: float | None = None  # DERIVED by §5.1 scorer.

    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# lead_assignment — append-only ownership history (LEAD_ASSIGNMENT.md §10).
# ---------------------------------------------------------------------------


class LeadAssignment(BaseModel):
    """One append-only ownership-history row (mirrors the 0017 `lead_assignment`).

    A reassignment is a FACT IN TIME (who→who, when, why), never an overwrite: the
    mutable ``FamilyRecord.assigned_rep_id`` answers "who owns it now?"; this
    timeline answers "what is the ownership history?" ``from_rep_id`` NULL ⇒ first
    assignment out of the intake pool; ``to_rep_id`` NULL ⇒ unassigned back to the
    pool. ``reason`` is the §2 human-readable rule trace (always present — every
    assignment is explainable). Written server-side only (service_role); immutable.
    """

    assignment_id: UUID
    family_id: UUID
    from_rep_id: UUID | None = None
    to_rep_id: UUID | None = None
    routed_role: str | None = None  # 'closer' | 'qualifier' (a structural label)
    assigned_by: str  # the operator/admin, or 'router'
    reason: str
    batch_id: str | None = None
    occurred_at: datetime | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# §4.1b student — one child's per-child funnel (A-24).
# ---------------------------------------------------------------------------


class Student(BaseModel):
    """One CHILD's own full funnel within a household (A-24; user-directed).

    The user's flow starts **a new application per child**, so each child runs
    its own funnel (application → enrollment → tuition → funding) rather than the
    family sharing one. A :class:`Student` therefore carries the per-child funnel
    state that previously lived (collapsed) on :class:`FamilyRecord`:
    ``current_stage``, ``stall_reason``/``stalled_since``, ``funding_type``/
    ``funding_state``, its own ``app_form_id``/``enrollment_form_id``, seam
    status, and the derived ``work_queue_score``. ``FamilyRecord`` remains the
    **household** that groups students (attribution, contact, region, seam root).

    ``display_label`` is a distinct, human label per student (e.g.
    ``"Rivera household — Alex · Grade 3"``); it also disambiguates the many
    same-surname households the generator produces. Child identity fields are
    synthetic-named (NFR-1 / INV-1).

    Derived fields (``funding_state``, ``crm_seam_status``, ``work_queue_score``)
    are owned by the deterministic core (§5.1/§5.4/§4.7), never an LLM (§1.1);
    they round-trip here, their derivation lives in the scorer / funding gate.
    """

    model_config = ConfigDict(use_enum_values=False)

    student_id: UUID
    family_id: UUID  # the household this child belongs to (FamilyRecord.family_id).

    display_label: str
    synthetic_first_name: str
    grade: str

    # Per-child funnel — each student its own position/stall (A-24).
    current_stage: Stage
    stall_reason: StallReason | None = None
    stalled_since: datetime | None = None

    funding_type: FundingType | None = None
    funding_state: FundingState = FundingState.NONE  # DERIVED (§5.4).

    # One application + one enrollment packet PER CHILD (A-24).
    app_form_id: UUID | None = None
    enrollment_form_id: UUID | None = None

    crm_seam_status: SeamStatus = SeamStatus.UNSYNCED  # DERIVED (§4.7).
    crm_synced_at: datetime | None = None
    work_queue_score: float | None = None  # DERIVED by §5.1 scorer.

    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# §4.2 leads_new — top-of-funnel lead (synthetic, shaped like GT's real table).
# ---------------------------------------------------------------------------


class LeadsNew(BaseModel):
    """Top-of-funnel lead (§4.2; FR-1.2).

    All PII-shaped fields carry the `synthetic_` prefix (INV-1 / NFR-1).
    `region` is an aggregate label only — no precise geo of minors (P-4).
    """

    lead_id: UUID
    family_id: UUID
    synthetic_first_name: str
    synthetic_last_name: str
    synthetic_email: str
    synthetic_phone: str
    source: str
    utm: dict[str, object] = Field(default_factory=dict)
    product_interest: ProductInterest
    grade_interest: str
    region: str
    # An aggregate neighborhood / area LABEL (a city district name, same spirit as
    # `region`) — NEVER precise geo of a minor (P-4 / INV-6); it is the coarse
    # geo→affluence KEY the conversion-likelihood signal maps via a params table.
    # Defaults to a safe placeholder so existing fixtures/rows that predate the
    # field stay valid. Synthetic only (INV-1).
    neighborhood: str = "Unspecified"
    # The Interest form's "How many children are you applying for? 1–5+" (funnel
    # map §4D). Drives the work-queue VALUE term (A-23): value scales with child
    # count since every targeted family pays the same per-child tuition. Defaults
    # to 1 so existing fixtures/rows that predate the field stay valid (a lead is
    # always for ≥1 child); a `ge=1` guard rejects a non-positive count.
    num_children: int = Field(default=1, ge=1)
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# §4.3 app_form — application.
# ---------------------------------------------------------------------------


class AppForm(BaseModel):
    """Application form (§4.3; FR-1.2).

    `extracted_fields` is the doc-extraction eval target (FR-4.2):
    proposal-sourced and human-confirmed — never written directly by an LLM.
    """

    app_form_id: UUID
    family_id: UUID
    # A-24: one application PER CHILD. `student_id` keys the app to its student;
    # optional so pre-A-24 family-only fixtures/rows stay valid (non-breaking).
    student_id: UUID | None = None
    submitted_at: datetime | None = None  # null = started, not submitted (a stall).
    # The household income the family SELF-REPORTS on the application, whole USD.
    # `None` = not yet provided (a valid mid-funnel state). One input to the
    # conversion-likelihood signal (alongside neighborhood affluence, child count,
    # funding type, application depth). Synthetic only (INV-1).
    self_reported_income: int | None = None
    completion_pct: float | None = None  # 0–100, deterministic.
    map_score: float | None = None  # academic signal surfaced in deal view (FR-2.2).
    academic_signals: dict[str, object] = Field(default_factory=dict)
    extracted_fields: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# §4.4 enrollment_forms — the six-signed-form gauntlet.
# ---------------------------------------------------------------------------


class EnrollmentForms(BaseModel):
    """The six-signed-form gauntlet (§4.4; PROJECT.md §2).

    `tuition_step_unlocked` is **derived by the funding gate** (§5.4) and is
    never hand-set; it is present here for round-tripping only.
    """

    enrollment_form_id: UUID
    family_id: UUID
    # A-24: one enrollment packet PER CHILD. Optional so pre-A-24 fixtures stay valid.
    student_id: UUID | None = None
    forms_total: int = 6  # the six-form gauntlet (§4.4 default).
    forms_signed: int = 0  # 0–6; forms_signed < forms_total ⇒ enroll-stage stall.
    forms_status: list[dict[str, object]] = Field(default_factory=list)
    tuition_step_unlocked: bool = False  # DERIVED by funding gate (§5.4).
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# §4.5 community_profiles — community/network context.
# ---------------------------------------------------------------------------


class CommunityProfile(BaseModel):
    """Community / network context (§4.5; FR-1.2).

    `engagement_signals` is aggregate only — no behavioral targeting of minors
    (P-4 / INV-6).
    """

    community_profile_id: UUID
    family_id: UUID
    engagement_signals: dict[str, object] = Field(default_factory=dict)
    referral_network: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None

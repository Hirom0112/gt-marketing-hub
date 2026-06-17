"""Synthetic data generator — the **only seed writer** (ARCHITECTURE.md §1.3).

`generate(n, seed)` returns an in-memory :class:`SyntheticDataset`: ``n``
`FamilyRecord` spine rows (§4.1), each joined by ``family_id`` to exactly one row
in each of the four source tables (`leads_new`, `app_form`, `enrollment_forms`,
`community_profiles`; §4.2–§4.5). The runtime store is seeded from this dataset —
per ASSUMPTIONS.md A-3 there is no live Supabase locally.

Determinism (CLAUDE.md §4.1): generation draws *only* from a single
``random.Random(seed)`` instance, so the same seed always yields byte-identical
output. UUIDs are derived deterministically from that RNG (not :func:`uuid.uuid4`,
which would reseed from the OS).

Synthetic-only mandate (INV-1 / NFR-1 / THREAT_MODEL.md §5.2, C-SYN-2): the
generator can never emit the real-PII cluster signature (a real-looking personal
name + ``household_income`` + ZIP/geo on one row). Concretely:

- display names are obviously-synthetic household labels — ``"The Rivera Family"``,
  never a bare ``First Last`` personal name;
- every email ends ``@example.invalid`` (the recognised synthetic marker) and every
  phone sits in the NANP fictitious ``555-01xx`` block;
- there is **no** ``household_income`` field anywhere, and geography is an aggregate
  ``region`` label only — never a ZIP or precise lat/long of minors (§4.2, P-4).

Per CLAUDE.md §3 this module imports **no** LLM/adapter modules — it depends only
on the stdlib and the pure data models (`app.data.models`). No pandas/faker: the
stdlib ``random`` is enough and keeps the runtime dep budget (≤15) untouched.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.core.params import Params, Realistic

from app.ai.schemas.brand import (
    BrandMemoryItem,
    BrandMemoryKind,
    BrandMemorySignal,
    BrandRule,
    EnforcedBy,
    LibraryAsset,
    LibraryAssetType,
    MarketingRecipe,
    RecipeParam,
    RecipeParamType,
    RuleType,
    Severity,
)
from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentCandidate,
    ContentFormat,
    GeneratedBy,
    HumanDecision,
    LifecycleStage,
    Provenance,
)
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    FundingType,
    LeadsNew,
    ProductInterest,
    SeamStatus,
    Stage,
    StallReason,
    Student,
)
from app.marketing.geo import GIFTED_SCHOOL_COMPETITOR_SET
from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    ImageArtifact,
    VideoArtifact,
)
from app.marketing.schemas.artifacts import (
    Stage as ArtifactStage,
)
from app.marketing.schemas.discovery import (
    AudienceSegment,
    CreatorDataMode,
    CreatorRecord,
    Sentiment,
    SentimentRecord,
    SentimentSourceMode,
)
from app.marketing.schemas.geo import GeoContentPiece, GeoStructure

# --------------------------------------------------------------------------- #
# Synthetic vocabularies. Surnames are used only to build household *labels*
# ("The Rivera Family"); they are never combined with income + geo, so the
# C-SYN-2 cluster signature can never form (THREAT_MODEL.md §5.2).
# --------------------------------------------------------------------------- #

_SURNAMES: tuple[str, ...] = (
    "Rivera",
    "Okafor",
    "Nguyen",
    "Patel",
    "Johnson",
    "Garcia",
    "Kim",
    "Müller",
    "Silva",
    "Ahmed",
    "Lopez",
    "Chen",
    "Brown",
    "Hassan",
    "Novak",
    "Rossi",
    "Tanaka",
    "Dubois",
    "Schmidt",
    "Kowalski",
    "Andersson",
    "Costa",
    "Mbeki",
    "Reyes",
    "Haddad",
    "Ivanov",
    "Park",
    "Wright",
    "Flores",
    "Becker",
)

_GIVEN_NAMES: tuple[str, ...] = (
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Riley",
    "Morgan",
    "Casey",
    "Jamie",
    "Avery",
    "Quinn",
    "Drew",
    "Reese",
    "Skyler",
    "Rowan",
    "Emerson",
    "Devon",
)

# Aggregate region labels only — no precise geo of minors (§4.2, P-4).
_REGIONS: tuple[str, ...] = (
    "Northeast",
    "Southeast",
    "Midwest",
    "Southwest",
    "Mountain West",
    "Pacific Northwest",
    "West Coast",
    "Mid-Atlantic",
    "Great Plains",
)

_ATTRIBUTION_SOURCES: tuple[str, ...] = (
    "organic_search",
    "branded_search",
    "referral",
    "paid_social",
    "newsletter",
    "webinar",
    "partner",
    "direct",
)

_UTM_CAMPAIGNS: tuple[str, ...] = (
    "spring_open_house",
    "summer_camp_2026",
    "anywhere_launch",
    "k8_enrollment",
    "scholarship_awareness",
    "alumni_referral",
)

_GRADES: tuple[str, ...] = ("K", "1", "2", "3", "4", "5", "6", "7", "8")

_FORM_NAMES: tuple[str, ...] = (
    "enrollment_agreement",
    "media_release",
    "health_form",
    "tech_acceptable_use",
    "tuition_agreement",
    "emergency_contacts",
)

# Realistic-but-synthetic funnel shape so later dashboards have signal (FR-2.x):
# most families sit early in the funnel, a tail reaches tuition.
_STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.INTEREST: 0.42,
    Stage.APPLY: 0.28,
    Stage.ENROLL: 0.20,
    Stage.TUITION: 0.10,
}

_FUNDING_TYPE_WEIGHTS: dict[FundingType, float] = {
    FundingType.TEFA_STANDARD: 0.55,
    FundingType.SELF_PAY: 0.25,
    FundingType.TEFA_DISABILITY: 0.12,
    FundingType.TEFA_HOMESCHOOL: 0.08,
}

# A-23 — the recovery board only targets families paying the FULL GT-Anywhere
# tuition: Texas voucher (TEFA standard) or self-pay. The active/targeted cohorts
# (back-to-school + realistic active stalls) draw funding from this restricted set;
# the disability ($30k) / homeschool ($2k) tiers are NOT a recovery target.
_TARGETED_FUNDING_WEIGHTS: dict[FundingType, float] = {
    FundingType.TEFA_STANDARD: 0.6,
    FundingType.SELF_PAY: 0.4,
}

# A-23 — the work-queue VALUE term scales with child count (the Interest form's
# "How many children are you applying for? 1–5+"). Synthetic shape only (module
# data, like the stage/funding weights): most families enroll one child, a tail
# enrolls more, capped at the form's "5+".
_CHILD_COUNT_WEIGHTS: dict[int, float] = {1: 0.55, 2: 0.28, 3: 0.11, 4: 0.04, 5: 0.02}

_PRODUCT_WEIGHTS: dict[ProductInterest, float] = {
    ProductInterest.CAMPUS: 0.50,
    ProductInterest.ANYWHERE: 0.35,
    ProductInterest.SUMMER_CAMP: 0.15,
}

# Stall reasons that fit each stall-prone stage (deterministic seeding only; the
# real stall *derivation* lives in the core, §5.1 — here we just supply signal).
_STALL_BY_STAGE: dict[Stage, tuple[StallReason, ...]] = {
    Stage.INTEREST: (StallReason.NO_RESPONSE, StallReason.INFO_SESSION_NO_SHOW),
    Stage.APPLY: (StallReason.APP_INCOMPLETE, StallReason.NO_RESPONSE),
    Stage.ENROLL: (StallReason.FORMS_PARTIAL, StallReason.FUNDING_PENDING),
    Stage.TUITION: (StallReason.FUNDING_PENDING,),
}

# The demo "now" anchor for synthetic timestamps (UTC). FIXED and deterministic
# (never datetime.now — determinism/repro is required, CLAUDE.md §4.1): every
# family's created_at/apply_date is a deterministic offset *before* this instant,
# so the funnel reads as a current one. Set to the demo date (2026-06-15) so the
# recent-month spread lands across the current month (June 2026) and the months
# around it, and the calendar opens on populated months instead of empty ones.
_EPOCH = datetime(2026, 6, 15, tzinfo=UTC)

# Recency buckets (S9 contact-color realism). A family's age-from-now bucket is
# drawn deterministically so the situation bar reads as a believable mix rather
# than ~100% overdue. FRESH families are created within the grey window
# (`enrollment.contact.grey_window_days` = 3) of the demo now ⇒ they color grey;
# OVERDUE families are older (created weeks-to-months back) ⇒ they color red.
# CLOSED is independent of age (it follows the funding gate), and FOLLOWED_UP is
# composed at the api layer from the audit log (A-14), not seeded here.
#
# `fresh` weight is set so that — net of the families the funding gate pulls into
# CLOSED — the non-closed remainder splits into a believable fresh/overdue mix.
_FRESH_WEIGHT = 0.32

# Day ranges for each recency bucket (inclusive, measured back from the epoch):
# fresh sits strictly inside the grey window (age 0..2 < grey_window_days=3 ⇒
# still FRESH at the committed overdue_days=4); overdue spans a few days to ~5
# months back so the recent-month calendar (May/June 2026) populates and the
# older tail reads as genuinely-overdue.
_FRESH_MAX_DAYS_AGO = 2
_OVERDUE_MIN_DAYS_AGO = 7
_OVERDUE_MAX_DAYS_AGO = 150


@dataclass(frozen=True)
class SyntheticDataset:
    """The in-memory seed dataset: the spine plus its four joined source tables.

    Every list is parallel-keyed by ``family_id`` — there is exactly one row per
    family in each source table, and the matching FK is set on the spine row.
    """

    families: list[FamilyRecord] = field(default_factory=list)
    leads: list[LeadsNew] = field(default_factory=list)
    app_forms: list[AppForm] = field(default_factory=list)
    enrollment_forms: list[EnrollmentForms] = field(default_factory=list)
    community_profiles: list[CommunityProfile] = field(default_factory=list)

    # A-24 — per-child STUDENT rows: one Student (own funnel) per child, plus its
    # own application + enrollment packet (one application per child). These live
    # in SEPARATE lists so the four source tables above stay one-row-per-family
    # (the determinism + count guards hold); the student pass draws from an
    # ISOLATED RNG so the family stream stays byte-identical.
    students: list[Student] = field(default_factory=list)
    student_app_forms: list[AppForm] = field(default_factory=list)
    student_enrollment_forms: list[EnrollmentForms] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic field fakers. Each draws only from the supplied RNG so the whole
# dataset is reproducible from a single seed.
# --------------------------------------------------------------------------- #


def _uuid(rng: random.Random) -> UUID:
    """A version-4-shaped UUID drawn deterministically from ``rng`` (not the OS)."""
    return UUID(int=rng.getrandbits(128), version=4)


def _weighted_choice[K](rng: random.Random, weights: dict[K, float]) -> K:
    """Pick a key from a ``{value: weight}`` mapping using ``rng``."""
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _num_children(rng: random.Random) -> int:
    """A synthetic child count (1–5, heavy on 1–2) for the value term (A-23)."""
    return _weighted_choice(rng, _CHILD_COUNT_WEIGHTS)


def _synthetic_email(rng: random.Random, surname: str) -> str:
    """A synthetic email in the @example.invalid sink — never a real address."""
    return f"{surname.lower()}.{rng.randint(100, 999)}@example.invalid"


def _synthetic_phone(rng: random.Random) -> str:
    """A phone in the NANP fictitious 555-01xx block — clearly not dialable."""
    return f"555-01{rng.randint(0, 99):02d}"


def _timestamp(rng: random.Random, max_days_ago: int) -> datetime:
    """A deterministic UTC timestamp within ``max_days_ago`` days of the epoch."""
    return _EPOCH - timedelta(days=rng.randint(0, max_days_ago), minutes=rng.randint(0, 1439))


def _timestamp_between(rng: random.Random, *, min_days_ago: int, max_days_ago: int) -> datetime:
    """A deterministic UTC timestamp ``[min_days_ago, max_days_ago]`` days before the epoch."""
    return _EPOCH - timedelta(
        days=rng.randint(min_days_ago, max_days_ago), minutes=rng.randint(0, 1439)
    )


def _created_at(rng: random.Random) -> datetime:
    """A family's ``created_at`` drawn from a recency bucket (contact-color realism).

    A ``_FRESH_WEIGHT`` share of families are created strictly inside the grey
    window (age ``0..2`` days) so they color FRESH; the rest are created weeks to
    months back (``7..150`` days) so they color OVERDUE when uncontacted — the
    recent end of that range keeps the May/June 2026 calendar populated. CLOSED
    and FOLLOWED_UP are derived downstream (funding gate / audit log), so this
    only governs the fresh-vs-overdue split. Deterministic: every draw is from
    ``rng``.
    """
    if rng.random() < _FRESH_WEIGHT:
        return _timestamp(rng, max_days_ago=_FRESH_MAX_DAYS_AGO)
    return _timestamp_between(
        rng, min_days_ago=_OVERDUE_MIN_DAYS_AGO, max_days_ago=_OVERDUE_MAX_DAYS_AGO
    )


def _synthetic_utm(rng: random.Random, attribution_source: str) -> dict[str, object]:
    """A synthetic utm/click-id blob (FR-1.4) — opaque IDs, no PII."""
    return {
        "utm_source": attribution_source,
        "utm_medium": rng.choice(("cpc", "email", "social", "organic")),
        "utm_campaign": rng.choice(_UTM_CAMPAIGNS),
        "click_id": f"clk_{rng.getrandbits(32):08x}",
    }


def _seam_status(rng: random.Random) -> SeamStatus:
    """A simulated CRM seam status (§4.7) — mostly synced, a conflict tail."""
    return rng.choices(list(SeamStatus), weights=[0.6, 0.3, 0.1], k=1)[0]


def _build_family(
    rng: random.Random,
) -> tuple[FamilyRecord, LeadsNew, AppForm, EnrollmentForms, CommunityProfile]:
    """Build one family + its four joined source rows, all keyed by family_id."""
    family_id = _uuid(rng)
    lead_id = _uuid(rng)
    app_form_id = _uuid(rng)
    enrollment_form_id = _uuid(rng)
    community_profile_id = _uuid(rng)

    surname = rng.choice(_SURNAMES)
    given = rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    stage = _weighted_choice(rng, _STAGE_WEIGHTS)
    funding_type = _weighted_choice(rng, _FUNDING_TYPE_WEIGHTS)
    product = _weighted_choice(rng, _PRODUCT_WEIGHTS)
    attribution_source = rng.choice(_ATTRIBUTION_SOURCES)
    email = _synthetic_email(rng, surname)
    created = _created_at(rng)

    utm = _synthetic_utm(rng, attribution_source)

    # ~38% of families are stalled at their current stage — signal for the queue.
    is_stalled = rng.random() < 0.38
    stall_reason = rng.choice(_STALL_BY_STAGE[stage]) if is_stalled else None
    stalled_since = _timestamp(rng, max_days_ago=45) if is_stalled else None

    # Funding-state distribution skews by stage (later stages are further along).
    funding_state = _funding_state_for_stage(rng, stage)

    family = FamilyRecord(
        family_id=family_id,
        display_name=f"The {surname} Family",
        primary_contact_synthetic_email=email,
        lead_id=lead_id,
        app_form_id=app_form_id,
        enrollment_form_id=enrollment_form_id,
        community_profile_id=community_profile_id,
        current_stage=stage,
        stall_reason=stall_reason,
        stalled_since=stalled_since,
        funding_type=funding_type,
        funding_state=funding_state,
        attribution_source=attribution_source,
        attribution_utm=utm,
        crm_seam_status=_seam_status(rng),
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        # Bounded by the demo now so a fresh family is never "updated in the future".
        updated_at=min(created + timedelta(days=rng.randint(0, 30)), _EPOCH),
    )

    lead = LeadsNew(
        lead_id=lead_id,
        family_id=family_id,
        synthetic_first_name=given,
        synthetic_last_name=surname,
        synthetic_email=email,
        synthetic_phone=_synthetic_phone(rng),
        source=attribution_source,
        utm=utm,
        product_interest=product,
        grade_interest=rng.choice(_GRADES),
        region=region,
        num_children=_num_children(rng),
        created_at=created,
    )

    app_form = _build_app_form(rng, app_form_id, family_id, stage, created)
    enrollment = _build_enrollment(rng, enrollment_form_id, family_id, stage, created)
    profile = _build_profile(rng, community_profile_id, family_id, created)

    return family, lead, app_form, enrollment, profile


def _funding_state_for_stage(rng: random.Random, stage: Stage) -> FundingState:
    """Pick a plausible funding_state given the funnel stage (later ⇒ further along)."""
    if stage is Stage.INTEREST:
        return rng.choice((FundingState.NONE, FundingState.APPLIED))
    if stage is Stage.APPLY:
        return rng.choice((FundingState.APPLIED, FundingState.AWARDED_SELFREPORT))
    if stage is Stage.ENROLL:
        return rng.choice((FundingState.AWARDED_SELFREPORT, FundingState.GT_CONFIRMED))
    return rng.choice((FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED))


def _build_app_form(
    rng: random.Random,
    app_form_id: UUID,
    family_id: UUID,
    stage: Stage,
    created: datetime,
) -> AppForm:
    """An app_form whose completion reflects the funnel stage (§4.3)."""
    applied = stage in (Stage.APPLY, Stage.ENROLL, Stage.TUITION)
    if stage is Stage.INTEREST:
        completion = round(rng.uniform(0.0, 40.0), 1)
        submitted_at: datetime | None = None
    elif stage is Stage.APPLY:
        completion = round(rng.uniform(40.0, 95.0), 1)
        submitted_at = None if completion < 100.0 else created
    else:
        completion = 100.0
        submitted_at = created + timedelta(days=rng.randint(1, 14))

    return AppForm(
        app_form_id=app_form_id,
        family_id=family_id,
        submitted_at=submitted_at,
        completion_pct=completion,
        map_score=round(rng.uniform(150.0, 260.0), 1) if applied else None,
        academic_signals={
            "reading_percentile": rng.randint(1, 99),
            "math_percentile": rng.randint(1, 99),
        },
        extracted_fields={},
        created_at=created,
    )


def _build_enrollment(
    rng: random.Random,
    enrollment_form_id: UUID,
    family_id: UUID,
    stage: Stage,
    created: datetime,
) -> EnrollmentForms:
    """The six-form gauntlet, signed-count reflecting the funnel stage (§4.4)."""
    if stage in (Stage.INTEREST, Stage.APPLY):
        forms_signed = 0
    elif stage is Stage.ENROLL:
        forms_signed = rng.randint(1, 5)  # partial ⇒ enroll-stage stall signal
    else:
        forms_signed = 6

    forms_status: list[dict[str, object]] = []
    for i, name in enumerate(_FORM_NAMES):
        signed = i < forms_signed
        forms_status.append(
            {
                "name": name,
                "signed_at": (created + timedelta(days=i + 1)).isoformat() if signed else None,
            }
        )

    return EnrollmentForms(
        enrollment_form_id=enrollment_form_id,
        family_id=family_id,
        forms_total=6,
        forms_signed=forms_signed,
        forms_status=forms_status,
        tuition_step_unlocked=stage is Stage.TUITION,
        created_at=created,
    )


def _build_profile(
    rng: random.Random,
    community_profile_id: UUID,
    family_id: UUID,
    created: datetime,
) -> CommunityProfile:
    """Aggregate engagement + a synthetic referral graph (§4.5; aggregate only, P-4)."""
    return CommunityProfile(
        community_profile_id=community_profile_id,
        family_id=family_id,
        engagement_signals={
            "events_attended": rng.randint(0, 6),
            "email_opens": rng.randint(0, 40),
            "newsletter_subscribed": rng.random() < 0.7,
        },
        referral_network={
            "referred_by_count": rng.randint(0, 3),
            "referrals_made": rng.randint(0, 4),
        },
        created_at=created,
    )


# A-24 — salt that isolates the per-child Student pass from the family RNG stream,
# so adding students leaves the family/lead/app/enrollment/profile output
# byte-identical (same isolation discipline as the A-21 back-to-school cohort).
_STUDENT_SEED_SALT = 0x5354_5544  # "STUD"

# A-24 (reshape) — a child's funnel tracks its HOUSEHOLD's recovery situation
# rather than being drawn independently. Drawing each child's stall at a flat
# ~38% (regardless of the family) swamped the realistic cohort's ~140-active
# shaping: it produced ~7,400 "active" children against ~118 active families, so
# the per-child board read as 50x busier than the family board. Correlating the
# child to its household restores believable proportions while keeping each
# child's own funnel (A-24): a SETTLED household (no active stall — its
# ``stalled_since`` is None) enrolled together, so its children all read
# RECOVERED; an ACTIVE household's children are mostly still stalled, bar the
# occasional sibling who already moved ahead.
_SIBLING_AHEAD_PROB = 0.15  # an active household's child who has already recovered


def _child_funnel(
    rng: random.Random, *, recovered: bool
) -> tuple[Stage, StallReason | None, datetime | None, FundingState]:
    """Per-child (stage, stall_reason, stalled_since, funding_state) for a disposition.

    ``recovered`` ⇒ a settled child: tuition stage, funded, no stall — the recovery
    deriver reads RECOVERED via the §5.4 funding gate. Otherwise an ACTIVE stall: a
    pre-tuition stage with a stall_reason whose stall-stage EQUALS the stage (via
    :data:`_BTS_STALL_BY_STAGE`, so it never reads "advanced"), funding below the
    first-installment floor, and a recent ``stalled_since`` — so the deriver reads
    STALLED. All draws come from the supplied isolated ``rng`` (determinism holds).
    """
    if recovered:
        return (Stage.TUITION, None, None, FundingState.FUNDED)
    stage = rng.choice((Stage.INTEREST, Stage.APPLY, Stage.ENROLL))
    stall_reason = rng.choice(_BTS_STALL_BY_STAGE[stage])
    stalled_since = _timestamp(rng, max_days_ago=45)
    funding_state = rng.choice(
        (FundingState.NONE, FundingState.APPLIED, FundingState.AWARDED_SELFREPORT)
    )
    return (stage, stall_reason, stalled_since, funding_state)


def _build_students_for_family(
    rng: random.Random,
    *,
    family: FamilyRecord,
    lead: LeadsNew,
) -> tuple[list[Student], list[AppForm], list[EnrollmentForms]]:
    """Build one Student per child for a household (A-24) — each its own funnel.

    The user's flow starts **a new application per child**, so each of the lead's
    ``num_children`` children gets its own Student with an independently-drawn
    ``current_stage``/stall/``funding_state`` and its own application + enrollment
    packet (keyed by ``student_id``). The household funding TIER is shared
    (``family.funding_type`` — voucher vs self-pay is a household attribute); the
    per-child funding STATE progresses per application. Child given names are
    sampled WITHOUT replacement so each label
    (``"{surname} household — {child} · Grade {g}"``) is distinct within the
    household — which also disambiguates the many same-surname households on the
    board. All draws come from the supplied (isolated) ``rng``.
    """
    students: list[Student] = []
    app_forms: list[AppForm] = []
    enrollment_forms: list[EnrollmentForms] = []

    created = family.created_at or _EPOCH
    surname = lead.synthetic_last_name
    child_names = rng.sample(_GIVEN_NAMES, k=lead.num_children)
    # The household's disposition: a family with no active stall (stalled_since is
    # None) has settled (enrolled / moved on), so its children read recovered; an
    # active stall's children are mostly still in the funnel (A-24 reshape).
    household_settled = family.stalled_since is None

    for child in child_names:
        student_id = _uuid(rng)
        app_form_id = _uuid(rng)
        enrollment_form_id = _uuid(rng)

        # Settled households recover every child together; an active household's
        # child is stalled unless it is the occasional already-ahead sibling.
        recovered = household_settled or rng.random() < _SIBLING_AHEAD_PROB
        stage, stall_reason, stalled_since, funding_state = _child_funnel(rng, recovered=recovered)
        grade = rng.choice(_GRADES)

        students.append(
            Student(
                student_id=student_id,
                family_id=family.family_id,
                display_label=f"{surname} household — {child} · Grade {grade}",
                synthetic_first_name=child,
                grade=grade,
                current_stage=stage,
                stall_reason=stall_reason,
                stalled_since=stalled_since,
                funding_type=family.funding_type,
                funding_state=funding_state,
                app_form_id=app_form_id,
                enrollment_form_id=enrollment_form_id,
                crm_seam_status=_seam_status(rng),
                work_queue_score=round(rng.uniform(0.0, 1.0), 4),
                created_at=created,
            )
        )
        app_forms.append(
            _build_app_form(rng, app_form_id, family.family_id, stage, created).model_copy(
                update={"student_id": student_id}
            )
        )
        enrollment_forms.append(
            _build_enrollment(rng, enrollment_form_id, family.family_id, stage, created).model_copy(
                update={"student_id": student_id}
            )
        )

    return students, app_forms, enrollment_forms


def _populate_students(ds: SyntheticDataset, *, seed: int) -> None:
    """Append one Student per child for every family already in ``ds`` (A-24).

    A second, ISOLATED pass shared by every cohort builder (default / realistic /
    back-to-school): draws from ``random.Random(seed ^ _STUDENT_SEED_SALT)`` so it
    leaves the family/lead/app/enrollment/profile stream the builder produced
    byte-identical (the determinism + one-row-per-family guards hold), and writes
    the per-child rows into the dataset's separate ``students`` /
    ``student_app_forms`` / ``student_enrollment_forms`` lists. Mutates ``ds``.
    """
    student_rng = random.Random(seed ^ _STUDENT_SEED_SALT)
    leads_by_family = {lead.family_id: lead for lead in ds.leads}
    for family in ds.families:
        lead = leads_by_family.get(family.family_id)
        if lead is None:
            continue
        students, app_forms, enrollment_forms = _build_students_for_family(
            student_rng, family=family, lead=lead
        )
        ds.students.extend(students)
        ds.student_app_forms.extend(app_forms)
        ds.student_enrollment_forms.extend(enrollment_forms)


def generate(n: int, seed: int = 0) -> SyntheticDataset:
    """Generate ``n`` synthetic families joined to their four source rows.

    Deterministic: the same ``seed`` always produces byte-identical output, because
    every draw comes from one ``random.Random(seed)``. Realistic-but-synthetic
    distributions across stages and funding types give later dashboards signal
    (FR-2.x); scales to ``n=5000`` (NFR-9) with no manual pagination.

    Args:
        n: number of families (and rows per source table) to produce.
        seed: RNG seed; fixed seed ⇒ reproducible dataset (CLAUDE.md §4.1).

    Returns:
        An in-memory :class:`SyntheticDataset` — the only seed the runtime store
        is hydrated from (ARCHITECTURE.md §1.3; ASSUMPTIONS.md A-3).
    """
    if n < 0:
        raise ValueError("n must be non-negative")

    rng = random.Random(seed)
    ds = SyntheticDataset()
    for _ in range(n):
        family, lead, app_form, enrollment, profile = _build_family(rng)
        ds.families.append(family)
        ds.leads.append(lead)
        ds.app_forms.append(app_form)
        ds.enrollment_forms.append(enrollment)
        ds.community_profiles.append(profile)

    _populate_students(ds, seed=seed)
    return ds


# --------------------------------------------------------------------------- #
# S12 W2 — the back-to-school VOLUME cohort (A-21; INV-1/INV-11).
#
# A SEPARATE deterministic cohort, NOT a mutation of the default June world: it
# draws from its OWN ``random.Random(seed)`` instance, so the default ``generate``
# stream stays byte-identical (the `test_synthetic.py` determinism guard holds —
# "new draws appended" via an isolated RNG). It reproduces the mock v2 shape: a
# surge of ``count`` ACTIVE stalls with a single-day Aug-24 ``stalled_since``
# spike (the calendar's ``_stall_date`` anchors on ``stalled_since`` first, so the
# spike day clusters there). Every family is synthetic (INV-1 — same household
# fakers as the default world ⇒ PII-scan stays clean) and every shape number
# comes from params (INV-11), never a code literal.
# --------------------------------------------------------------------------- #
# Stall reasons whose recovery-deriver stall-stage (api/families.py
# ``_STALL_REASON_STAGE``) EQUALS the family's current stage — so a cohort family
# never reads "advanced past the stall stage" and stays an ACTIVE stall (A-21):
# INTEREST→{no_response, info_session_no_show}, APPLY→{app_incomplete},
# ENROLL→{forms_partial, funding_pending}. Distinct from the default
# ``_STALL_BY_STAGE`` (which includes cross-stage reasons fine for the June world).
_BTS_STALL_BY_STAGE: dict[Stage, tuple[StallReason, ...]] = {
    Stage.INTEREST: (StallReason.NO_RESPONSE, StallReason.INFO_SESSION_NO_SHOW),
    Stage.APPLY: (StallReason.APP_INCOMPLETE,),
    Stage.ENROLL: (StallReason.FORMS_PARTIAL, StallReason.FUNDING_PENDING),
}


def _stall_date_for(
    rng: random.Random,
    *,
    on_spike: bool,
    anchor: datetime,
    spread_days: int,
) -> datetime:
    """A back-to-school family's ``stalled_since`` — the spike day or a band before it.

    Spike families land exactly on ``anchor`` (a single-day spike — the surge that
    "just happened"). Off-spike families spread BACKWARD across
    ``[anchor - spread_days, anchor]`` with a deterministic minute-of-day jitter,
    so ``anchor`` is the MOST-RECENT stall day (the calendar opens on the spike
    month and the surge is the freshest cluster, with an overdue tail trailing
    back). Backward-only — no family stalls after the surge. All draws from ``rng``.
    """
    if on_spike:
        offset_days = 0
    else:
        offset_days = -rng.randint(0, spread_days)
    return anchor + timedelta(days=offset_days, minutes=rng.randint(0, 1439))


def _build_back_to_school_family(
    rng: random.Random,
    *,
    stalled_since: datetime,
) -> tuple[FamilyRecord, LeadsNew, AppForm, EnrollmentForms, CommunityProfile]:
    """Build one ACTIVE back-to-school stall + its four joined source rows (A-21).

    Active = stalled, not recovered: the stage is held to the pre-tuition funnel
    and the funding_state below the §5.4 first-installment floor, so the derived
    recovery_state reads ``stalled``/``working`` (never ``recovered``). The
    family's ``stalled_since`` is the supplied spike/spread anchor — the calendar
    grouping key. Everything else reuses the default household fakers (INV-1).
    """
    family_id = _uuid(rng)
    lead_id = _uuid(rng)
    app_form_id = _uuid(rng)
    enrollment_form_id = _uuid(rng)
    community_profile_id = _uuid(rng)

    surname = rng.choice(_SURNAMES)
    given = rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    # Active stalls sit in the pre-tuition funnel (an enrolled/tuition family is
    # closing, not stalling) so the cohort reads as a recovery surface.
    stage = rng.choice((Stage.INTEREST, Stage.APPLY, Stage.ENROLL))
    # A-23 — active recovery targets are full-pay only (Texas voucher / self-pay).
    funding_type = _weighted_choice(rng, _TARGETED_FUNDING_WEIGHTS)
    product = _weighted_choice(rng, _PRODUCT_WEIGHTS)
    attribution_source = rng.choice(_ATTRIBUTION_SOURCES)
    email = _synthetic_email(rng, surname)
    # created_at precedes the stall anchor so the family existed before it stalled.
    # A tight 0-30d band (vs the lead age) so the freshest surge families (stalled
    # within the contact window of "now") read FRESH while the trailing tail reads
    # OVERDUE — the mock's fresh-surge-plus-overdue-stragglers recency mix.
    created = stalled_since - timedelta(days=rng.randint(0, 30), minutes=rng.randint(0, 1439))
    utm = _synthetic_utm(rng, attribution_source)

    # Stall reason mapped so the recovery-deriver stall-stage equals current_stage
    # ⇒ the family never reads "advanced" (stays an ACTIVE stall; A-21).
    stall_reason = rng.choice(_BTS_STALL_BY_STAGE[stage])
    # Below the §5.4 first-installment floor ⇒ never derives RECOVERED on funding.
    funding_state = rng.choice(
        (FundingState.NONE, FundingState.APPLIED, FundingState.AWARDED_SELFREPORT)
    )

    family = FamilyRecord(
        family_id=family_id,
        display_name=f"The {surname} Family",
        primary_contact_synthetic_email=email,
        lead_id=lead_id,
        app_form_id=app_form_id,
        enrollment_form_id=enrollment_form_id,
        community_profile_id=community_profile_id,
        current_stage=stage,
        stall_reason=stall_reason,
        stalled_since=stalled_since,
        funding_type=funding_type,
        funding_state=funding_state,
        attribution_source=attribution_source,
        attribution_utm=utm,
        crm_seam_status=_seam_status(rng),
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        updated_at=min(created + timedelta(days=rng.randint(0, 14)), stalled_since),
    )

    lead = LeadsNew(
        lead_id=lead_id,
        family_id=family_id,
        synthetic_first_name=given,
        synthetic_last_name=surname,
        synthetic_email=email,
        synthetic_phone=_synthetic_phone(rng),
        source=attribution_source,
        utm=utm,
        product_interest=product,
        grade_interest=rng.choice(_GRADES),
        region=region,
        num_children=_num_children(rng),
        created_at=created,
    )

    app_form = _build_app_form(rng, app_form_id, family_id, stage, created)
    enrollment = _build_enrollment(rng, enrollment_form_id, family_id, stage, created)
    profile = _build_profile(rng, community_profile_id, family_id, created)
    return family, lead, app_form, enrollment, profile


def generate_back_to_school(
    *,
    count: int,
    seed: int,
    spike_year: int,
    spike_month: int,
    spike_day: int,
    spike_share: float,
    spread_days: int,
) -> SyntheticDataset:
    """Generate the deterministic back-to-school volume cohort (A-21; S12 W2).

    A SEPARATE cohort drawn from its own ``random.Random(seed)`` — the default
    ``generate`` stream is untouched (byte-identical), so the determinism guard
    stays green. Produces ``count`` ACTIVE stalls; a ``spike_share`` fraction land
    exactly on ``spike_year-spike_month-spike_day`` (the single-day surge), the
    rest spread across ``[-spread_days, +spread_days]`` of that anchor. Same seed ⇒
    byte-identical output. All synthetic (INV-1); every number a param (INV-11).

    Args:
        count: number of active-stall families in the cohort.
        seed: the cohort's own RNG seed (isolated from the default stream).
        spike_year/spike_month/spike_day: the single-day spike anchor.
        spike_share: fraction of ``count`` whose ``stalled_since`` is the spike day.
        spread_days: the +/- band the off-spike families spread across.

    Returns:
        An in-memory :class:`SyntheticDataset` — the volume scenario seed.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    if not (0.0 <= spike_share <= 1.0):
        raise ValueError("spike_share must be in [0, 1]")

    anchor = datetime(spike_year, spike_month, spike_day, tzinfo=UTC)
    spike_count = round(count * spike_share)

    rng = random.Random(seed)
    ds = SyntheticDataset()
    for i in range(count):
        on_spike = i < spike_count
        stalled_since = _stall_date_for(
            rng, on_spike=on_spike, anchor=anchor, spread_days=spread_days
        )
        family, lead, app_form, enrollment, profile = _build_back_to_school_family(
            rng, stalled_since=stalled_since
        )
        ds.families.append(family)
        ds.leads.append(lead)
        ds.app_forms.append(app_form)
        ds.enrollment_forms.append(enrollment)
        ds.community_profiles.append(profile)
    _populate_students(ds, seed=seed)  # A-24 — per-child rows for the volume cohort.
    return ds


# --------------------------------------------------------------------------- #
# MD — the curated `COCKPIT_SCENARIO=demo` cohort (MULTI_AGENT_COCKPIT §10.1).
#
# A small, hand-shaped, deterministic fixture for the on-camera demo — NOT the
# volume/cadence cohorts above. 8–10 synthetic households with controlled, legible
# state: exactly one two-child household (household→student grouping), a stage
# spread (mid-funnel + "went all the way"/Closed-pending-SIS), a funding/voucher
# spread, an assignment split across the two demo agents (the closer holds the
# high-value / deadline / multi-child case, the setter holds standard
# re-engagement, ≥1 household is left unassigned for the admin to route LIVE), and
# seeded SIS divergence.
#
# SIS divergence is produced by the EXISTING roster generator, not re-implemented:
# ``generate_sis_roster`` sorts the PAID families by ``family_id`` and seeds the
# first → 🔴 paid_not_in_sis, the second → 🟡 records_lag, the rest → ✅ confirmed
# (it only needs ≥3 paid families). This cohort seeds FOUR paid households
# (3 funded TUITION "went all the way" + 1 first-installment ENROLL), so the M5
# reconcile yields ≥1 of each bucket deterministically.
#
# LIVE-Supabase concern (director live-step, NOT built here): seeding this cohort
# into the shared apply-pages Supabase DB — "clear all existing synthetic/cohort
# data, then seed each family as a synthetic anon-session user" — is the external
# §10.5 live step (cloud-throwaway vs local-Docker was never resolved). This
# function builds only the in-memory ``SyntheticDataset`` (the gate path); the
# in-memory repo hydrates from it exactly like every other scenario.
# --------------------------------------------------------------------------- #

# The two seeded demo agents — STABLE per-rank uuid literals, identical to the
# 0013_sales_agents.sql seed + app/core/sales_agents.py (the closer is rank 1, the
# setter rank 2). Kept as literals here to avoid importing app.core into the data
# generator; the registry test guards that these stay in sync.
_DEMO_CLOSER_ID = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1 — closer
_DEMO_SETTER_ID = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2 — setter

# Fixed seed for the curated cohort — its OWN RNG instance, isolated from the
# default/back-to-school/realistic streams (same discipline as the other cohorts),
# so the determinism guards hold and the cohort is byte-identical across runs.
_DEMO_SEED = 0x6D64  # "md"


@dataclass(frozen=True)
class _DemoHousehold:
    """One curated household's controlled, legible demo state (MULTI_AGENT §10.1)."""

    surname: str
    stage: Stage
    funding_type: FundingType
    funding_state: FundingState
    num_children: int
    assigned_rep_id: UUID | None
    stall_reason: StallReason | None
    note: str  # the demo intent (documentation only — never emitted)
    # Conversion-signal raw inputs (DH-2). Both synthetic + per-spec so the cohort
    # carries a deliberate HIGH/MED/LOW affluence + income spread for the downstream
    # signal. `neighborhood` is a coarse AGGREGATE area label (no minor geo —
    # P-4/INV-6); `self_reported_income` is whole USD (`None` = not yet provided).
    neighborhood: str
    self_reported_income: int | None


# Synthetic AGGREGATE neighborhood / area labels (NOT minor geo — P-4/INV-6); a
# small set assigned across the cohort to give the conversion signal a believable
# affluence spread (HIGH→LOW). The downstream DH-1 signal maps these to affluence
# via a PARAMS table (built there, not here — DH-2 carries only the raw label).
_DEMO_NB_HIGH = "Highland Park"  # affluent district (HIGH affluence)
_DEMO_NB_MID = "Riverside"  # middle district (MED affluence)
_DEMO_NB_MODEST = "Eastgate"  # modest district (LOW affluence)
_DEMO_NB_MIXED = "Lakeview"  # mixed district (MED affluence)

# The curated cohort — exactly 6 households, hand-shaped for legible on-camera
# state (DH-2 trimmed from 9 → the deliberate 6: dropped Johnson, Garcia, Ahmed).
# Composition (verified by ``test_demo_scenario_shape`` + the SIS divergence test):
#   * EXACTLY ONE two-child household (the Rivera household — closer, mid-funnel).
#   * Stage spread (the full funnel ladder): 1 APPLY (Kim — mid-funnel setter
#     re-engagement) + 1 ENROLL (Rivera) + 3 "went all the way" (TUITION, funded
#     ⇒ Closed — pending SIS confirmation) + 1 top-of-funnel (INTEREST — Silva,
#     unassigned intake).
#   * Funding/voucher spread: TEFA standard (voucher), self-pay, disability,
#     homeschool — so the voucher clocks + SIS buckets each show something.
#   * 3 PAID households (Okafor + Nguyen + Patel, all FUNDED) ⇒ the roster
#     generator seeds 🔴 + 🟡 + ✅ deterministically (sis_roster needs ≥3 paid).
#   * Assignment: the closer (#1) holds the multi-child + high-value cases; the
#     setter (#2) holds standard re-engagement; ≥1 (Silva) is UNASSIGNED.
#   * Conversion-signal spread (DH-2): a deliberate HIGH/MED/LOW mix of
#     neighborhood affluence + self-reported income, with mid-funnel families
#     carrying `None` income (not yet provided).
_DEMO_HOUSEHOLDS: tuple[_DemoHousehold, ...] = (
    # The two-child household — closer, mid-funnel (the household→student demo).
    # HIGH: affluent neighborhood + high self-reported income — the prime case.
    _DemoHousehold(
        surname="Rivera",
        stage=Stage.ENROLL,
        funding_type=FundingType.TEFA_STANDARD,
        funding_state=FundingState.AWARDED_SELFREPORT,
        num_children=2,
        assigned_rep_id=_DEMO_CLOSER_ID,
        stall_reason=StallReason.FORMS_PARTIAL,
        note="multi-child voucher household mid-enroll — the high-value closer case",
        neighborhood=_DEMO_NB_HIGH,
        self_reported_income=185_000,
    ),
    # "Went all the way" — funded TUITION ⇒ Closed — pending SIS confirmation.
    # HIGH: top affluence + top income (the self-pay-grade voucher family).
    _DemoHousehold(
        surname="Okafor",
        stage=Stage.TUITION,
        funding_type=FundingType.TEFA_STANDARD,
        funding_state=FundingState.FUNDED,
        num_children=1,
        assigned_rep_id=_DEMO_CLOSER_ID,
        stall_reason=None,
        note="closed voucher enrollment, awaiting SIS confirmation",
        neighborhood=_DEMO_NB_HIGH,
        self_reported_income=240_000,
    ),
    # MED: mid district + mid income (a closed self-pay family).
    _DemoHousehold(
        surname="Nguyen",
        stage=Stage.TUITION,
        funding_type=FundingType.SELF_PAY,
        funding_state=FundingState.FUNDED,
        num_children=1,
        assigned_rep_id=_DEMO_SETTER_ID,
        stall_reason=None,
        note="closed self-pay enrollment, awaiting SIS confirmation",
        neighborhood=_DEMO_NB_MID,
        self_reported_income=95_000,
    ),
    # LOW: modest district + lower income — the disability-IEP ($30k tier) family.
    _DemoHousehold(
        surname="Patel",
        stage=Stage.TUITION,
        funding_type=FundingType.TEFA_DISABILITY,
        funding_state=FundingState.FUNDED,
        num_children=1,
        assigned_rep_id=_DEMO_SETTER_ID,
        stall_reason=None,
        note="closed disability-IEP ($30k tier) enrollment, awaiting SIS",
        neighborhood=_DEMO_NB_MODEST,
        self_reported_income=52_000,
    ),
    # Mid-funnel (APPLY) re-engagement — the setter's book (the mid-funnel rung
    # Garcia used to hold). MED neighborhood + a provided mid-funnel income, so the
    # conversion signal has a believable in-progress data point (not just None).
    _DemoHousehold(
        surname="Kim",
        stage=Stage.APPLY,
        funding_type=FundingType.SELF_PAY,
        funding_state=FundingState.APPLIED,
        num_children=1,
        assigned_rep_id=_DEMO_SETTER_ID,
        stall_reason=StallReason.APP_INCOMPLETE,
        note="application incomplete — standard mid-funnel setter re-engagement",
        neighborhood=_DEMO_NB_MIXED,
        self_reported_income=78_000,
    ),
    # UNASSIGNED — the intake pool the admin routes LIVE on camera. LOW: modest
    # district; income not yet provided (None) — fresh top-of-funnel lead.
    _DemoHousehold(
        surname="Silva",
        stage=Stage.INTEREST,
        funding_type=FundingType.TEFA_HOMESCHOOL,
        funding_state=FundingState.NONE,
        num_children=1,
        assigned_rep_id=None,
        stall_reason=StallReason.INFO_SESSION_NO_SHOW,
        note="fresh homeschool ($2k tier) lead — UNASSIGNED, the live-route case",
        neighborhood=_DEMO_NB_MODEST,
        self_reported_income=None,
    ),
)


def generate_demo_cohort(*, params: Params) -> SyntheticDataset:
    """Generate the curated ``COCKPIT_SCENARIO=demo`` cohort (MULTI_AGENT §10.1).

    A SEPARATE deterministic fixture (its own ``random.Random(_DEMO_SEED)``, so the
    other cohort streams stay byte-identical) of :data:`_DEMO_HOUSEHOLDS` — 6
    synthetic households with controlled, legible state for the on-camera demo:
    exactly one two-child household, a stage + funding/voucher spread, three PAID
    households (so the M5 roster generator seeds the 🔴/🟡/✅ SIS buckets), and an
    assignment split across the two demo agents with ≥1 household left UNASSIGNED
    (the intake pool the admin routes live). Same input ⇒ byte-identical output.
    All synthetic (INV-1): obviously-fake household labels, ``@example.invalid``
    emails, ``555-01xx`` phones; no clock/random (deterministic, CLAUDE §4.1).

    The LIVE-Supabase seed (clear-slate + each family a synthetic anon-session
    user) is the director's live-step (the external §10.5 gate), NOT built here:
    this returns only the in-memory dataset the gate path consumes.

    Args:
        params: the loaded params (the cohort reads its tunables from here, INV-11).

    Returns:
        An in-memory :class:`SyntheticDataset` — the curated demo scenario seed.
    """
    rng = random.Random(_DEMO_SEED)
    ds = SyntheticDataset()
    for spec in _DEMO_HOUSEHOLDS:
        family, lead, app_form, enrollment, profile = _build_demo_household(rng, spec=spec)
        ds.families.append(family)
        ds.leads.append(lead)
        ds.app_forms.append(app_form)
        ds.enrollment_forms.append(enrollment)
        ds.community_profiles.append(profile)
    _populate_students(ds, seed=_DEMO_SEED)  # A-24 — one Student per child.
    return ds


def _build_demo_household(
    rng: random.Random,
    *,
    spec: _DemoHousehold,
) -> tuple[FamilyRecord, LeadsNew, AppForm, EnrollmentForms, CommunityProfile]:
    """Build one curated demo household + its four joined source rows.

    Everything legible comes from ``spec`` (stage, funding tier/state, child count,
    assignment, stall); the incidental fields (given name, region, attribution,
    contact, engagement) are drawn from ``rng`` so the household reads believable
    while staying fully synthetic + deterministic. A "went all the way" closed
    family is anchored older (it has run the full funnel) and an active/unassigned
    family is anchored fresher.
    """
    family_id = _uuid(rng)
    lead_id = _uuid(rng)
    app_form_id = _uuid(rng)
    enrollment_form_id = _uuid(rng)
    community_profile_id = _uuid(rng)

    given = rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    product = _weighted_choice(rng, _PRODUCT_WEIGHTS)
    attribution_source = rng.choice(_ATTRIBUTION_SOURCES)
    email = _synthetic_email(rng, spec.surname)
    utm = _synthetic_utm(rng, attribution_source)

    # Closed ("went all the way") families have run the whole funnel ⇒ anchor them
    # older; active/intake families are fresher. Deterministic (drawn from rng).
    closed = spec.stage is Stage.TUITION
    if closed:
        created = _timestamp_between(rng, min_days_ago=60, max_days_ago=150)
    else:
        created = _timestamp_between(rng, min_days_ago=0, max_days_ago=30)
    stalled_since = _timestamp(rng, max_days_ago=21) if spec.stall_reason is not None else None
    assigned_at = (
        min(created + timedelta(days=rng.randint(0, 7)), _EPOCH)
        if spec.assigned_rep_id is not None
        else None
    )

    family = FamilyRecord(
        family_id=family_id,
        display_name=f"The {spec.surname} Family",
        primary_contact_synthetic_email=email,
        assigned_rep_id=spec.assigned_rep_id,
        assigned_at=assigned_at,
        lead_id=lead_id,
        app_form_id=app_form_id,
        enrollment_form_id=enrollment_form_id,
        community_profile_id=community_profile_id,
        current_stage=spec.stage,
        stall_reason=spec.stall_reason,
        stalled_since=stalled_since,
        funding_type=spec.funding_type,
        funding_state=spec.funding_state,
        attribution_source=attribution_source,
        attribution_utm=utm,
        crm_seam_status=_seam_status(rng),
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        updated_at=min(created + timedelta(days=rng.randint(0, 14)), _EPOCH),
    )

    lead = LeadsNew(
        lead_id=lead_id,
        family_id=family_id,
        synthetic_first_name=given,
        synthetic_last_name=spec.surname,
        synthetic_email=email,
        synthetic_phone=_synthetic_phone(rng),
        source=attribution_source,
        utm=utm,
        product_interest=product,
        grade_interest=rng.choice(_GRADES),
        region=region,
        # DH-2: the coarse aggregate area label the conversion signal keys on
        # (per-spec, deterministic — no minor geo, P-4/INV-6).
        neighborhood=spec.neighborhood,
        num_children=spec.num_children,
        created_at=created,
    )

    # DH-2: the family's self-reported household income (whole USD; `None` =
    # not yet provided) — set per-spec on the application (conversion-signal input).
    app_form = _build_app_form(rng, app_form_id, family_id, spec.stage, created).model_copy(
        update={"self_reported_income": spec.self_reported_income}
    )
    enrollment = _build_enrollment(rng, enrollment_form_id, family_id, spec.stage, created)
    profile = _build_profile(rng, community_profile_id, family_id, created)
    return family, lead, app_form, enrollment, profile


# --------------------------------------------------------------------------- #
# The realistic-cadence cohort — a SEPARATE deterministic cohort calibrated to
# GT's measured top-of-funnel cadence (aggregate-only numbers, INV-1). Like
# `generate_back_to_school` it draws from its OWN `random.Random(seed)`, so the
# default `generate` and `back_to_school` streams stay byte-identical.
#
# Stall / resolution model (documented here so it can't silently drift):
#   * `total` families inquire (created_at) across the measured window, spread to
#     match the `monthly_counts` weights EXACTLY (deterministic counts, not
#     sampling) — with the campaign `spike_count` on the spike day and the mild
#     `secondary_bumps` on their days; the per-month remainder fills the other
#     in-window days as evenly as possible. So the lead/created layer reproduces
#     the seasonal Jan–Mar peak, the Jan-27 burst, and the summer taper.
#   * Most families are HISTORY: they inquired months ago and moved on, so they
#     are shaped to DERIVE `recovered` (stage advanced PAST the stall stage AND
#     forms cleared AND funding ≥ first-installment) with `stalled_since=None` —
#     they belong to the funnel/history layer, not the active recovery board.
#   * The most-recent `active_count` families (by build order) are UNRESOLVED
#     ACTIVE stalls: `stalled_since` in the last `active_window_days` (so the
#     ACTIVE recovery calendar shows a believable handful per day, not hundreds),
#     stall_reason mapped so its stall-stage EQUALS current_stage (never
#     "advanced"), funding below the §5.4 floor, forms not cleared ⇒ derives
#     `stalled`.
#   * The first `dismissed_count` of those active stalls are returned as
#     `dismissed_family_ids`; the composition root (api/deps) logs a dismiss event
#     for each, so they DERIVE `dismissed` (History/dismissed is non-empty).
#   Net derived mix: active = `active_count - dismissed_count`, dismissed =
#   `dismissed_count`, recovered = everything else (`total - active_count`).
# --------------------------------------------------------------------------- #

# A family shaped as HISTORY must trip the recovery deriver's RECOVERED signals.
# We give it a tuition-stage spine with all six forms signed and funding FUNDED —
# so stage-advance, forms-cleared, AND the §5.4 funding gate all read recovered.
_REALISTIC_HISTORY_STAGE = Stage.TUITION

# Active stalls reuse the back-to-school active-stall stage set + stall map, so
# the stall-stage equals current_stage (never "advanced") — they stay ACTIVE.
_REALISTIC_ACTIVE_STAGES: tuple[Stage, ...] = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL)


@dataclass(frozen=True)
class RealisticCohort:
    """The realistic cohort: the dataset plus the family ids to dismiss (A-19).

    ``dataset`` seeds the repository; ``dismissed_family_ids`` are the active
    stalls the composition root logs a dismiss event for (so they derive
    ``dismissed`` rather than ``stalled``). Returned together so the dismiss
    intent travels with the data and stays deterministic.
    """

    dataset: SyntheticDataset
    dismissed_family_ids: list[UUID] = field(default_factory=list)


def _realistic_created_days(p: Realistic) -> list[date]:
    """The ``total`` inquiry DAYS, deterministically calibrated to the monthly shape.

    Returns one :class:`date` per family (length == ``total``), exactly matching
    each month's count, with the campaign ``spike_count`` on the spike day and the
    ``secondary_bumps`` on their days; each month's remainder fills the other
    in-window days as evenly as possible. Pure and deterministic — no RNG.
    """
    window_start = date(p.window_start_year, p.window_start_month, p.window_start_day)
    window_end = date(p.window_end_year, p.window_end_month, p.window_end_day)

    # Forced single-day counts (spike + secondary bumps), keyed by exact date.
    forced: dict[date, int] = {}
    forced[date(p.spike_year, p.spike_month, p.spike_day)] = p.spike_count
    for bump in p.secondary_bumps:
        bump_day = date(bump.year, bump.month, bump.day)
        forced[bump_day] = forced.get(bump_day, 0) + bump.count

    days: list[date] = []
    for month_key, month_total in p.monthly_counts.items():
        year_s, month_s = month_key.split("-")
        year, month = int(year_s), int(month_s)
        # The in-window days of this month.
        all_days = _days_in_month_within(year, month, window_start, window_end)
        forced_here = {d: c for d, c in forced.items() if d.year == year and d.month == month}
        forced_sum = sum(forced_here.values())
        remainder = month_total - forced_sum
        if remainder < 0:
            raise ValueError(
                f"realistic.monthly_counts[{month_key}] is smaller than its forced day counts"
            )
        spread_days = [d for d in all_days if d not in forced_here]
        # Emit the forced days at their exact counts.
        for d, c in forced_here.items():
            days.extend([d] * c)
        # Spread the remainder across the non-forced days as evenly as possible
        # (round-robin ⇒ deterministic, no RNG, stable across runs).
        if spread_days:
            for i in range(remainder):
                days.append(spread_days[i % len(spread_days)])
        elif remainder:
            raise ValueError(f"realistic.monthly_counts[{month_key}] has no in-window spread days")
    return days


def _days_in_month_within(
    year: int, month: int, window_start: date, window_end: date
) -> list[date]:
    """Every day of ``year-month`` that lies inside ``[window_start, window_end]``."""
    first = date(year, month, 1)
    # First day of the next month, then step back one day for this month's last day.
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    out: list[date] = []
    d = max(first, window_start)
    last = min(next_month - timedelta(days=1), window_end)
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def _build_realistic_history_family(
    rng: random.Random, *, created: datetime
) -> tuple[FamilyRecord, LeadsNew, AppForm, EnrollmentForms, CommunityProfile]:
    """Build one HISTORY family that DERIVES recovered (moved on; no active stall).

    Tuition-stage, all six forms signed, funding FUNDED, ``stalled_since=None`` —
    so stage-advance, forms-cleared, and the §5.4 funding gate all read recovered.
    The family belongs to the funnel/history layer, not the active board.
    """
    family_id = _uuid(rng)
    lead_id = _uuid(rng)
    app_form_id = _uuid(rng)
    enrollment_form_id = _uuid(rng)
    community_profile_id = _uuid(rng)

    surname = rng.choice(_SURNAMES)
    given = rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    stage = _REALISTIC_HISTORY_STAGE
    funding_type = _weighted_choice(rng, _FUNDING_TYPE_WEIGHTS)
    product = _weighted_choice(rng, _PRODUCT_WEIGHTS)
    attribution_source = rng.choice(_ATTRIBUTION_SOURCES)
    email = _synthetic_email(rng, surname)
    utm = _synthetic_utm(rng, attribution_source)

    family = FamilyRecord(
        family_id=family_id,
        display_name=f"The {surname} Family",
        primary_contact_synthetic_email=email,
        lead_id=lead_id,
        app_form_id=app_form_id,
        enrollment_form_id=enrollment_form_id,
        community_profile_id=community_profile_id,
        current_stage=stage,
        stall_reason=None,
        stalled_since=None,
        funding_type=funding_type,
        funding_state=FundingState.FUNDED,
        attribution_source=attribution_source,
        attribution_utm=utm,
        crm_seam_status=_seam_status(rng),
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        updated_at=min(created + timedelta(days=rng.randint(1, 30)), _EPOCH),
    )
    lead = LeadsNew(
        lead_id=lead_id,
        family_id=family_id,
        synthetic_first_name=given,
        synthetic_last_name=surname,
        synthetic_email=email,
        synthetic_phone=_synthetic_phone(rng),
        source=attribution_source,
        utm=utm,
        product_interest=product,
        grade_interest=rng.choice(_GRADES),
        region=region,
        num_children=_num_children(rng),
        created_at=created,
    )
    app_form = _build_app_form(rng, app_form_id, family_id, stage, created)
    enrollment = _build_enrollment(rng, enrollment_form_id, family_id, stage, created)
    profile = _build_profile(rng, community_profile_id, family_id, created)
    return family, lead, app_form, enrollment, profile


def _build_realistic_active_family(
    rng: random.Random, *, created: datetime, stalled_since: datetime
) -> tuple[FamilyRecord, LeadsNew, AppForm, EnrollmentForms, CommunityProfile]:
    """Build one ACTIVE stall (recent, unresolved) — reuses the BTS active shape.

    Stage in the pre-tuition funnel, stall_reason mapped so its stall-stage equals
    current_stage (never "advanced"), funding below the §5.4 floor, forms not
    cleared ⇒ derives ``stalled``. ``stalled_since`` is the recent anchor; the
    family inquired earlier (``created``).
    """
    family_id = _uuid(rng)
    lead_id = _uuid(rng)
    app_form_id = _uuid(rng)
    enrollment_form_id = _uuid(rng)
    community_profile_id = _uuid(rng)

    surname = rng.choice(_SURNAMES)
    given = rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    stage = rng.choice(_REALISTIC_ACTIVE_STAGES)
    # A-23 — active recovery targets are full-pay only (Texas voucher / self-pay).
    funding_type = _weighted_choice(rng, _TARGETED_FUNDING_WEIGHTS)
    product = _weighted_choice(rng, _PRODUCT_WEIGHTS)
    attribution_source = rng.choice(_ATTRIBUTION_SOURCES)
    email = _synthetic_email(rng, surname)
    utm = _synthetic_utm(rng, attribution_source)

    stall_reason = rng.choice(_BTS_STALL_BY_STAGE[stage])
    funding_state = rng.choice(
        (FundingState.NONE, FundingState.APPLIED, FundingState.AWARDED_SELFREPORT)
    )

    family = FamilyRecord(
        family_id=family_id,
        display_name=f"The {surname} Family",
        primary_contact_synthetic_email=email,
        lead_id=lead_id,
        app_form_id=app_form_id,
        enrollment_form_id=enrollment_form_id,
        community_profile_id=community_profile_id,
        current_stage=stage,
        stall_reason=stall_reason,
        stalled_since=stalled_since,
        funding_type=funding_type,
        funding_state=funding_state,
        attribution_source=attribution_source,
        attribution_utm=utm,
        crm_seam_status=_seam_status(rng),
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        updated_at=min(created + timedelta(days=rng.randint(0, 14)), stalled_since),
    )
    lead = LeadsNew(
        lead_id=lead_id,
        family_id=family_id,
        synthetic_first_name=given,
        synthetic_last_name=surname,
        synthetic_email=email,
        synthetic_phone=_synthetic_phone(rng),
        source=attribution_source,
        utm=utm,
        product_interest=product,
        grade_interest=rng.choice(_GRADES),
        region=region,
        num_children=_num_children(rng),
        created_at=created,
    )
    app_form = _build_app_form(rng, app_form_id, family_id, stage, created)
    enrollment = _build_enrollment(rng, enrollment_form_id, family_id, stage, created)
    profile = _build_profile(rng, community_profile_id, family_id, created)
    return family, lead, app_form, enrollment, profile


def generate_realistic(*, params: Realistic) -> RealisticCohort:
    """Generate the realistic-cadence cohort, calibrated to GT's measured cadence.

    A SEPARATE deterministic cohort drawn from its own ``random.Random(seed)`` —
    the default ``generate`` and ``back_to_school`` streams are untouched
    (byte-identical), so their determinism guards hold. ``total`` families inquire
    across the measured window matching ``monthly_counts`` exactly (with the
    campaign ``spike_count`` and ``secondary_bumps``); the most-recent
    ``active_count`` are unresolved active stalls (``stalled_since`` in the last
    ``active_window_days``), of which ``dismissed_count`` are returned for the
    composition root to dismiss; the rest derive ``recovered``. Every shape number
    is a param (INV-11); all rows are synthetic (INV-1). See the module-level
    stall/resolution model comment for the full contract.

    Args:
        params: the validated ``realistic`` params block (§8).

    Returns:
        A :class:`RealisticCohort` — the dataset plus the dismiss-target ids.
    """
    if params.active_count > params.total:
        raise ValueError("realistic.active_count cannot exceed total")

    created_days = _realistic_created_days(params)
    assert len(created_days) == params.total

    rng = random.Random(params.seed)
    epoch_floor = _EPOCH - timedelta(days=params.active_window_days)

    ds = SyntheticDataset()
    dismissed_family_ids: list[UUID] = []

    # The LAST `active_count` inquiry-days (in build order) become the active
    # stalls. created_days is month-ordered, so the tail is the most-recent slice
    # of inquiries — a believable "recently went quiet" cohort.
    active_start = params.total - params.active_count

    for i, day in enumerate(created_days):
        created = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
            minutes=rng.randint(0, 1439)
        )
        if i >= active_start:
            # Active stall: a recent stalled_since spread across the active window.
            # Clamped to [created, _EPOCH] so a family never "stalls before it
            # inquired" (the active tail's created can itself be recent).
            stalled_since = epoch_floor + timedelta(
                days=rng.randint(0, params.active_window_days),
                minutes=rng.randint(0, 1439),
            )
            stalled_since = min(max(stalled_since, created), _EPOCH)
            family, lead, app_form, enrollment, profile = _build_realistic_active_family(
                rng, created=created, stalled_since=stalled_since
            )
            # The first `dismissed_count` active stalls are the dismiss targets.
            if (i - active_start) < params.dismissed_count:
                dismissed_family_ids.append(family.family_id)
        else:
            family, lead, app_form, enrollment, profile = _build_realistic_history_family(
                rng, created=created
            )
        ds.families.append(family)
        ds.leads.append(lead)
        ds.app_forms.append(app_form)
        ds.enrollment_forms.append(enrollment)
        ds.community_profiles.append(profile)

    _populate_students(ds, seed=params.seed)  # A-24 — per-child rows for the demo cohort.
    return RealisticCohort(dataset=ds, dismissed_family_ids=dismissed_family_ids)


# --------------------------------------------------------------------------- #
# Marketing seed inventory (CONTENT_SPEC §11). Distinct from the family spine:
# these are the fixed, synthetic brand-OS + content seeds that make the S4
# content engine, the brand judge, and BOTH §9 BLOCK paths (V-2 grounding, V-3
# COPPA) demoable on synthetic data alone. They are *fixed content* (not RNG-
# drawn) so they're byte-reproducible by construction, and carry no wall-clock
# (a fixed ISO timestamp is baked in) — determinism per CLAUDE.md §4.1.
#
# This module stays the only seed writer (NFR-1) and imports no LLM/eval/adapter
# code (INV-1, CLAUDE.md §3) — only the pure schema models above. The lone real
# name anywhere is the INTENTIONAL Tom Babb attribution (INV-7): the marketing
# skills are HIS, attributed, never claimed as the builder's authorship.
# --------------------------------------------------------------------------- #

# Fixed ISO timestamp for every seed record's provenance (no wall clock).
_SEED_TS = _EPOCH.isoformat()

# INV-7 (LOCKED): every recipe's attribution names Tom Babb. Single canonical
# string so the attribution can never silently drift off a recipe.
_TOM_BABB_ATTRIBUTION = (
    "Marketing skills attributed to Tom Babb (GT School). Illustrative seed "
    "template pending the real Tom Babb source; not the builder's authorship."
)


def _seed_provenance() -> Provenance:
    """Provenance shared by every §11 seed record: synthetic_seed + fixed timestamp."""
    return Provenance(generated_by=GeneratedBy.SYNTHETIC_SEED, created_at=_SEED_TS)


def generate_brand_memory() -> list[BrandMemoryItem]:
    """The §11.1 brand-memory seed inventory — ≥8 items that condition S4 generation.

    Fixed, synthetic, deterministic. Covers ≥3 ``voice_attribute``, ≥3
    ``exemplar`` (short_caption / faq_block / email_body), and ≥2
    ``dont_rule``/``signal`` — including the two named rules that make the §9
    V-4/gate enforcement demonstrable: "Don't use speed multipliers"
    (``signal=discarded``) and "Don't target children". Each is ``active``,
    versioned, weighted, with ``synthetic_seed`` provenance (FR-3.2).
    """
    prov = _seed_provenance()
    return [
        BrandMemoryItem(
            id="bm-voice-mastery",
            kind=BrandMemoryKind.VOICE_ATTRIBUTE,
            content="Confident, mastery-focused, parent-respectful.",
            weight=1.0,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-voice-concrete",
            kind=BrandMemoryKind.VOICE_ATTRIBUTE,
            content="Concrete over hype — describe the program, never promise outcomes.",
            weight=1.0,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-voice-plain",
            kind=BrandMemoryKind.VOICE_ATTRIBUTE,
            content="Plain language, no jargon; speak to the parent's real decision.",
            weight=0.8,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-exemplar-short-caption",
            kind=BrandMemoryKind.EXEMPLAR,
            content=(
                "Gifted K-8, built around mastery. See how a GT School day actually "
                "works for your child."
            ),
            signal=BrandMemorySignal.KEPT,
            channel_scope=[Channel.INSTAGRAM],
            weight=0.7,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-exemplar-faq-block",
            kind=BrandMemoryKind.EXEMPLAR,
            content=(
                "Q: Is GT School a test-prep program? A: No. GT School is a mastery-based "
                "gifted K-8 program; students advance when they have truly learned the material."
            ),
            signal=BrandMemorySignal.KEPT,
            channel_scope=[Channel.GEO],
            weight=0.7,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-exemplar-email-body",
            kind=BrandMemoryKind.EXEMPLAR,
            content=(
                "Thanks for your interest in GT School. Here is what the next step looks "
                "like for your family, and a time to talk it through with our team."
            ),
            signal=BrandMemorySignal.KEPT,
            channel_scope=[Channel.EMAIL],
            weight=0.7,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-dont-speed-multipliers",
            kind=BrandMemoryKind.DONT_RULE,
            content=(
                "Don't use speed multipliers. Claims like '4X speed' / '2X faster' are "
                "unverifiable performance multiplier hype and get discarded."
            ),
            signal=BrandMemorySignal.DISCARDED,
            weight=1.0,
            active=True,
            version=1,
            provenance=prov,
        ),
        BrandMemoryItem(
            id="bm-dont-target-children",
            kind=BrandMemoryKind.DONT_RULE,
            content=(
                "Don't target children. Never address or target minors; speak to parents "
                "and educators only (COPPA-safe)."
            ),
            signal=BrandMemorySignal.DISCARDED,
            weight=1.0,
            active=True,
            version=1,
            provenance=prov,
        ),
    ]


def generate_brand_rules() -> list[BrandRule]:
    """The §11.2 brand-rule seed inventory — ≥4 rules as data that drive §9 V-2/V-4.

    Two ``never`` rules ("no unverifiable performance claims" / "never target
    minors"), one ``must`` ("lead with the program, not hype"), one ``avoid``
    ("avoid test-prep framing"). Each carries the right ``enforced_by`` /
    ``severity``, is ``active``, with ``synthetic_seed`` provenance (FR-3.12).
    """
    prov = _seed_provenance()
    return [
        BrandRule(
            id="br-no-unverifiable-claims",
            rule_type=RuleType.NEVER,
            statement=("No unverifiable performance claims (4X/2X speed, fastest, guaranteed)."),
            enforced_by=EnforcedBy.GROUNDING,
            severity=Severity.BLOCK,
            active=True,
            provenance=prov,
        ),
        BrandRule(
            id="br-never-target-minors",
            rule_type=RuleType.NEVER,
            statement="Never target or address minors; parents and educators only.",
            enforced_by=EnforcedBy.COPPA,
            severity=Severity.BLOCK,
            active=True,
            provenance=prov,
        ),
        BrandRule(
            id="br-lead-with-program",
            rule_type=RuleType.MUST,
            statement="Lead with the program and the parent's decision, not hype.",
            enforced_by=EnforcedBy.BRAND,
            severity=Severity.WARN,
            active=True,
            provenance=prov,
        ),
        BrandRule(
            id="br-avoid-test-prep",
            rule_type=RuleType.AVOID,
            statement="Avoid test-prep framing; GT is mastery-based gifted K-8.",
            enforced_by=EnforcedBy.BRAND,
            severity=Severity.WARN,
            active=True,
            provenance=prov,
        ),
    ]


def generate_recipes() -> list[MarketingRecipe]:
    """The §11.3 recipe seed inventory — ≥3 runnable templates, each Tom-Babb-attributed.

    "GEO FAQ builder", "Parent nurture email", "Comparison-table generator".
    INV-7 (LOCKED): EVERY recipe's ``attribution`` names Tom Babb — the marketing
    skills are HIS, attributed, never the builder's. Each is marked
    ``synthetic_seed`` and flagged illustrative pending the real source (§8.5).
    """
    prov = _seed_provenance()
    return [
        MarketingRecipe(
            id="recipe-geo-faq-builder",
            name="GEO FAQ builder",
            attribution=_TOM_BABB_ATTRIBUTION,
            description=(
                "Builds an authoritative FAQ block engineered to win AI-search "
                "citations on a target prompt against a competitor set (§7)."
            ),
            parameters=[
                RecipeParam(
                    key="targetPrompt",
                    label="Target AI-search prompt",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
                RecipeParam(
                    key="competitorSet",
                    label="Competitor set",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
            ],
            prompt_template=(
                "Write a concise, grounded FAQ block answering '{targetPrompt}' for "
                "prospective parents, citing only verifiable facts about GT School and "
                "honestly contrasting with {competitorSet}. No performance multipliers."
            ),
            output_channel=Channel.GEO,
            output_format=ContentFormat.FAQ_BLOCK,
            brand_rule_refs=["br-no-unverifiable-claims", "br-avoid-test-prep"],
            version=1,
            provenance=prov,
        ),
        MarketingRecipe(
            id="recipe-parent-nurture-email",
            name="Parent nurture email",
            attribution=_TOM_BABB_ATTRIBUTION,
            description=(
                "Drafts a stage- and persona-aware nurture email that leads with the "
                "program and the parent's next decision, never hype."
            ),
            parameters=[
                RecipeParam(
                    key="stage",
                    label="Funnel stage",
                    type=RecipeParamType.ENUM,
                    required=True,
                    options=["interest", "apply", "enroll", "tuition"],
                ),
                RecipeParam(
                    key="persona",
                    label="Parent persona",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
            ],
            prompt_template=(
                "Write a warm, plain-language nurture email for a {persona} parent at the "
                "{stage} stage. Lead with the program and a concrete next step; no "
                "guarantees, no speed multipliers, parents only."
            ),
            output_channel=Channel.EMAIL,
            output_format=ContentFormat.EMAIL_BODY,
            brand_rule_refs=["br-lead-with-program", "br-never-target-minors"],
            version=1,
            provenance=prov,
        ),
        MarketingRecipe(
            id="recipe-comparison-table-generator",
            name="Comparison-table generator",
            attribution=_TOM_BABB_ATTRIBUTION,
            description=(
                "Generates an honest, source-able comparison table of GT School vs a "
                "competitor set for GEO surfaces (§7)."
            ),
            parameters=[
                RecipeParam(
                    key="competitorSet",
                    label="Competitor set",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
            ],
            prompt_template=(
                "Build a comparison table of GT School vs {competitorSet} across mastery "
                "model, grade band, and format. Use only verifiable, source-able rows; "
                "never claim '#1', 'fastest', or any unverifiable multiplier."
            ),
            output_channel=Channel.GEO,
            output_format=ContentFormat.COMPARISON_TABLE,
            brand_rule_refs=["br-no-unverifiable-claims"],
            version=1,
            provenance=prov,
        ),
        # The two under-posted, strategically-critical themes (INSIGHTS): TEFA
        # $0-net affordability, and socialization-as-proof. Both carry the LOCKED
        # Tom Babb attribution (INV-7) and lean on the verifiable TEFA funding fact
        # ($10,474 standard award) rather than any unverifiable multiplier (V-2).
        MarketingRecipe(
            id="recipe-tefa-affordability",
            name="TEFA $0-net affordability explainer",
            attribution=_TOM_BABB_ATTRIBUTION,
            description=(
                "Drafts a grounded affordability explainer showing how the Texas "
                "Education Freedom Account (TEFA) can bring a family's net tuition "
                "to roughly $0 — an under-posted theme that answers the #1 parent "
                "objection without overclaiming."
            ),
            parameters=[
                RecipeParam(
                    key="fundingTier",
                    label="TEFA funding tier",
                    type=RecipeParamType.ENUM,
                    required=True,
                    options=["standard", "disability_iep", "homeschool"],
                ),
                RecipeParam(
                    key="persona",
                    label="Parent persona",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
            ],
            prompt_template=(
                "Write a plain-language affordability explainer for a {persona} parent "
                "showing how the Texas Education Freedom Account ({fundingTier} tier) "
                "offsets GT School tuition toward a near-$0 net cost. Cite only the "
                "verifiable TEFA award amount; no guarantees, no speed multipliers, "
                "parents only."
            ),
            output_channel=Channel.EMAIL,
            output_format=ContentFormat.EMAIL_BODY,
            brand_rule_refs=["br-no-unverifiable-claims", "br-lead-with-program"],
            version=1,
            provenance=prov,
        ),
        MarketingRecipe(
            id="recipe-socialization-proof",
            name="Socialization-as-proof story",
            attribution=_TOM_BABB_ATTRIBUTION,
            description=(
                "Generates an honest socialization-as-proof story that answers the "
                "'but what about socialization?' objection with concrete community, "
                "cohort, and in-person-intensive detail — an under-posted theme that "
                "reframes a perceived weakness as evidence."
            ),
            parameters=[
                RecipeParam(
                    key="audience",
                    label="Audience segment",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
                RecipeParam(
                    key="proofPoint",
                    label="Concrete community proof point",
                    type=RecipeParamType.STRING,
                    required=True,
                ),
            ],
            prompt_template=(
                "Write a warm, concrete short caption for a {audience} audience that "
                "reframes socialization as a GT School strength, using {proofPoint} as "
                "real, source-able evidence. Describe the community; never promise "
                "outcomes, never use multipliers, parents and educators only."
            ),
            output_channel=Channel.INSTAGRAM,
            output_format=ContentFormat.SHORT_CAPTION,
            brand_rule_refs=["br-lead-with-program", "br-never-target-minors"],
            version=1,
            provenance=prov,
        ),
    ]


def _candidate(
    *,
    suffix: str,
    channel: Channel,
    fmt: ContentFormat,
    concept: str,
    copy_text: str,
    audience: AudienceTag,
    claims: list[str] | None = None,
) -> ContentCandidate:
    """Build one fixed §11.4 ContentCandidate in the shared demo batch."""
    return ContentCandidate(
        id=f"cc-seed-{suffix}",
        batch_id="batch-seed-demo-001",
        prompt="Seed demo: draft on-brand GT School marketing copy.",
        channel=channel,
        format=fmt,
        concept=concept,
        copy=copy_text,
        claims=claims or [],
        audience_tag=audience,
        lifecycle=LifecycleStage.CANDIDATE,
        decision=HumanDecision(),
        provenance=_seed_provenance(),
    )


def generate_content_batch() -> list[ContentCandidate]:
    """The §11.4 content batch — ≥6 candidates in one batch, with both BLOCK demos.

    One ``batch_id`` groups the run. The batch deliberately includes one
    candidate that FAILS §9 V-2 (``copy_text`` contains "4X speed") and one that
    FAILS V-3 (a minor-targeting signal — "Hey kids, ages 9-12..."; note the
    ``audience_tag`` itself can never be a minor, INV-6, so the V-3 failure is
    encoded in the copy), so BOTH BLOCK paths are demoable. The rest are clean.
    """
    return [
        # Clean candidates (pass all four rules).
        _candidate(
            suffix="clean-short-caption",
            channel=Channel.INSTAGRAM,
            fmt=ContentFormat.SHORT_CAPTION,
            concept="Show a real mastery-based GT School day to prospective parents.",
            copy_text=(
                "Mastery-based gifted K-8. See how a GT School day actually fits your child's pace."
            ),
            audience=AudienceTag.PROSPECTIVE_PARENT,
        ),
        _candidate(
            suffix="clean-faq",
            channel=Channel.GEO,
            fmt=ContentFormat.FAQ_BLOCK,
            concept="Answer 'is GT School test prep?' for AI-search.",
            copy_text=(
                "Q: Is GT School a test-prep program? A: No. It is a mastery-based gifted "
                "K-8 program where students advance once they have learned the material."
            ),
            audience=AudienceTag.GENERAL,
        ),
        _candidate(
            suffix="clean-email",
            channel=Channel.EMAIL,
            fmt=ContentFormat.EMAIL_BODY,
            concept="Warm nurture email leading with the next step.",
            copy_text=(
                "Thanks for your interest in GT School. Here is what the next step looks "
                "like for your family and a time to talk it through with our team."
            ),
            audience=AudienceTag.PROSPECTIVE_PARENT,
        ),
        _candidate(
            suffix="clean-comparison",
            channel=Channel.GEO,
            fmt=ContentFormat.COMPARISON_TABLE,
            concept="Honest GT vs alternatives table for GEO.",
            copy_text=(
                "GT School: mastery-based, gifted K-8, online. A clear, source-able "
                "comparison of model, grade band, and format."
            ),
            audience=AudienceTag.GENERAL,
        ),
        # V-2 BLOCK demo: an unverifiable performance multiplier ("4X speed").
        _candidate(
            suffix="block-v2-speed",
            channel=Channel.INSTAGRAM,
            fmt=ContentFormat.AD_COPY,
            concept="Hype variant that overclaims — must BLOCK on grounding (V-2).",
            copy_text=(
                "Kids learn at 4X speed with GT School — the fastest gifted program anywhere!"
            ),
            audience=AudienceTag.PROSPECTIVE_PARENT,
            claims=["Kids learn at 4X speed"],
        ),
        # V-3 BLOCK demo: a minor-targeting signal in the copy (COPPA, V-3).
        _candidate(
            suffix="block-v3-minor",
            channel=Channel.TIKTOK,
            fmt=ContentFormat.SHORT_CAPTION,
            concept="Copy that addresses children directly — must BLOCK on COPPA (V-3).",
            copy_text=(
                "Hey kids, ages 9-12 — sign up yourself and start your GT School adventure today!"
            ),
            audience=AudienceTag.GENERAL,
        ),
    ]


def generate_library_assets() -> list[LibraryAsset]:
    """The §11.4 library seed inventory — ≥4 kept + validated assets.

    Across ``copy`` / ``faq_block`` / ``comparison_table`` asset types. Only
    validated content enters the library, so each carries a passing
    ``validation`` id and ``lifecycle=kept`` (FR-3.4); ``synthetic_seed``
    provenance throughout.
    """
    prov = _seed_provenance()
    return [
        LibraryAsset(
            id="lib-copy-mastery-caption",
            title="Mastery-based day — short caption",
            asset_type=LibraryAssetType.COPY,
            channel=Channel.INSTAGRAM,
            format=ContentFormat.SHORT_CAPTION,
            body=(
                "Mastery-based gifted K-8. See how a GT School day actually fits your child's pace."
            ),
            tags=["mastery", "k8", "prospective_parent"],
            search_text="mastery-based gifted k-8 day short caption prospective parent",
            validation="vr-seed-pass-001",
            lifecycle=LifecycleStage.KEPT,
            provenance=prov,
        ),
        LibraryAsset(
            id="lib-faq-not-test-prep",
            title="FAQ: is GT School test prep?",
            asset_type=LibraryAssetType.FAQ_BLOCK,
            channel=Channel.GEO,
            format=ContentFormat.FAQ_BLOCK,
            body=(
                "Q: Is GT School a test-prep program? A: No. It is a mastery-based gifted "
                "K-8 program where students advance once they have learned the material."
            ),
            tags=["faq", "test-prep", "mastery", "geo"],
            search_text="faq is gt school test prep mastery gifted k-8 geo",
            validation="vr-seed-pass-002",
            lifecycle=LifecycleStage.KEPT,
            provenance=prov,
        ),
        LibraryAsset(
            id="lib-faq-funding",
            title="FAQ: how does TEFA funding work?",
            asset_type=LibraryAssetType.FAQ_BLOCK,
            channel=Channel.GEO,
            format=ContentFormat.FAQ_BLOCK,
            body=(
                "Q: How does funding work? A: Eligible families may use a TEFA award "
                "toward tuition, disbursed in installments. Our team walks you through it."
            ),
            tags=["faq", "funding", "tefa"],
            search_text="faq funding tefa award installments tuition",
            validation="vr-seed-pass-003",
            lifecycle=LifecycleStage.KEPT,
            provenance=prov,
        ),
        LibraryAsset(
            id="lib-comparison-gt-vs-alts",
            title="Comparison table: GT School vs alternatives",
            asset_type=LibraryAssetType.COMPARISON_TABLE,
            channel=Channel.GEO,
            format=ContentFormat.COMPARISON_TABLE,
            body=(
                "GT School: mastery-based, gifted K-8, online. A clear, source-able "
                "comparison of model, grade band, and format vs alternatives."
            ),
            tags=["comparison", "geo", "competitors"],
            search_text="comparison table gt school vs alternatives mastery gifted k-8 geo",
            validation="vr-seed-pass-004",
            lifecycle=LifecycleStage.KEPT,
            provenance=prov,
        ),
    ]


# §11.5 sampling cadence (RESEARCH Q5): GEO coverage is never a single snapshot —
# it is measured by repeated sampling across the ICP prompt set on a weekly cadence
# (§7.4). One canonical note so the cadence can't silently drift across seeds.
_GEO_SAMPLING_NOTE = (
    "Coverage measured by repeated sampling, not a single snapshot: ~30 ICP "
    "prompts sampled weekly across AI-search engines; baseline starts at 0% "
    "and is grown deliberately (CONTENT_SPEC §7.4, RESEARCH Q5)."
)


def _geo_piece(
    *,
    int_id: int,
    target_prompt: str,
    structure: GeoStructure,
    body: str,
    citation_targets: list[str],
    structured_data_note: str | None = None,
    validation_ref: str,
) -> GeoContentPiece:
    """Build one fixed §11.5 GeoContentPiece seed.

    Deterministic: the id is a fixed `UUID(int=...)` (never `uuid4`, which would
    reseed from the OS), the competitor set is the LOCKED gifted-school universe
    (single source of truth, INV-6), the baseline is the 0% baseline (§7.1), and
    a repeated-sampling note is always attached (§7.4). `claims_text` is left
    empty: these seeds make their case in structured `body` prose, carrying no
    bare empirical claim strings (which, being source-less, would fail V-2).
    """
    # Aliased fields are passed by their wire (camelCase) alias — matching the
    # `ContentCandidate(copy=...)` convention in `_candidate` above, so mypy reads
    # the generated init signature without a per-call `type: ignore`.
    return GeoContentPiece(
        id=UUID(int=int_id, version=4),
        targetPrompt=target_prompt,
        geoStructure=structure,
        body=body,
        # §7.3 / INV-6: the LOCKED gifted-school set — never retyped, single source.
        competitorSet=list(GIFTED_SCHOOL_COMPETITOR_SET),
        citationTargets=citation_targets,
        structuredDataNote=structured_data_note,
        baselineCoverage=0.0,
        samplingNote=_GEO_SAMPLING_NOTE,
        validation=validation_ref,
        lifecycle=LifecycleStage.KEPT,
        provenance=_seed_provenance(),
    )


def generate_geo_content_pieces() -> list[GeoContentPiece]:
    """The §11.5 GEO seed inventory — ≥3 GeoContentPieces that enable S5.

    Each piece sits on a real ICP prompt (e.g. "best virtual school for gifted
    K-8"), carries the LOCKED gifted-school ``competitor_set`` (§7.3, INV-6),
    starts at the **0% baseline** (§7.1), and attaches a repeated-sampling note
    (§7.4). The bodies are STRUCTURED to the §7.2 citation levers (definition /
    faq / comparison_table), quotable by the over-cited authorities
    (``davidsongifted.org``, ``niche.com``) carried in ``citation_targets``.

    These are VALID seeds (good GEO content), NOT the §11.4 BLOCK demos: the prose
    is on-brand and grounded — no banned "fastest / the best / 4X speed /
    guaranteed / #1" patterns — and ``claims_text`` is empty, so each piece passes
    V-1/V-2 through the existing grounding gate. Fixed and deterministic;
    ``synthetic_seed`` provenance throughout (INV-1).
    """
    return [
        _geo_piece(
            int_id=0x6E0_0001,
            target_prompt="best virtual school for gifted K-8",
            structure=GeoStructure.DEFINITION,
            body=(
                "GT School is an online, mastery-based program for gifted K-8 students: "
                "learners advance once they have genuinely mastered the material rather "
                "than moving on a fixed calendar. It is built for profoundly gifted "
                "families who want a rigorous, self-paced K-8 path that adapts to a "
                "child who is ready to move ahead."
            ),
            citation_targets=["davidsongifted.org", "niche.com"],
            structured_data_note=(
                "Emit as schema.org/EducationalOrganization + a DefinedTerm for "
                "'mastery-based gifted K-8' so AI-search can quote the definition."
            ),
            validation_ref="vr-seed-geo-001",
        ),
        _geo_piece(
            int_id=0x6E0_0002,
            target_prompt="online school for profoundly gifted elementary",
            structure=GeoStructure.FAQ,
            body=(
                "Q: What makes GT School suited to profoundly gifted elementary "
                "students? A: It is mastery-based, so a child advances the moment they "
                "have learned the material instead of waiting out a grade level. "
                "Q: Is it accredited and parent-guided? A: GT School is an accredited "
                "online K-8 program; parents and educators guide enrollment, and the "
                "pacing is set by the student's demonstrated mastery, not their age."
            ),
            citation_targets=["davidsongifted.org", "hoagiesgifted.org"],
            structured_data_note=(
                "Emit as schema.org/FAQPage with one Question per Q/A pair so each "
                "answer is independently quotable by AI-search."
            ),
            validation_ref="vr-seed-geo-002",
        ),
        _geo_piece(
            int_id=0x6E0_0003,
            target_prompt="accredited gifted K-8 online program",
            structure=GeoStructure.COMPARISON_TABLE,
            body=(
                "How GT School compares for accredited, gifted K-8 online learning: "
                "Model — mastery-based progression (advance on demonstrated mastery). "
                "Grade band — K through 8, designed for gifted and profoundly gifted "
                "learners. Format — fully online, parent- and educator-guided. "
                "Each row is source-able against the program's published details; "
                "families should verify current accreditation directly with the school."
            ),
            citation_targets=["niche.com", "greatschools.org"],
            structured_data_note=(
                "Emit as an HTML <table> with a schema.org/Table description so the "
                "comparison rows surface as a structured answer in AI-search."
            ),
            validation_ref="vr-seed-geo-003",
        ),
    ]


# --------------------------------------------------------------------------- #
# §8.1 Creator-discovery seed inventory (S6, FR-3.8, OUT-4 / INV-6). AGGREGATE/
# SYNTHETIC by construction: every handle is an OBVIOUSLY-synthetic stand-in
# (`@gifted_parent_hub`, never a real or minor handle), `audience_segment` is the
# adults-only closed set, `data_mode` is `synthetic` (NEVER live_scrape), and
# `is_minor` is False on EVERY record (the schema rejects True anyway — §9 V-3).
# Fixed and deterministic; ids are fixed `UUID(int=...)` (never uuid4), provenance
# is `synthetic_seed` throughout (INV-1).
# --------------------------------------------------------------------------- #

# Base for creator-record ids — a fixed namespace so the ints can't collide with
# the GEO seed namespace (0x6E0_xxxx) above.
_CREATOR_ID_BASE = 0xC0FFEE_0000


def generate_creator_records() -> list[CreatorRecord]:
    """The §8.1 creator-discovery seed inventory — ≥5 aggregate/synthetic records.

    Every record is INV-6-safe by construction: an OBVIOUSLY-synthetic
    ``display_handle`` (a made-up brand-mention handle, never a real or minor
    handle), an ADULTS-ONLY ``audience_segment`` (``parents``/``educators``/
    ``general`` — no minor segment exists), ``data_mode = synthetic`` (NEVER
    ``live_scrape``), and ``is_minor = False`` on EVERY record (the schema's
    validator rejects ``True`` anyway — fail closed, §9 V-3). ``fit_score`` /
    ``authenticity_score`` carry a plausible 0–1 spread. Fixed, deterministic
    ids (``UUID(int=...)``); ``synthetic_seed`` provenance throughout (INV-1).
    """
    prov = _seed_provenance()
    # (handle, channel, segment, fit, authenticity, rationale)
    specs: tuple[
        tuple[str, Channel, AudienceSegment, float, float, str],
        ...,
    ] = (
        (
            "@gifted_parent_hub",
            Channel.INSTAGRAM,
            AudienceSegment.PARENTS,
            0.88,
            0.81,
            "Aggregate parent-community account; strong gifted-education topical fit.",
        ),
        (
            "@mastery_classroom_notes",
            Channel.TIKTOK,
            AudienceSegment.EDUCATORS,
            0.74,
            0.69,
            "Educator audience discussing mastery-based pacing; on-topic, mid authenticity.",
        ),
        (
            "@homeschool_k8_journeys",
            Channel.X,
            AudienceSegment.PARENTS,
            0.66,
            0.72,
            "Homeschool-parent niche; relevant to K-8 alternatives, moderate fit.",
        ),
        (
            "@edu_policy_digest",
            Channel.LINKEDIN,
            AudienceSegment.GENERAL,
            0.52,
            0.85,
            "General education-policy audience; broad reach, high authenticity signal.",
        ),
        (
            "@profoundly_gifted_families",
            Channel.BLOG,
            AudienceSegment.PARENTS,
            0.91,
            0.77,
            "Profoundly-gifted parent blog; very high topical fit for the ICP.",
        ),
        (
            "@stem_enrichment_review",
            Channel.INSTAGRAM,
            AudienceSegment.GENERAL,
            0.60,
            0.58,
            "Broad STEM-enrichment reviewer; adjacent fit, mid authenticity.",
        ),
    )
    # Aliased fields are passed by their wire (camelCase) alias — matching the
    # `_geo_piece` / `_candidate` convention above, so mypy reads the generated
    # init signature without a per-call `type: ignore`.
    return [
        CreatorRecord(
            id=UUID(int=_CREATOR_ID_BASE + i, version=4),
            displayHandle=handle,
            channel=channel,
            audienceSegment=segment,
            fitScore=fit,
            authenticityScore=authenticity,
            rationale=rationale,
            dataMode=CreatorDataMode.SYNTHETIC,
            isMinor=False,
            provenance=prov,
        )
        for i, (handle, channel, segment, fit, authenticity, rationale) in enumerate(specs)
    ]


# --------------------------------------------------------------------------- #
# §8.2 Sentiment seed inventory (S6, FR-3.10, OUT-5). PLACEHOLDER data: every
# `excerpt` is an INVENTED synthetic brand-mention string (no real-user PII,
# INV-1), `source_mode` is `placeholder` (NEVER live_feed), and `observed_at` is
# a fixed ISO string (no wall clock). Mixed polarity (positive/neutral/negative
# all represented) so the S6 sentiment surface has signal. Fixed, deterministic
# ids; `synthetic_seed` provenance throughout.
# --------------------------------------------------------------------------- #

_SENTIMENT_ID_BASE = 0x5E471_0000


def generate_sentiment_records() -> list[SentimentRecord]:
    """The §8.2 sentiment seed inventory — ≥6 PLACEHOLDER records, mixed polarity.

    Varied ``channel`` / ``topic`` with all three ``sentiment`` polarities
    represented. Every ``excerpt`` is an INVENTED synthetic brand-mention string
    (no real-user PII, INV-1), ``source_mode = placeholder`` (NEVER
    ``live_feed`` — the schema's closed enum makes a live feed unrepresentable,
    OUT-5), and ``observed_at`` is the fixed seed ISO timestamp (no wall clock).
    Fixed, deterministic ids; ``synthetic_seed`` provenance throughout (INV-1).
    """
    prov = _seed_provenance()
    # (channel, topic, sentiment, score, excerpt)
    specs: tuple[
        tuple[Channel, str, Sentiment, float | None, str],
        ...,
    ] = (
        (
            Channel.INSTAGRAM,
            "mastery-based pacing",
            Sentiment.POSITIVE,
            0.8,
            "Synthetic mention: a parent praises GT School's mastery-based pacing for their child.",
        ),
        (
            Channel.X,
            "gifted online options",
            Sentiment.POSITIVE,
            0.6,
            "Synthetic mention: a thread on gifted online options speaks well of GT School.",
        ),
        (
            Channel.LINKEDIN,
            "accreditation questions",
            Sentiment.NEUTRAL,
            0.0,
            "Synthetic mention: a commenter asks a neutral question about GT School accreditation.",
        ),
        (
            Channel.BLOG,
            "enrollment process",
            Sentiment.NEUTRAL,
            0.1,
            "Synthetic mention: a blog post describes the GT School enrollment steps neutrally.",
        ),
        (
            Channel.TIKTOK,
            "tuition and funding",
            Sentiment.NEGATIVE,
            -0.5,
            "Synthetic mention: a commenter worries that gifted-school tuition feels out of reach.",
        ),
        (
            Channel.INSTAGRAM,
            "waitlist timing",
            Sentiment.NEGATIVE,
            -0.3,
            "Synthetic mention: a parent expresses mild frustration about K-8 waitlist timing.",
        ),
    )
    # Aliased fields are passed by their wire (camelCase) alias — matching the
    # `_geo_piece` / `_candidate` convention above, so mypy reads the generated
    # init signature without a per-call `type: ignore`.
    return [
        SentimentRecord(
            id=UUID(int=_SENTIMENT_ID_BASE + i, version=4),
            channel=channel,
            topic=topic,
            sentiment=sentiment,
            score=score,
            excerpt=excerpt,
            sourceMode=SentimentSourceMode.PLACEHOLDER,
            observedAt=_SEED_TS,
            provenance=prov,
        )
        for i, (channel, topic, sentiment, score, excerpt) in enumerate(specs)
    ]


# --------------------------------------------------------------------------- #
# §4 Staged-pipeline seed (S6). ONE full piece flowing concept→image→video, the
# three records SHARING a single `pipeline_id`. The concept stage is REAL in v1
# (has concept/copy/validation, status=selected); image+video are PLACEHOLDER
# (OUT-1): each carries a synthetic non-empty `placeholder_uri` and a STRING
# `cost_estimate_ref` pointer at the TECH_STACK cost model — NEVER a numeric
# price (INV-11). The ref chain links image→concept and video→image. Fixed,
# deterministic ids; `synthetic_seed` provenance throughout (INV-1).
# --------------------------------------------------------------------------- #

# Fixed ids for the one seeded pipeline (deterministic — never uuid4).
_PIPELINE_ID = UUID(int=0x91E_0000, version=4)
_CONCEPT_ID = UUID(int=0x91E_0001, version=4)
_IMAGE_ID = UUID(int=0x91E_0002, version=4)
_VIDEO_ID = UUID(int=0x91E_0003, version=4)
# A source ContentCandidate this concept was promoted from (a §11.4 batch id ref).
_PIPELINE_SOURCE_CANDIDATE_ID = UUID(int=0x91E_0004, version=4)


def generate_content_pipeline() -> list[ConceptArtifact | ImageArtifact | VideoArtifact]:
    """The §4 staged-pipeline seed — ONE full concept→image→video chain.

    Returns the three stage artifacts in pipeline order ``[concept, image,
    video]``, all SHARING one ``pipeline_id``. The ``ConceptArtifact`` is REAL in
    v1 (``status=selected``, carrying ``concept`` / ``copy`` / ``validation``);
    the ``ImageArtifact`` and ``VideoArtifact`` are PLACEHOLDER (OUT-1) — each has
    a synthetic non-empty ``placeholder_uri`` and a STRING ``cost_estimate_ref``
    pointer at the TECH_STACK cost model, NEVER a numeric price (INV-11). The ref
    chain links ``image.concept_ref → concept.id`` and ``video.image_ref →
    image.id``. Fixed, deterministic ids; ``synthetic_seed`` provenance (INV-1).
    """
    prov = _seed_provenance()

    # The shared image brief, written once so the concept and image stages agree.
    image_brief = (
        "A bright, calm home-learning desk: a K-8 student mid-lesson with a parent nearby; "
        "warm, candid, no on-screen text and no minors' faces identifiable."
    )

    # Aliased fields are passed by their wire (camelCase) alias — matching the
    # `_geo_piece` / `_candidate` convention above, so mypy reads the generated
    # init signature without a per-call `type: ignore`.
    concept = ConceptArtifact(
        id=_CONCEPT_ID,
        pipelineId=_PIPELINE_ID,
        stage=ArtifactStage.CONCEPT,
        status=ArtifactStatus.SELECTED,
        costEstimateRef="tech_stack:cost_model#concept_llm",
        provenance=prov,
        sourceCandidateRef=_PIPELINE_SOURCE_CANDIDATE_ID,
        concept=(
            "Show a real mastery-based GT School day: a profoundly gifted K-8 learner "
            "advancing the moment they have mastered the material, parent alongside."
        ),
        copy=("Mastery-based gifted K-8. See how a GT School day actually fits your child's pace."),
        imageBrief=image_brief,
        validation="vr-seed-pipeline-001",
    )

    image = ImageArtifact(
        id=_IMAGE_ID,
        pipelineId=_PIPELINE_ID,
        stage=ArtifactStage.IMAGE,
        status=ArtifactStatus.PLACEHOLDER,
        costEstimateRef="tech_stack:cost_model#image_gen",
        provenance=prov,
        conceptRef=_CONCEPT_ID,
        imageBrief=image_brief,
        placeholderUri="placeholder://gtschool/seed/pipeline-001/image.png",
        liveAssetUri=None,
    )

    video = VideoArtifact(
        id=_VIDEO_ID,
        pipelineId=_PIPELINE_ID,
        stage=ArtifactStage.VIDEO,
        status=ArtifactStatus.PLACEHOLDER,
        costEstimateRef="tech_stack:cost_model#video_gen",
        provenance=prov,
        imageRef=_IMAGE_ID,
        videoScript=(
            "15s spot: a GT School day in three beats — a learner masters a concept, advances, "
            "and a parent watches the moment. Voiceover: mastery-based gifted K-8, at your "
            "child's real pace."
        ),
        durationSec=15.0,
        placeholderUri="placeholder://gtschool/seed/pipeline-001/video.mp4",
        liveAssetUri=None,
    )

    return [concept, image, video]

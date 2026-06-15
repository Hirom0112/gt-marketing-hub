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
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
)

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

# An epoch start for synthetic timestamps (UTC). Deterministic offsets only.
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


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


def _synthetic_email(rng: random.Random, surname: str) -> str:
    """A synthetic email in the @example.invalid sink — never a real address."""
    return f"{surname.lower()}.{rng.randint(100, 999)}@example.invalid"


def _synthetic_phone(rng: random.Random) -> str:
    """A phone in the NANP fictitious 555-01xx block — clearly not dialable."""
    return f"555-01{rng.randint(0, 99):02d}"


def _timestamp(rng: random.Random, max_days_ago: int) -> datetime:
    """A deterministic UTC timestamp within ``max_days_ago`` days of the epoch."""
    return _EPOCH - timedelta(days=rng.randint(0, max_days_ago), minutes=rng.randint(0, 1439))


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
    created = _timestamp(rng, max_days_ago=120)

    utm: dict[str, object] = {
        "utm_source": attribution_source,
        "utm_medium": rng.choice(("cpc", "email", "social", "organic")),
        "utm_campaign": rng.choice(_UTM_CAMPAIGNS),
        "click_id": f"clk_{rng.getrandbits(32):08x}",
    }

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
        crm_seam_status=rng.choices(list(SeamStatus), weights=[0.6, 0.3, 0.1], k=1)[0],
        work_queue_score=round(rng.uniform(0.0, 1.0), 4),
        created_at=created,
        updated_at=created + timedelta(days=rng.randint(0, 30)),
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
    return ds

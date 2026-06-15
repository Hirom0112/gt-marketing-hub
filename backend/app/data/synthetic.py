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
    created = _timestamp(rng, max_days_ago=120)

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

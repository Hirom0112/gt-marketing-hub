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

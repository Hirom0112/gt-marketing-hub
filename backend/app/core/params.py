"""Params loader + validation (ARCHITECTURE.md §8; CLAUDE.md INV-11).

The params file (`params/params.yaml`) is the single home for every magic
number — queue weights, TEFA amounts, eval thresholds, cost caps, latency
budgets, geo settings. Nothing in `core/`, `ai/`, or `adapters/` hardcodes a
tunable; each consumer reads it from here (INV-11). This module parses the
YAML into typed Pydantic v2 models — one nested model per top-level §8 block —
and validates it at load: a missing required key or a wrong type raises a
clear, typed error, so config drift fails the build (CLAUDE.md §4.1).

This module is part of the deterministic core and stays pure: it imports
nothing from `app.ai` or `app.adapters` (the core-purity test guards this).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Default location of the params file, relative to the repo root, when neither
# an explicit path nor the PARAMS_PATH env var is supplied.
_DEFAULT_PARAMS_PATH = Path("params/params.yaml")


class _StrictModel(BaseModel):
    """Base for every params block: reject unknown keys, validate strictly.

    `extra="forbid"` turns a stray/renamed key into a validation error rather
    than silently ignoring it, so the params file cannot drift away from the
    schema without the build failing (CLAUDE.md §4.1).
    """

    model_config = ConfigDict(extra="forbid")


class Recoverability(_StrictModel):
    """work_queue.recoverability sub-factors, each normalized to [0,1].

    ``stage_proximity_weight`` is the dominant sub-factor (A-23): how far a family
    got down the Interest→Tuition funnel before stalling is the primary "who can I
    save first" signal — the further they went, the more recoverable.
    """

    stall_recency_weight: float
    stage_proximity_weight: float
    responsiveness_weight: float
    # Normalizer for the responsiveness sub-factor (A-5): the aggregate
    # `community_profile.engagement_signals["email_opens"]` count is divided by
    # this to map into [0,1]. Aggregate only — no child-keyed signal (INV-6).
    responsiveness_email_opens_max: int


class WorkQueueValue(_StrictModel):
    """work_queue.value — per-child tuition × the family's child count (A-23).

    Every targeted family pays the same full GT-Anywhere tuition per child (Texas
    voucher = self-pay), so the only thing that varies value across families is how
    many children they enrolled (the Interest form's "How many children? 1–5+",
    funnel-map §4D). ``value = num_children × tuition_annual_default``;
    ``max_children`` (the "5+" cap) normalizes it via ``value_max`` so the value
    term stays in [0,1]. The old per-family ``funded_multiplier``/``variance`` hash
    jitter is GONE — value spread is now a real funnel signal, not noise.
    """

    tuition_annual_default: float
    max_children: int

    @model_validator(mode="after")
    def _max_children_valid(self) -> WorkQueueValue:
        if self.max_children < 1:
            raise ValueError(
                f"work_queue.value.max_children must be >= 1, got {self.max_children!r}"
            )
        return self


class WorkQueue(_StrictModel):
    """FR-2.5 work-queue scorer weights and sub-factors (§8).

    ``freshness_window_days``/``freshness_floor`` (S12) drive the
    ``recoverable_now`` freshness decay: freshness falls linearly from 1.0 at the
    stall anchor to ``freshness_floor`` once ``freshness_window_days`` have
    elapsed (never reaching 0, so a long-stalled family stays rankable).
    """

    w_recoverability: float
    w_value: float
    recoverability: Recoverability
    value: WorkQueueValue
    stall_window_days: int
    freshness_window_days: int
    freshness_floor: float


class ContactWindows(_StrictModel):
    """enrollment.contact — contact-recency color thresholds (S9 W1; INV-11).

    The single home for the recency deriver's day windows. Age is measured in
    whole days from a family's ``created_at``: an uncontacted family is *fresh*
    (grey) while ``age_days <= grey_window_days`` and *overdue* (red) once
    ``age_days >= overdue_days``. Read by ``core/contact_status.py`` — never
    hardcoded.
    """

    grey_window_days: int
    overdue_days: int


class Enrollment(_StrictModel):
    """S9 enrollment-depth tunables (§8). Currently the contact-recency windows."""

    contact: ContactWindows


class Bulk(_StrictModel):
    """S12 W2 bulk-action governance (A-20; INV-8 — the per-run cap + kill).

    ``nudge_per_run_cap`` is the hard ceiling on how many families a single
    bulk-nudge run may SEND: an eval-passing family beyond the cap is deferred to
    ``capped`` rather than overspending the metered edge (INV-8). The single
    canonical home for the bulk cap — never a code literal.
    """

    nudge_per_run_cap: int


class BackToSchool(_StrictModel):
    """S12 W2 back-to-school volume cohort shape (A-21; INV-1/INV-11).

    The SEPARATE deterministic synthetic cohort (NOT the default June world): a
    surge of ``count`` active stalls with a single-day Aug spike. Every value is
    a tunable home (INV-11); the cohort is drawn from its own ``seed`` so the
    default stream stays byte-identical (the determinism guard holds). ``spike_*``
    anchor the spike day, ``spike_share`` is the fraction of the cohort whose
    ``stalled_since`` lands on that day, and ``spread_days`` bands the rest.
    """

    count: int
    seed: int
    spike_year: int
    spike_month: int
    spike_day: int
    spike_share: float
    spread_days: int


class SecondaryBump(_StrictModel):
    """A single within-month inquiry bump in the realistic cohort (a one-day cluster)."""

    year: int
    month: int
    day: int
    count: int


class Realistic(_StrictModel):
    """The realistic-cadence synthetic cohort shape (INV-1/INV-11).

    A SEPARATE deterministic cohort calibrated to GT's measured top-of-funnel
    cadence (aggregate-only): ``total`` inquiries spread across ``monthly_counts``
    within the ``[window_start, window_end]`` inquiry window, with one campaign
    spike (``spike_count`` on the ``spike_*`` day) and optional ``secondary_bumps``.
    A recent ``active_count`` slice are UNRESOLVED stalls whose ``stalled_since``
    falls in the last ``active_window_days`` (the ACTIVE board), of which
    ``dismissed_count`` are set aside via a logged dismiss event; the rest of the
    cohort is HISTORY (derives RECOVERED). Drawn from its own ``seed`` so the
    default + back_to_school streams stay byte-identical.
    """

    total: int
    seed: int
    window_start_year: int
    window_start_month: int
    window_start_day: int
    window_end_year: int
    window_end_month: int
    window_end_day: int
    # Year-month string ("2026-01") → inquiry count. Keys are validated as data;
    # the sum is asserted against ``total`` so the calibration can't silently drift.
    monthly_counts: dict[str, int]
    spike_year: int
    spike_month: int
    spike_day: int
    spike_count: int
    secondary_bumps: list[SecondaryBump]
    active_count: int
    active_window_days: int
    dismissed_count: int

    @model_validator(mode="after")
    def _shape_is_consistent(self) -> Realistic:
        month_total = sum(self.monthly_counts.values())
        if month_total != self.total:
            raise ValueError(
                f"realistic.monthly_counts must sum to total {self.total}, got {month_total}"
            )
        if self.spike_count > self.total:
            raise ValueError("realistic.spike_count cannot exceed total")
        if self.dismissed_count > self.active_count:
            raise ValueError("realistic.dismissed_count cannot exceed active_count")
        if self.active_count > self.total:
            raise ValueError("realistic.active_count cannot exceed total")
        if self.active_window_days <= 0:
            raise ValueError("realistic.active_window_days must be positive")
        return self


class AwardAmounts(_StrictModel):
    """funding.award_amounts — TEFA tiers, $/yr (RESEARCH.md Q1)."""

    tefa_standard: float
    tefa_disability: float
    tefa_homeschool: float


class Funding(_StrictModel):
    """FR-2.7 funding amounts + installment split + tuition-unlock gate (§8)."""

    award_amounts: AwardAmounts
    installment_split: list[float]
    tuition_unlock_state: str

    @field_validator("installment_split")
    @classmethod
    def _split_sums_to_one(cls, value: list[float]) -> list[float]:
        """The installment split must partition the award exactly (FR-2.7).

        A split that does not sum to 1.0 would leave the award over- or
        under-disbursed; drift here fails the build (CLAUDE.md INV-11, §4.1).
        A tiny float-epsilon tolerance absorbs YAML decimal representation only.
        """
        total = sum(value)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"funding.installment_split must sum to 1.0, got {total!r} ({value!r})"
            )
        return value


class NudgeTrigger(_StrictModel):
    """FR-4.1 nudge classifier thresholds."""

    min_precision: float
    min_recall: float


class DocExtraction(_StrictModel):
    """FR-4.2 doc-extraction accuracy threshold."""

    min_accuracy: float


class MessageSafetyGrounding(_StrictModel):
    """FR-4.3 grounding/safety gate — gates §5.2 and §5.3.

    Two DISTINCT thresholds, one canonical home each (INV-11):

    * ``min_grounding`` is the V-2 grounding floor — an unverifiable "4X/2X"
      performance claim must be BLOCKED at this bar; it is never lowered (INV-4).
    * ``min_brand_score`` is the V-4 brand-voice bar — the LLM brand judge scores
      genuinely on-brand copy around 0.85, so V-4 is decoupled from the V-2 floor
      (reusing 0.95 wrongly blocked legitimate on-brand generation). REQUIRED, so
      config drift fails the build (CLAUDE.md §4.1).
    """

    min_grounding: float
    min_brand_score: float
    max_unverifiable_claims: int
    require_coppa_safe: bool


class CloseTips(_StrictModel):
    """S9 W5 "how to close" tips — grounded in app_form.extracted_fields (FR-4.3).

    Same shape as :class:`MessageSafetyGrounding`: the close-tips proposal crosses
    the SAME canonical grounding gate (A-10), so a hallucinated fact (a tip absent
    from ``extracted_fields``) fails V-2 and is BLOCKED, not softened (INV-4). The
    golden-set eval is gated by ``min_grounding`` (INV-3).
    """

    min_grounding: float
    max_unverifiable_claims: int
    require_coppa_safe: bool


class GeoTracking(_StrictModel):
    """FR-4.4 GEO repeated-sampling thresholds."""

    min_samples_per_prompt: int
    report_variance: bool


class EvalThresholds(_StrictModel):
    """FR-4.x eval thresholds; an action below threshold is BLOCKED/disabled (§8)."""

    nudge_trigger: NudgeTrigger
    doc_extraction: DocExtraction
    message_safety_grounding: MessageSafetyGrounding
    close_tips: CloseTips
    geo_tracking: GeoTracking


class CostCaps(_StrictModel):
    """NFR-5 hard per-run caps; exceeding ⇒ deterministic/placeholder (§9)."""

    anthropic_per_run_usd: float
    media_gen_per_run_usd: float


class ModelPricing(_StrictModel):
    """Per-model token rates, $/MTok (TECH_STACK §6.1).

    Pure data — the canonical per-million-token input/output prices. The actual
    token→USD computation lives in the AI layer (`app/ai/pricing.py`), NOT here:
    `core/` stays pure (no logic/IO), it just owns the rates so they have exactly
    one home (INV-11).
    """

    input_per_mtok: float
    output_per_mtok: float


class AnthropicPricing(_StrictModel):
    """TECH_STACK §6.1 token pricing — per-model $/MTok rates keyed by model id.

    The single canonical home for the live cost model (INV-11): the AI layer's
    pricing helper reads these to convert reported tokens into the real USD the
    per-run/daily caps charge, never a code literal. Keys are the model ids that
    match ``ANTHROPIC_MODEL_*`` (§5.3); an id absent here is a config gap and the
    helper fails loud rather than charging $0.
    """

    models: dict[str, ModelPricing]


class LatencyBudgetMs(_StrictModel):
    """NFR-9 latency budgets, milliseconds."""

    ai_proposal: int


class Geo(_StrictModel):
    """FR-3.7 GEO prompt-set + cadence + 0% baseline + generate-to-win lift (§8)."""

    prompt_set_size: int
    cadence: str
    baseline_coverage: float
    # generate-to-win flywheel (FR-3.7): the GT cite-likelihood buckets (of 256)
    # the simulated engine uses for a PROMPT THAT HAS BEEN WON (a GEO piece was
    # generated, gate-passed, and published). The single canonical home for the
    # lift amount (INV-11); the simulated adapter reads it, never a code literal.
    published_cite_buckets: int


class BrandMemory(_StrictModel):
    """FR-3.2 brand-memory conditioning loop tunables (CONTENT_SPEC §8.3.2).

    `weight_step` is the affirm/discard weight delta — keeping a candidate adds
    it to the conditioning weight, discarding subtracts it. It is the canonical
    home for the value the brand store currently defaults in code
    (`SqliteBrandMemoryStore`'s `_DEFAULT_WEIGHT_STEP`), closing that INV-11 gap.
    """

    weight_step: float


class LibraryIngestNormalization(_StrictModel):
    """library_ingest.normalization — per-platform engagement caps (INV-11).

    Scraped engagement is NOT comparable across platforms: X / YouTube carry a
    `views_plays` count in the tens-of-thousands while Instagram / Facebook /
    TikTok carry a `likes` count in the low hundreds. The distill + loader
    normalize each post's raw engagement by its platform cap into [0,1], so
    `weight` ranks WITHIN a platform (an X view and an IG like are never
    compared directly). Each cap divides that platform's raw signal; the result
    is clamped to [0,1]. The canonical home for those caps — never a code
    literal.
    """

    instagram_likes_max: int
    facebook_likes_max: int
    tiktok_likes_max: int
    x_views_max: int
    youtube_views_max: int


class LibraryIngest(_StrictModel):
    """Scraper-library ingest tunables (Phase-1 marketing; INV-11).

    The distilled `brand_library.json` (GT's OWN proven public marketing) seeds
    brand memory, GEO prompts, and the content library. `top_n_per_theme` caps
    how many exemplars the distill keeps per INSIGHTS theme so the seed stays
    small and deterministic; `normalization` holds the per-platform engagement
    caps that map raw engagement into a comparable [0,1] `weight`. The library
    ROOT path is NOT a param — it is the `GT_LIBRARY_PATH` env var (TECH_STACK
    §5); the committed in-repo JSON path is a fixed code constant, not a tunable.
    """

    top_n_per_theme: int
    normalization: LibraryIngestNormalization

    @model_validator(mode="after")
    def _top_n_positive(self) -> LibraryIngest:
        if self.top_n_per_theme < 1:
            raise ValueError(
                f"library_ingest.top_n_per_theme must be >= 1, got {self.top_n_per_theme!r}"
            )
        return self


class CreatorScoringFit(_StrictModel):
    """FR-3.8 creator-discovery fit sub-weights (CONTENT_SPEC §8.1).

    Each weight multiplies a [0,1] sub-factor; together they form the fit
    score. They MUST partition to 1.0 so the consumer can trust the fit score
    stays in [0,1] — a drifted set fails to load (INV-11, §4.1).
    """

    topic_match_weight: float
    audience_match_weight: float
    brand_alignment_weight: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> CreatorScoringFit:
        total = self.topic_match_weight + self.audience_match_weight + self.brand_alignment_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"creator_scoring.fit weights must sum to 1.0, got {total!r}")
        return self


class CreatorScoringAuthenticity(_StrictModel):
    """FR-3.8 creator-discovery authenticity sub-weights (CONTENT_SPEC §8.1).

    Sub-weights over [0,1] sub-factors; MUST sum to 1.0. The consumer applies
    `spam_signal_weight` as a penalty (higher spam signal LOWERS authenticity).
    """

    follower_authenticity_weight: float
    engagement_consistency_weight: float
    spam_signal_weight: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> CreatorScoringAuthenticity:
        total = (
            self.follower_authenticity_weight
            + self.engagement_consistency_weight
            + self.spam_signal_weight
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"creator_scoring.authenticity weights must sum to 1.0, got {total!r}")
        return self


class CreatorScoring(_StrictModel):
    """FR-3.8 creator-discovery fit + authenticity + surface threshold."""

    fit: CreatorScoringFit
    authenticity: CreatorScoringAuthenticity
    # Minimum fit score for a creator to surface in the discovery list.
    surface_threshold: float


class KpiLever(_StrictModel):
    """FR-3.11 per-channel KPI lever: baseline (current) + target.

    `lever delta = metric - baseline`; the consumer rolls these up vs target.
    """

    baseline: float
    target: float


class Kpi(_StrictModel):
    """FR-3.11 per-channel KPI rollup vs baseline/target."""

    # Channel-name keyed (keys are the `Channel` tokens).
    levers: dict[str, KpiLever]


class Scheduler(_StrictModel):
    """FR-3.6 / OUT-2 content scheduler — dispatch is SIMULATED in v1.

    The publish-monitor slice fans one publish request out to a subset of
    ``publish_channels`` (a subset of the LOCKED ``Channel`` enum, CONTENT_SPEC
    §2.1 — social-publishable feeds only). ``daily_caps`` is the per-platform max
    SIMULATED dispatches/day; an over-cap channel is forced ``blocked`` (INV-8
    governance posture). Both live here as the one canonical home (INV-11).
    """

    # Never 'live' in v1 (INV-9, OUT-2): the field is typed shut to simulated.
    dispatch_mode: str
    # Social-publishable channel tokens the fan-out may target (subset of Channel).
    publish_channels: list[str]
    # Per-platform-token max simulated dispatches/day (quota guard; over-cap ⇒ blocked).
    daily_caps: dict[str, int]

    @field_validator("dispatch_mode")
    @classmethod
    def _dispatch_is_simulated(cls, value: str) -> str:
        if value != "simulated":
            raise ValueError(
                f"scheduler.dispatch_mode must be 'simulated' in v1, got {value!r} (INV-9, OUT-2)"
            )
        return value

    @model_validator(mode="after")
    def _caps_cover_channels(self) -> Scheduler:
        if not self.publish_channels:
            raise ValueError("scheduler.publish_channels must be non-empty")
        missing = [c for c in self.publish_channels if c not in self.daily_caps]
        if missing:
            raise ValueError(
                f"scheduler.daily_caps must define a cap for every publish channel; "
                f"missing {missing!r}"
            )
        bad = {k: v for k, v in self.daily_caps.items() if v < 1}
        if bad:
            raise ValueError(f"scheduler.daily_caps values must be >= 1, got {bad!r}")
        return self


class CrmGtProperties(_StrictModel):
    """crm.gt_properties — the gt_* custom HubSpot property internal names (S10).

    Provisioned by ``scripts/provision_hubspot.py`` and read by the live adapter
    so a property name lives in exactly one place (INV-11): the adapter never
    hardcodes ``gt_synthetic_id`` et al.

    ``social_post`` are the gt_* properties on the **GT Social Post** custom
    object (publish-monitor W3): the second-screen mirror of each dispatched
    social post. Same INV-11 posture — the mirror adapter reads these names,
    never a code literal.
    """

    deal: list[str]
    contact: list[str]
    social_post: list[str]


class CrmSocialPostObject(_StrictModel):
    """crm.gt_social_post_object — the GT Social Post custom object config (W3).

    The publish-monitor mirror upserts one custom-object record per dispatched
    social post so the team can monitor publishing on the HubSpot screen too. Per
    INV-11 the object's API identifiers live here (provisioned by
    ``scripts/provision_hubspot.py``), never hardcoded in the adapter:

    - ``object_type`` is the CRM v3 object identifier the adapter puts in the URL
      path (``/crm/v3/objects/{object_type}``) — HubSpot accepts either the
      ``fullyQualifiedName`` (``p<portal>_gt_social_post``) or the object type id
      (``2-XXXXXXX``). A placeholder ships in the example; the live params.yaml
      carries the provisioned value.
    - ``id_property`` is the idempotency upsert key on the object — the
      ``gt_synthetic_id`` analogue keyed on ``str(post_id)`` (NEVER any contact
      identity; INV-1).
    """

    object_type: str
    id_property: str


class Crm(_StrictModel):
    """S10 HubSpot CRM seam config (ANALYSIS/hubspot-complement-plan.md §4).

    Every value the live adapter and the provisioning script need that is not
    code: the cockpit-stage ↔ HubSpot-stage-id map (portable to GT's real
    pipeline by re-provisioning), the synthetic write-lock allowlist/denylist
    (guard 1, INV-1), and the gt_* property names (INV-11).
    """

    # Cockpit Stage enum value → HubSpot deal stage id. The live adapter looks a
    # stage up here; a missing stage must fail closed (INV-4 posture) — enforced
    # in the pure mapping helper, not silently defaulted.
    stage_map: dict[str, str]
    synthetic_email_domains: list[str]
    real_domain_denylist: list[str]
    gt_properties: CrmGtProperties
    gt_social_post_object: CrmSocialPostObject

    @model_validator(mode="after")
    def _social_post_id_property_declared(self) -> Crm:
        """The custom object's upsert key MUST be in the social_post prop list.

        Keeps the two homes consistent (INV-11): the idempotency key the mirror
        upserts on (``gt_social_post_object.id_property``) has to be a property the
        provisioner declares on ``gt_properties.social_post``, else a drift would
        let the adapter key on a property that was never created.
        """
        if self.gt_social_post_object.id_property not in self.gt_properties.social_post:
            raise ValueError(
                "crm.gt_social_post_object.id_property "
                f"{self.gt_social_post_object.id_property!r} must appear in "
                f"crm.gt_properties.social_post {self.gt_properties.social_post!r}"
            )
        return self


class Params(_StrictModel):
    """Typed view of the whole params file — one field per §8 top-level block."""

    work_queue: WorkQueue
    enrollment: Enrollment
    bulk: Bulk
    back_to_school: BackToSchool
    realistic: Realistic
    funding: Funding
    eval_thresholds: EvalThresholds
    cost_caps: CostCaps
    anthropic_pricing: AnthropicPricing
    latency_budget_ms: LatencyBudgetMs
    geo: Geo
    brand_memory: BrandMemory
    library_ingest: LibraryIngest
    creator_scoring: CreatorScoring
    kpi: Kpi
    scheduler: Scheduler
    crm: Crm


def _resolve_path(path: Path | None) -> Path:
    """Resolve which params file to load.

    Precedence: explicit `path` arg → `PARAMS_PATH` env var → the default
    `params/params.yaml`.
    """
    if path is not None:
        return path
    env_path = os.environ.get("PARAMS_PATH")
    if env_path:
        return Path(env_path)
    return _DEFAULT_PARAMS_PATH


def load_params(path: Path | None = None) -> Params:
    """Load and validate the params file into typed Pydantic models.

    Args:
        path: Explicit params file to load. If omitted, falls back to the
            `PARAMS_PATH` env var, then to `params/params.yaml`.

    Returns:
        A validated `Params` object exposing every §8 block as typed fields.

    Raises:
        FileNotFoundError: if the resolved path does not exist.
        pydantic.ValidationError: if a required key is missing, an unknown key
            is present, or a value has the wrong type (drift fails the build).
    """
    resolved = _resolve_path(path)
    raw: Any = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return Params.model_validate(raw)

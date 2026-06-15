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
    """work_queue.recoverability sub-factors, each normalized to [0,1]."""

    stall_recency_weight: float
    stage_proximity_weight: float
    responsiveness_weight: float
    # Normalizer for the responsiveness sub-factor (A-5): the aggregate
    # `community_profile.engagement_signals["email_opens"]` count is divided by
    # this to map into [0,1]. Aggregate only — no child-keyed signal (INV-6).
    responsiveness_email_opens_max: int


class WorkQueueValue(_StrictModel):
    """work_queue.value baseline + funded weighting + S12 per-family variance band.

    ``variance_min``/``variance_max`` bound the deterministic per-family value
    multiplier (S12; A-19 ranking spread): a stable hash of ``family_id`` maps
    into ``[variance_min, variance_max]``. This affects ONLY the new
    ``recoverable_now`` ranking path — the canonical ``value()``/``score_family``
    (and the TEFA worked targets) are untouched. The band must be valid
    (``0 < min <= max``) so the multiplier stays positive and ordered.
    """

    tuition_annual_default: float
    funded_multiplier: float
    variance_min: float
    variance_max: float

    @model_validator(mode="after")
    def _variance_band_valid(self) -> WorkQueueValue:
        if not (0.0 < self.variance_min <= self.variance_max):
            raise ValueError(
                "work_queue.value variance band must satisfy 0 < variance_min <= "
                f"variance_max, got [{self.variance_min!r}, {self.variance_max!r}]"
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
    """FR-4.3 grounding/safety gate — gates §5.2 and §5.3."""

    min_grounding: float
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


class LatencyBudgetMs(_StrictModel):
    """NFR-9 latency budgets, milliseconds."""

    ai_proposal: int


class Geo(_StrictModel):
    """FR-3.7 GEO prompt-set + cadence + 0% baseline (§8)."""

    prompt_set_size: int
    cadence: str
    baseline_coverage: float


class BrandMemory(_StrictModel):
    """FR-3.2 brand-memory conditioning loop tunables (CONTENT_SPEC §8.3.2).

    `weight_step` is the affirm/discard weight delta — keeping a candidate adds
    it to the conditioning weight, discarding subtracts it. It is the canonical
    home for the value the brand store currently defaults in code
    (`SqliteBrandMemoryStore`'s `_DEFAULT_WEIGHT_STEP`), closing that INV-11 gap.
    """

    weight_step: float


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
    """FR-3.6 / OUT-2 content scheduler — dispatch is SIMULATED in v1."""

    # Never 'live' in v1 (INV-9, OUT-2): the field is typed shut to simulated.
    dispatch_mode: str

    @field_validator("dispatch_mode")
    @classmethod
    def _dispatch_is_simulated(cls, value: str) -> str:
        if value != "simulated":
            raise ValueError(
                f"scheduler.dispatch_mode must be 'simulated' in v1, got {value!r} (INV-9, OUT-2)"
            )
        return value


class CrmGtProperties(_StrictModel):
    """crm.gt_properties — the gt_* custom HubSpot property internal names (S10).

    Provisioned by ``scripts/provision_hubspot.py`` and read by the live adapter
    so a property name lives in exactly one place (INV-11): the adapter never
    hardcodes ``gt_synthetic_id`` et al.
    """

    deal: list[str]
    contact: list[str]


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


class Params(_StrictModel):
    """Typed view of the whole params file — one field per §8 top-level block."""

    work_queue: WorkQueue
    enrollment: Enrollment
    funding: Funding
    eval_thresholds: EvalThresholds
    cost_caps: CostCaps
    latency_budget_ms: LatencyBudgetMs
    geo: Geo
    brand_memory: BrandMemory
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

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
from pydantic import BaseModel, ConfigDict, field_validator

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
    """work_queue.value baseline + funded weighting."""

    tuition_annual_default: float
    funded_multiplier: float


class WorkQueue(_StrictModel):
    """FR-2.5 work-queue scorer weights and sub-factors (§8)."""

    w_recoverability: float
    w_value: float
    recoverability: Recoverability
    value: WorkQueueValue
    stall_window_days: int


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


class GeoTracking(_StrictModel):
    """FR-4.4 GEO repeated-sampling thresholds."""

    min_samples_per_prompt: int
    report_variance: bool


class EvalThresholds(_StrictModel):
    """FR-4.x eval thresholds; an action below threshold is BLOCKED/disabled (§8)."""

    nudge_trigger: NudgeTrigger
    doc_extraction: DocExtraction
    message_safety_grounding: MessageSafetyGrounding
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


class Params(_StrictModel):
    """Typed view of the whole params file — one field per §8 top-level block."""

    work_queue: WorkQueue
    funding: Funding
    eval_thresholds: EvalThresholds
    cost_caps: CostCaps
    latency_budget_ms: LatencyBudgetMs
    geo: Geo
    brand_memory: BrandMemory


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

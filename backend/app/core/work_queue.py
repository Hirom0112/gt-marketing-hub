"""Deterministic work-queue scorer — the headline FR-2.5 ranking unit (§5.1).

Given a family's queue-relevant attributes, this module computes a single
recoverability/value score and ranks a cohort by it:

    score = w_recoverability · recoverability + w_value · (value / value_max)

Both terms live in [0,1]. `recoverability` composes three normalized sub-factors
weighted per §8 — how recently the family stalled (relative to the stall
window), how close it sits to the Tuition finish line, and how responsive it is.
`value` is the tuition baseline scaled by the applicable funded multiplier,
normalized by `value_max` so the value term also stays in [0,1] (ASSUMPTIONS.md
A-1). Every weight, baseline, and window is read from the typed params (§8) —
nothing here is hardcoded (CLAUDE.md INV-11).

This is part of the deterministic core and stays pure: a function of the typed
inputs + params alone, with no LLM, no adapter, and no DB access. It imports
nothing from `app.ai` or `app.adapters` (the core-purity test guards this);
persistence of `family_record.work_queue_score` is wired up in a later slice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.params import Params
from app.data.models import FundingType, Stage

# §4.8 funnel order, Interest → Tuition. Stage proximity normalizes a family's
# position along this path to [0,1]; closer to Tuition = more recoverable (§5.1).
# Derived from the Stage enum, not a hardcoded magnitude (INV-11).
_STAGE_ORDER: tuple[Stage, ...] = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION)

# Multiplier for a self-pay / unfunded family: the tuition baseline at face
# value (no funded weighting). Structural identity, not a tunable (the funded
# weighting itself lives in params — work_queue.value.funded_multiplier).
_SELF_PAY_MULTIPLIER = 1.0


class WorkQueueFamily(BaseModel):
    """The queue-relevant attributes the scorer reads — its pure input (§5.1).

    A projection of `family_record` (§4.1) down to exactly what FR-2.5 scores:
    funnel position, stall recency, responsiveness, and funding tier. Frozen so
    a scored family cannot mutate mid-ranking; `family_id` is the stable
    tiebreak key.
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    current_stage: Stage
    stalled_since: datetime | None = None
    # A normalized engagement signal in [0,1] (aggregate only — P-4 / INV-6);
    # clamped defensively at scoring time.
    responsiveness: float = 0.0
    funding_type: FundingType | None = None


def _clamp01(value: float) -> float:
    """Clamp a value into [0,1] so every sub-factor stays normalized (§5.1)."""
    return max(0.0, min(1.0, value))


def _now_or(now: datetime | None) -> datetime:
    """Reference time for the stall-recency window; injectable for determinism."""
    return now if now is not None else datetime.now(UTC)


def _stall_recency(family: WorkQueueFamily, params: Params, *, now: datetime) -> float:
    """Stall-recency sub-factor ∈ [0,1] — fresher stalls are more recoverable.

    Measured relative to ``work_queue.stall_window_days`` (§8): a family stalled
    today scores 1.0, one stalled a full window (or longer) ago scores 0.0, and
    a family that has never stalled is treated as fully recoverable (1.0 — the
    absence of a stall is not a penalty).
    """
    if family.stalled_since is None:
        return 1.0
    window_days = params.work_queue.stall_window_days
    elapsed_days = (now - family.stalled_since) / timedelta(days=1)
    return _clamp01(1.0 - elapsed_days / window_days)


def _stage_proximity(family: WorkQueueFamily) -> float:
    """Stage-proximity sub-factor ∈ [0,1] — closer to Tuition ⇒ higher (§5.1).

    The family's index along the §4.8 funnel order, normalized by the number of
    transitions (Interest → 0.0, Tuition → 1.0).
    """
    return _STAGE_ORDER.index(family.current_stage) / (len(_STAGE_ORDER) - 1)


def recoverability(
    family: WorkQueueFamily, params: Params, *, now: datetime | None = None
) -> float:
    """Composite recoverability ∈ [0,1] — the weighted sub-factor blend (§8).

    Sums the three normalized sub-factors (stall-recency, stage-proximity,
    responsiveness), each scaled by its §8 weight. The weights sum to 1.0, so a
    family that maxes every sub-factor scores exactly 1.0 and one that floors
    them scores 0.0 — keeping the term in [0,1] (INV-11: weights from params).

    Args:
        family: The queue-relevant family attributes.
        params: Loaded params (§8); supplies the recoverability sub-weights and
            the stall window.
        now: Reference time for the stall-recency window; defaults to UTC now.

    Returns:
        The recoverability score in [0,1].
    """
    reference = _now_or(now)
    sub = params.work_queue.recoverability
    return (
        sub.stall_recency_weight * _stall_recency(family, params, now=reference)
        + sub.stage_proximity_weight * _stage_proximity(family)
        + sub.responsiveness_weight * _clamp01(family.responsiveness)
    )


def _is_funded(funding_type: FundingType | None) -> bool:
    """True when the family carries a TEFA award (any tier), not self-pay (§4.8)."""
    return funding_type is not None and funding_type is not FundingType.SELF_PAY


def value(family: WorkQueueFamily, params: Params) -> float:
    """Raw queue value — tuition baseline × the applicable funded multiplier (§8).

    Funded (any TEFA tier) families take ``work_queue.value.funded_multiplier``;
    self-pay / no-funding families take the 1.0 baseline. Both inputs are params
    (INV-11). Normalized by :func:`value_max` at scoring time so the value term
    stays in [0,1] (A-1).
    """
    value_cfg = params.work_queue.value
    multiplier = (
        value_cfg.funded_multiplier if _is_funded(family.funding_type) else _SELF_PAY_MULTIPLIER
    )
    return value_cfg.tuition_annual_default * multiplier


def value_max(params: Params) -> float:
    """The value normalizer (A-1): baseline × the max applicable funded multiplier.

    Derived deterministically from existing §8 params — no new magic number.
    Dividing :func:`value` by this keeps the value term in [0,1] regardless of
    funding tier (the funded path can never exceed the cap).
    """
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * max(_SELF_PAY_MULTIPLIER, value_cfg.funded_multiplier)


def score_family(family: WorkQueueFamily, params: Params, *, now: datetime | None = None) -> float:
    """Score one family for the work queue (FR-2.5; §5.1, A-1).

    ``score = w_recoverability · recoverability + w_value · (value / value_max)``,
    with both terms in [0,1] and every weight read from §8 params (INV-11).
    Conceptually this is the value written to ``family_record.work_queue_score``;
    persistence/wiring lives in a later slice.

    Args:
        family: The queue-relevant family attributes.
        params: Loaded params (§8); supplies the headline weights, sub-weights,
            value baseline/multiplier, and stall window.
        now: Reference time for the stall-recency window; defaults to UTC now.

    Returns:
        The work-queue score in [0,1].
    """
    work_queue = params.work_queue
    recover_term = recoverability(family, params, now=now)
    value_term = value(family, params) / value_max(params)
    return work_queue.w_recoverability * recover_term + work_queue.w_value * value_term


def rank_families(
    families: list[WorkQueueFamily],
    params: Params,
    *,
    now: datetime | None = None,
) -> list[WorkQueueFamily]:
    """Rank a cohort by work-queue score, highest first (FR-2.5; §5.1).

    Deterministic: families are ordered by descending score, and ties are broken
    by ascending ``family_id`` so the order is total and never wobbles between
    runs. Pinning ``now`` makes the whole ranking reproducible under test.

    Args:
        families: The cohort to rank (not mutated; a new list is returned).
        params: Loaded params (§8).
        now: Reference time for the stall-recency window; defaults to UTC now.

    Returns:
        A new list of the families ordered for the queue.
    """
    reference = _now_or(now)
    return sorted(
        families,
        key=lambda family: (-score_family(family, params, now=reference), family.family_id),
    )

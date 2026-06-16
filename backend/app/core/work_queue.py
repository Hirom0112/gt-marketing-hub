"""Deterministic work-queue scorer — the headline FR-2.5 ranking unit (§5.1).

Given a family's queue-relevant attributes, this module computes a single
recoverability/value score and ranks a cohort by it:

    score = w_recoverability · recoverability + w_value · (value / value_max)

Both terms live in [0,1]. `recoverability` composes three normalized sub-factors
weighted per §8 — how recently the family stalled (relative to the stall
window), how close it sits to the Tuition finish line (the DOMINANT sub-factor —
funnel depth, A-23), and how responsive it is. `value` is `num_children ×
per-child tuition` (A-23 — every targeted family is full-pay, so value varies
only by child count), normalized by `value_max` so the value term also stays in
[0,1] (ASSUMPTIONS.md A-1). Every weight, baseline, and window is read from the
typed params (§8) — nothing here is hardcoded (CLAUDE.md INV-11).

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
    # The spine creation instant — the freshness fallback anchor when a family
    # carries no explicit ``stalled_since`` (S12). Optional so the existing
    # scorer fixtures (which never set it) keep constructing unchanged.
    created_at: datetime | None = None
    # A normalized engagement signal in [0,1] (aggregate only — P-4 / INV-6);
    # clamped defensively at scoring time.
    responsiveness: float = 0.0
    # The Interest form's child count (A-23) — the sole driver of value spread:
    # value = num_children × per-child tuition. Defaults to 1 (a family is always
    # for ≥1 child) so existing fixtures construct unchanged; clamped ≥1 at scoring.
    num_children: int = 1
    # Funding tier — informational only now (every targeted family is full-pay, so
    # it no longer scales value, A-23). Retained for display / cohort filtering.
    funding_type: FundingType | None = None


def _clamp01(value: float) -> float:
    """Clamp a value into [0,1] so every sub-factor stays normalized (§5.1)."""
    return max(0.0, min(1.0, value))


def responsiveness_from_engagement(engagement_signals: dict[str, object], params: Params) -> float:
    """Derive the [0,1] responsiveness sub-factor from aggregate engagement (A-5).

    The spine `FamilyRecord` does not carry a normalized responsiveness; it lives
    in the joined ``community_profile.engagement_signals`` dict as an integer
    ``email_opens`` count (aggregate only — P-4 / INV-6). This normalizes that
    count into [0,1] by dividing by ``work_queue.recoverability.
    responsiveness_email_opens_max`` (params — INV-11, no magic number) and
    clamping. A missing, empty, or non-numeric signal yields ``0.0`` (no
    engagement evidence ⇒ no responsiveness credit), never an error.

    Args:
        engagement_signals: The ``community_profile.engagement_signals`` dict
            (aggregate counts). May be empty / missing the key.
        params: Loaded params (§8); supplies the email-opens normalizer.

    Returns:
        The responsiveness sub-factor in [0,1].
    """
    raw = engagement_signals.get("email_opens", 0)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return 0.0
    cap = params.work_queue.recoverability.responsiveness_email_opens_max
    return _clamp01(raw / cap)


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


def _child_count(family: WorkQueueFamily) -> int:
    """The family's child count for value, clamped to ``[1, ∞)`` (A-23).

    A family always enrolls ≥1 child; a stray non-positive count would zero out
    value and drop the row off the board, so it floors at 1.
    """
    return max(1, family.num_children)


def value(family: WorkQueueFamily, params: Params) -> float:
    """Raw queue value — per-child tuition × the family's child count (A-23; §8).

    Every targeted family pays the same full GT-Anywhere tuition per child (Texas
    voucher = self-pay), so value varies across families ONLY by how many children
    they enrolled (the Interest form's "How many children? 1–5+"). The old funded
    multiplier / per-family hash variance are gone — this is a real funnel signal,
    not jitter. ``tuition_annual_default`` is params (INV-11); normalized by
    :func:`value_max` at scoring time so the value term stays in [0,1] (A-1).
    """
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * _child_count(family)


def value_max(params: Params) -> float:
    """The value normalizer (A-1): per-child tuition × the max child count (§8).

    Derived deterministically from existing §8 params — no new magic number.
    Dividing :func:`value` by this keeps the value term in [0,1]: a family with the
    ``max_children`` cap (the Interest form's "5+") hits exactly 1.0, none exceed it.
    """
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * value_cfg.max_children


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


def freshness(family: WorkQueueFamily, params: Params, *, now: datetime | None = None) -> float:
    """Freshness ∈ [floor,1] — how recently a family went quiet (S12; recoverable_now).

    ``freshness = max(floor, min(1, 1 - elapsed_days / window))`` where
    ``elapsed_days`` is days since the stall anchor (``stalled_since`` if present,
    else ``created_at``), ``window = work_queue.freshness_window_days`` and
    ``floor = work_queue.freshness_floor`` (both params — INV-11). A family at its
    anchor scores 1.0; one a full window (or more) past it floors but never hits 0,
    so a long-stalled family stays rankable. A family with neither anchor is
    treated as fully fresh (1.0 — absence of evidence is not decay), mirroring the
    stall-recency convention. Pure: ``now`` is injected (INV-2).

    Args:
        family: The queue-relevant family attributes.
        params: Loaded params (§8); supplies the freshness window + floor.
        now: Reference time for the decay; defaults to UTC now.

    Returns:
        The freshness factor in ``[floor, 1.0]``.
    """
    reference = _now_or(now)
    anchor = family.stalled_since if family.stalled_since is not None else family.created_at
    if anchor is None:
        return 1.0
    window_days = params.work_queue.freshness_window_days
    floor = params.work_queue.freshness_floor
    elapsed_days = (reference - anchor) / timedelta(days=1)
    return max(floor, min(1.0, 1.0 - elapsed_days / window_days))


def recoverable_now(
    family: WorkQueueFamily, params: Params, *, now: datetime | None = None
) -> float:
    """The recoverable-now ranking key — ``value × score × freshness`` (A-23).

    Composes the queue's value (now ``num_children × per-child tuition``) with the
    likelihood score and the time decay (:func:`freshness`): a many-children,
    deep-funnel, freshly-stalled family ranks far above a one-child, cold,
    top-of-funnel one. The old per-family hash *variance* factor is GONE — the
    per-row spread is now a REAL signal (child count drives value, funnel depth
    drives score), not jitter (A-23). Pure and params-driven (INV-11), ``now``
    injected (INV-2).

    Args:
        family: The queue-relevant family attributes.
        params: Loaded params (§8).
        now: Reference time for the freshness decay; defaults to UTC now.

    Returns:
        The recoverable-now score (a positive dollars-weighted magnitude, not
        normalized to [0,1] — it is a ranking key, not a probability).
    """
    reference = _now_or(now)
    return (
        value(family, params)
        * score_family(family, params, now=reference)
        * freshness(family, params, now=reference)
    )


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

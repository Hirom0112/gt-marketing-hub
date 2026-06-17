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
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.params import Params
from app.data.models import FundingType, Stage

# §4.8 funnel order, Interest → Tuition. Stage proximity normalizes a family's
# position along this path to [0,1]; closer to Tuition = more recoverable (§5.1).
# Derived from the Stage enum, not a hardcoded magnitude (INV-11).
_STAGE_ORDER: tuple[Stage, ...] = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION)


@runtime_checkable
class _Scorable(Protocol):
    """The funnel attributes the recoverability/freshness sub-factors read.

    Both :class:`WorkQueueFamily` and the per-child :class:`WorkQueueStudent`
    (A-24) satisfy this structurally, so the §5.1 sub-factors (stall recency,
    stage proximity, freshness) are computed once and reused for either unit.
    """

    current_stage: Stage
    stalled_since: datetime | None
    created_at: datetime | None
    responsiveness: float


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


class WorkQueueStudent(BaseModel):
    """The queue-relevant attributes for ONE child's funnel (A-24; FR-2.5).

    The per-child analog of :class:`WorkQueueFamily`. Each child runs its own
    funnel (one application per child), so the queue scores STUDENTS: a child's
    value is exactly **one** per-child tuition (the A-23 ``num_children``
    multiplier is gone — a household's $-at-risk is the SUM over its still-
    recoverable students, no longer all-or-nothing). Funnel position / stall
    recency / responsiveness drive recoverability exactly as for a family.
    """

    model_config = ConfigDict(frozen=True)

    student_id: UUID
    family_id: UUID  # the household, for grouping/aggregation on the board.
    current_stage: Stage
    stalled_since: datetime | None = None
    created_at: datetime | None = None
    responsiveness: float = 0.0
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


def _stall_recency(family: _Scorable, params: Params, *, now: datetime) -> float:
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


def _stage_proximity(family: _Scorable) -> float:
    """Stage-proximity sub-factor ∈ [0,1] — closer to Tuition ⇒ higher (§5.1).

    The family's index along the §4.8 funnel order, normalized by the number of
    transitions (Interest → 0.0, Tuition → 1.0).
    """
    return _STAGE_ORDER.index(family.current_stage) / (len(_STAGE_ORDER) - 1)


def recoverability(family: _Scorable, params: Params, *, now: datetime | None = None) -> float:
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


def deadline_proximity(
    days_remaining: int | None,
    *,
    at_risk: bool,
    params: Params,
) -> float:
    """Deadline-proximity sub-factor ∈ [0,1] — voucher-deadline urgency (R2).

    A family AWARDED/SELECTED but not yet RECONFIRMED near its voucher deadline is
    about to LOSE the award, so it must float to the top of the work queue. This
    maps that urgency into [0,1] from the ``voucher_standing`` signals:

    * ``0.0`` when the family is **not at risk** or carries **no deadline**
      (``days_remaining is None``) — so a family without a voucher deadline is
      UNCHANGED by the R2 term (the base scoring stays byte-identical);
    * otherwise it rises linearly as the deadline nears,
      ``clamp01(1 - days_remaining / deadline_horizon_days)`` — ``0`` a full
      horizon (or more) out, ``1.0`` on the deadline day, and clamped to ``1.0``
      once past due (negative ``days_remaining``).

    The horizon is params-homed (``work_queue.deadline_horizon_days``, INV-11);
    nothing here is a code literal. Pure — it consumes the precomputed standing
    signals (``days_remaining``/``at_risk`` from :func:`voucher_standing`) as
    inputs and does no I/O (INV-2).

    Args:
        days_remaining: Days until the reconfirm/select deadline (``None`` when the
            family has no reconfirm deadline); may be negative if past due.
        at_risk: Whether the family is in the "$X lost on a deadline" gap
            (selected/awarded but not yet reconfirmed, deadline at hand).
        params: Loaded params (§8); supplies ``deadline_horizon_days``.

    Returns:
        The deadline-proximity factor in [0,1].
    """
    if not at_risk or days_remaining is None:
        return 0.0
    horizon = params.work_queue.deadline_horizon_days
    return _clamp01(1.0 - days_remaining / horizon)


def score_family(
    family: WorkQueueFamily,
    params: Params,
    *,
    now: datetime | None = None,
    deadline_proximity: float = 0.0,
) -> float:
    """Score one family for the work queue (FR-2.5; §5.1, A-1, R2).

    ``score = w_recoverability · recoverability + w_value · (value / value_max)
    + w_deadline · deadline_proximity``, with each term in [0,1] and every weight
    read from §8 params (INV-11). Conceptually this is the value written to
    ``family_record.work_queue_score``; persistence/wiring lives in a later slice.

    The ``deadline_proximity`` term (R2) defaults to ``0.0`` — so a family with no
    voucher deadline / not at risk scores EXACTLY as before — and rises toward
    ``w_deadline`` as an at-risk family's reconfirm deadline nears (compute it with
    the module-level :func:`deadline_proximity` from the family's
    ``voucher_standing``). The signal is an INPUT (no I/O here), keeping the scorer
    pure (INV-2).

    Args:
        family: The queue-relevant family attributes.
        params: Loaded params (§8); supplies the headline weights, sub-weights,
            value baseline/multiplier, stall window, and the deadline weight.
        now: Reference time for the stall-recency window; defaults to UTC now.
        deadline_proximity: The [0,1] voucher-deadline-urgency factor for this
            family (default ``0.0`` ⇒ no deadline pressure / unchanged score);
            clamped defensively.

    Returns:
        The work-queue score in ``[0, w_recoverability + w_value + w_deadline]``.
    """
    work_queue = params.work_queue
    recover_term = recoverability(family, params, now=now)
    value_term = value(family, params) / value_max(params)
    return (
        work_queue.w_recoverability * recover_term
        + work_queue.w_value * value_term
        + work_queue.w_deadline * _clamp01(deadline_proximity)
    )


def freshness(family: _Scorable, params: Params, *, now: datetime | None = None) -> float:
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


# --------------------------------------------------------------------------- #
# A-24 — per-child scoring. Each child is worth exactly ONE per-child tuition
# (no num_children multiplier); the funnel sub-factors are shared with the
# family scorer via _Scorable, so per-student ranking is driven by recoverability
# (the value term is uniform across students — every student is one child).
# --------------------------------------------------------------------------- #


def student_value(params: Params) -> float:
    """One child's queue value — a single per-child tuition (A-24; §8).

    The A-23 ``num_children`` multiplier is dropped: a Student is one child, so
    its value is exactly ``tuition_annual_default`` (params — INV-11). A
    household's dollar value is the SUM of its students' :func:`student_value`
    over the ones still in play (resolved at the API/aggregation layer), which is
    why a partially-stalled household no longer over- or under-states its $ at risk.
    """
    return params.work_queue.value.tuition_annual_default


def score_student(
    student: WorkQueueStudent, params: Params, *, now: datetime | None = None
) -> float:
    """Score one child for the work queue (A-24; FR-2.5, §5.1).

    Same shape as :func:`score_family` —
    ``w_recoverability · recoverability + w_value · (value / value_max)`` — but the
    value term uses :func:`student_value` (one child), so it is uniform across
    students and the per-student ranking is driven by recoverability (funnel depth
    + stall recency + responsiveness). Every weight is params (INV-11); ``now`` is
    injected (INV-2).
    """
    work_queue = params.work_queue
    recover_term = recoverability(student, params, now=now)
    value_term = student_value(params) / value_max(params)
    return work_queue.w_recoverability * recover_term + work_queue.w_value * value_term


def recoverable_now_student(
    student: WorkQueueStudent, params: Params, *, now: datetime | None = None
) -> float:
    """A child's recoverable-now ranking key — ``student_value × score × freshness``.

    The per-child analog of :func:`recoverable_now`: one child's tuition weighted
    by likelihood and time decay. Pure and params-driven (INV-11); ``now`` injected.
    """
    reference = _now_or(now)
    return (
        student_value(params)
        * score_student(student, params, now=reference)
        * freshness(student, params, now=reference)
    )


def rank_students(
    students: list[WorkQueueStudent],
    params: Params,
    *,
    now: datetime | None = None,
) -> list[WorkQueueStudent]:
    """Rank children by work-queue score, highest first (A-24; FR-2.5).

    Deterministic: descending score, ties broken by ascending ``student_id`` so
    the order is total and reproducible. Not mutated; a new list is returned.
    """
    reference = _now_or(now)
    return sorted(
        students,
        key=lambda student: (-score_student(student, params, now=reference), student.student_id),
    )

"""Work-queue scorer tests (S1; ARCHITECTURE.md §5.1, §8, CLAUDE.md §4.1, A-1).

The work-queue scorer (FR-2.5) is the headline deterministic unit:

    score = w_recoverability · recoverability + w_value · (value / value_max)

with both terms in [0,1]. `recoverability` composes three normalized
sub-factors (stall-recency, stage-proximity, responsiveness) weighted per §8
(stage-proximity DOMINANT — funnel depth, A-23); `value` is `num_children ×
per-child tuition` (A-23 — every targeted family is full-pay so value varies
only by child count) and is normalized by `value_max = max_children × tuition`
(A-1) so the value term stays in [0,1]. No LLM, no adapters — a pure function of
the typed inputs + params (CLAUDE.md §3, INV-2).

Every expected value here is computed FROM the params (not hardcoded literals),
so the suite stays correct if a tunable is retuned: it pins the *formula*, and
`test_params_work_queue.py` pins the *values*. Together they make the worked
target (CLAUDE.md §4.1) reproducible and drift-proof (INV-11).

Deterministic without a local `params/params.yaml` (gitignored, not created):
the committed `params/params.example.yaml` is passed explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.core.params import Params, load_params
from app.core.work_queue import (
    WorkQueueFamily,
    WorkQueueStudent,
    deadline_proximity,
    freshness,
    rank_families,
    rank_students,
    recoverable_now,
    recoverable_now_student,
    score_family,
    score_student,
    student_value,
    value,
    value_max,
)
from app.data.models import FundingType, Stage

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# A fixed reference "now" so the stall-recency factor is deterministic under test.
NOW = datetime(2026, 6, 14, tzinfo=UTC)

# Stable UUIDs so tiebreak ordering (by family_id) is assertable.
FID_A = UUID("00000000-0000-0000-0000-0000000000aa")
FID_B = UUID("00000000-0000-0000-0000-0000000000bb")
FID_C = UUID("00000000-0000-0000-0000-0000000000cc")


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


# ---------------------------------------------------------------------------
# Reference implementation of the §5.1 / A-1 formula, computed FROM params.
# The production `score_family` must reproduce these to 4 dp. Kept independent
# of the implementation so a copy-paste bug in the scorer is caught.
# ---------------------------------------------------------------------------

# §4.8 stage order, nearest-to-Tuition = most recoverable. Used to derive the
# stage-proximity sub-factor without hardcoding a magnitude.
_STAGE_ORDER = [Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _expected_stall_recency(stalled_since: datetime | None, params: Params) -> float:
    window = params.work_queue.stall_window_days
    if stalled_since is None:
        return 1.0
    days = (NOW - stalled_since).total_seconds() / timedelta(days=1).total_seconds()
    return _clamp01(1.0 - days / window)


def _expected_stage_proximity(stage: Stage) -> float:
    return _STAGE_ORDER.index(stage) / (len(_STAGE_ORDER) - 1)


def _expected_recoverability(family: WorkQueueFamily, params: Params) -> float:
    sub = params.work_queue.recoverability
    return (
        sub.stall_recency_weight * _expected_stall_recency(family.stalled_since, params)
        + sub.stage_proximity_weight * _expected_stage_proximity(family.current_stage)
        + sub.responsiveness_weight * _clamp01(family.responsiveness)
    )


def _expected_value(family: WorkQueueFamily, params: Params) -> float:
    # A-23: per-child tuition × the family's child count (floored at 1 child).
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * max(1, family.num_children)


def _expected_value_max(params: Params) -> float:
    # A-1/A-23: per-child tuition × the "5+" child-count cap (max_children).
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * value_cfg.max_children


def _expected_score(family: WorkQueueFamily, params: Params) -> float:
    wq = params.work_queue
    recoverability = _expected_recoverability(family, params)
    value_term = _expected_value(family, params) / _expected_value_max(params)
    return wq.w_recoverability * recoverability + wq.w_value * value_term


# ---------------------------------------------------------------------------
# Three fixture families spanning different recoverability/value.
# ---------------------------------------------------------------------------


def _family_high() -> WorkQueueFamily:
    """Near Tuition, freshly stalled, responsive, 4-child voucher ⇒ high score."""
    return WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.TUITION,
        stalled_since=NOW - timedelta(days=2),
        responsiveness=0.9,
        num_children=4,
        funding_type=FundingType.TEFA_STANDARD,
    )


def _family_mid() -> WorkQueueFamily:
    """Mid-funnel, half-window stall, middling responsiveness, 2-child self-pay."""
    return WorkQueueFamily(
        family_id=FID_B,
        current_stage=Stage.ENROLL,
        stalled_since=NOW - timedelta(days=7),
        responsiveness=0.5,
        num_children=2,
        funding_type=FundingType.SELF_PAY,
    )


def _family_low() -> WorkQueueFamily:
    """Top-of-funnel, long-since stalled (beyond window), unresponsive, 1 child."""
    return WorkQueueFamily(
        family_id=FID_C,
        current_stage=Stage.INTEREST,
        stalled_since=NOW - timedelta(days=30),
        responsiveness=0.0,
        num_children=1,
        funding_type=FundingType.SELF_PAY,
    )


def test_score_matches_params_worked_target() -> None:
    """`score_family` equals the params-derived expected score to 4 dp (§5.1/A-1).

    Three fixture families span the recoverability/value space. The expected
    score is recomputed from the params here (not a literal), so the test pins
    the formula and survives a retune. Both score terms must lie in [0,1].
    """
    params = _params()
    for family in (_family_high(), _family_mid(), _family_low()):
        actual = score_family(family, params, now=NOW)
        expected = _expected_score(family, params)

        # Both component terms must individually stay in [0,1] (§5.1, A-1).
        recoverability = _expected_recoverability(family, params)
        value_term = _expected_value(family, params) / _expected_value_max(params)
        assert 0.0 <= recoverability <= 1.0
        assert 0.0 <= value_term <= 1.0

        assert round(actual, 4) == round(expected, 4)
        assert 0.0 <= actual <= 1.0


def test_recoverability_subfactors() -> None:
    """`recoverability` ∈ [0,1], composed from the three weighted sub-factors (§8).

    Each sub-factor is normalized to [0,1]: stall-recency relative to the stall
    window (fresher ⇒ higher), stage-proximity (closer to Tuition ⇒ higher),
    responsiveness. The weighted composite reproduces the reference to 4 dp.
    """
    params = _params()

    # A fully-recoverable family: freshly stalled at Tuition, fully responsive.
    best = WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.TUITION,
        stalled_since=NOW,
        responsiveness=1.0,
        funding_type=FundingType.TEFA_STANDARD,
    )
    # A least-recoverable family: long-stalled at Interest, unresponsive.
    worst = WorkQueueFamily(
        family_id=FID_C,
        current_stage=Stage.INTEREST,
        stalled_since=NOW - timedelta(days=60),
        responsiveness=0.0,
        funding_type=None,
    )

    from app.core.work_queue import recoverability as recoverability_fn

    best_r = recoverability_fn(best, params, now=NOW)
    worst_r = recoverability_fn(worst, params, now=NOW)

    assert round(best_r, 4) == round(_expected_recoverability(best, params), 4)
    assert round(worst_r, 4) == round(_expected_recoverability(worst, params), 4)
    assert best_r == 1.0  # all three sub-factors maxed ⇒ weights sum to 1.0
    assert worst_r == 0.0  # all three sub-factors floored
    assert 0.0 <= worst_r <= best_r <= 1.0

    # Stage proximity ranks the stages monotonically toward Tuition.
    proximities = [_expected_stage_proximity(s) for s in _STAGE_ORDER]
    assert proximities == sorted(proximities)
    assert proximities[0] == 0.0 and proximities[-1] == 1.0

    # No stall ⇒ stall-recency is fully recoverable (not a penalty).
    never_stalled = WorkQueueFamily(
        family_id=FID_B,
        current_stage=Stage.ENROLL,
        stalled_since=None,
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )
    assert round(recoverability_fn(never_stalled, params, now=NOW), 4) == round(
        _expected_recoverability(never_stalled, params), 4
    )


def test_value_scales_with_child_count() -> None:
    """`value` = per-child tuition × num_children, scored as value/value_max (A-23/A-1).

    Every targeted family is full-pay (Texas voucher = self-pay), so funding tier
    no longer scales value — child count does. A 4-child family is worth exactly
    4× a 1-child family; the term is normalized by `value_max = max_children ×
    tuition` so it stays in [0,1]. The expected values derive from params, not
    literals (INV-11).
    """
    params = _params()
    tuition = params.work_queue.value.tuition_annual_default

    vmax = value_max(params)
    assert vmax == _expected_value_max(params)
    assert vmax == tuition * params.work_queue.value.max_children
    assert vmax > 0.0

    big = _family_high()  # 4 children
    small = _family_low()  # 1 child

    assert value(big, params) == _expected_value(big, params)
    assert value(small, params) == _expected_value(small, params)
    # Real per-family spread: 4 children is worth exactly 4× one child.
    assert value(big, params) == tuition * 4
    assert value(small, params) == tuition * 1
    assert value(big, params) == 4 * value(small, params)

    # Normalized value term stays within [0,1] for every child count (A-1).
    for fam in (_family_high(), _family_mid(), _family_low()):
        assert 0.0 <= value(fam, params) / vmax <= 1.0

    # A stray non-positive child count floors at 1 child (never zeroes the row).
    zero_kids = small.model_copy(update={"num_children": 0})
    assert value(zero_kids, params) == tuition * 1


def test_ranking_is_stable() -> None:
    """`rank_families` is deterministic, ties broken stably by family_id (§5.1).

    Highest score first. Repeated runs are byte-for-byte identical, and equal
    scores resolve by ascending `family_id` so the order never wobbles.
    """
    params = _params()
    families = [_family_low(), _family_high(), _family_mid()]

    ranked = rank_families(families, params, now=NOW)
    scores = [score_family(f, params, now=NOW) for f in ranked]

    # Descending by score.
    assert scores == sorted(scores, reverse=True)
    # High-recoverability family leads; low trails.
    assert ranked[0].family_id == FID_A
    assert ranked[-1].family_id == FID_C
    # Determinism: a second run (re-shuffled input) yields the same order.
    again = rank_families(list(reversed(families)), params, now=NOW)
    assert [f.family_id for f in again] == [f.family_id for f in ranked]

    # Tie-break: two families with identical score-bearing attributes but
    # different ids resolve by ascending family_id, deterministically.
    tie_first = WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.ENROLL,
        stalled_since=NOW - timedelta(days=3),
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )
    tie_second = tie_first.model_copy(update={"family_id": FID_B})
    tied = rank_families([tie_second, tie_first], params, now=NOW)
    assert score_family(tie_first, params, now=NOW) == score_family(tie_second, params, now=NOW)
    assert [f.family_id for f in tied] == [FID_A, FID_B]


# ---------------------------------------------------------------------------
# S12 W1 — freshness decay + recoverable_now ranking (A-19).
# ---------------------------------------------------------------------------


def test_freshness_decays_from_one_to_floor() -> None:
    """`freshness` = max(floor, min(1, 1 - elapsed/window)) off the stall anchor.

    Pinned at three points: at the stall anchor freshness is 1.0; at half the
    window it is exactly 0.5; at and beyond a full window it floors. The window
    and floor are read from params (INV-11), not hardcoded literals.
    """
    params = _params()
    window = params.work_queue.freshness_window_days
    floor = params.work_queue.freshness_floor

    stalled = NOW - timedelta(days=0)
    at_anchor = WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.ENROLL,
        stalled_since=stalled,
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )
    assert freshness(at_anchor, params, now=NOW) == 1.0

    mid = at_anchor.model_copy(update={"stalled_since": NOW - timedelta(days=window / 2)})
    assert freshness(mid, params, now=NOW) == 0.5

    past = at_anchor.model_copy(update={"stalled_since": NOW - timedelta(days=window)})
    assert freshness(past, params, now=NOW) == floor

    way_past = at_anchor.model_copy(update={"stalled_since": NOW - timedelta(days=window * 3)})
    assert freshness(way_past, params, now=NOW) == floor


def test_freshness_uses_created_at_when_no_stall() -> None:
    """With no `stalled_since`, freshness anchors on `created_at` (the fallback)."""
    params = _params()
    window = params.work_queue.freshness_window_days
    family = WorkQueueFamily(
        family_id=FID_B,
        current_stage=Stage.APPLY,
        stalled_since=None,
        created_at=NOW - timedelta(days=window / 2),
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )
    assert freshness(family, params, now=NOW) == 0.5


def test_recoverable_now_is_value_x_score_x_freshness() -> None:
    """`recoverable_now` = value × score_family × freshness, all params-driven (A-23).

    The old per-family hash *variance* factor is gone — the per-row spread now
    comes from real signals (child count drives value, funnel depth drives score).
    """
    params = _params()
    family = _family_high()
    expected = (
        value(family, params)
        * score_family(family, params, now=NOW)
        * freshness(family, params, now=NOW)
    )
    assert recoverable_now(family, params, now=NOW) == pytest.approx(expected, rel=1e-9)


def test_recoverable_now_scales_with_children() -> None:
    """A many-child family outranks an otherwise-identical one-child family (A-23).

    Value spread is now a REAL signal: holding stage / recency / responsiveness
    fixed, more children ⇒ proportionally higher recoverable_now.
    """
    params = _params()
    one = _family_mid().model_copy(update={"num_children": 1})
    five = _family_mid().model_copy(update={"num_children": 5})
    assert recoverable_now(five, params, now=NOW) > recoverable_now(one, params, now=NOW)


def test_recoverable_now_orders_three_families() -> None:
    """recoverable_now ranks the three fixture families high > mid > low.

    The high family (near Tuition, fresh, responsive, funded) outranks the mid
    (half-window stall, self-pay) which outranks the low (beyond-window stall,
    Interest, unresponsive) — the freshness factor sharpens the spread.
    """
    params = _params()
    high = recoverable_now(_family_high(), params, now=NOW)
    mid = recoverable_now(_family_mid(), params, now=NOW)
    low = recoverable_now(_family_low(), params, now=NOW)
    assert high > mid > low


def test_recoverable_now_is_params_sensitive() -> None:
    """Shrinking the freshness window lowers recoverable_now for a stalled family.

    A param drift must move the number — proving recoverable_now reads the window
    from params, never a hardcoded constant (INV-11).
    """
    params = _params()
    family = _family_mid()  # stalled half a window ago.
    base = recoverable_now(family, params, now=NOW)

    tighter = params.model_copy(
        update={
            "work_queue": params.work_queue.model_copy(
                update={"freshness_window_days": params.work_queue.freshness_window_days // 3}
            )
        }
    )
    assert recoverable_now(family, tighter, now=NOW) < base


def test_stage_proximity_dominates_recoverability() -> None:
    """Funnel depth is the dominant recoverability sub-factor (A-23).

    "The further they went, the more recoverable they are": holding recency and
    responsiveness fixed, advancing one stage down the funnel must raise
    recoverability by the stage_proximity weight share — and that weight is the
    largest of the three sub-weights (the user-directed rebalance).
    """
    from app.core.work_queue import recoverability as recoverability_fn

    params = _params()
    sub = params.work_queue.recoverability
    # The rebalance: funnel depth outweighs both recency and responsiveness.
    assert sub.stage_proximity_weight > sub.stall_recency_weight
    assert sub.stage_proximity_weight > sub.responsiveness_weight

    base = WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.INTEREST,
        stalled_since=NOW - timedelta(days=3),
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )
    deeper = base.model_copy(update={"current_stage": Stage.ENROLL})
    gain = recoverability_fn(deeper, params, now=NOW) - recoverability_fn(base, params, now=NOW)
    # Interest→Enroll is 2 of 3 proximity steps ⇒ +(2/3)·stage_proximity_weight.
    expected_gain = (2 / 3) * sub.stage_proximity_weight
    assert gain == pytest.approx(expected_gain, rel=1e-9)


# ---------------------------------------------------------------------------
# A-24 — per-child scoring: one student = one per-child tuition (no multiplier).
# ---------------------------------------------------------------------------

SID_A = UUID("00000000-0000-0000-0000-0000000000a1")
SID_B = UUID("00000000-0000-0000-0000-0000000000b2")
SID_C = UUID("00000000-0000-0000-0000-0000000000c3")


def test_student_value_is_one_child_tuition_no_multiplier() -> None:
    """A student is worth exactly one per-child tuition; N students sum to N×tuition."""
    params = _params()
    tuition = params.work_queue.value.tuition_annual_default

    # One student = one child of tuition (the A-23 num_children multiplier is gone).
    assert student_value(params) == tuition

    # A household 3-up: per-student aggregation = 3 × tuition (not all-or-nothing).
    household = [student_value(params) for _ in range(3)]
    assert sum(household) == 3 * tuition


def _student(
    sid: UUID, stage: Stage, *, stalled_days: int, responsiveness: float
) -> WorkQueueStudent:
    return WorkQueueStudent(
        student_id=sid,
        family_id=FID_A,
        current_stage=stage,
        stalled_since=NOW - timedelta(days=stalled_days),
        responsiveness=responsiveness,
        funding_type=FundingType.TEFA_STANDARD,
    )


def test_score_student_matches_params_formula_to_4dp() -> None:
    """score_student reproduces w_rec·recoverability + w_value·(tuition/value_max)."""
    params = _params()
    s = _student(SID_A, Stage.ENROLL, stalled_days=3, responsiveness=0.5)

    # Reference recoverability reuses the family expectation (same sub-factors).
    ref_family = WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.ENROLL,
        stalled_since=NOW - timedelta(days=3),
        responsiveness=0.5,
    )
    wq = params.work_queue
    expected = wq.w_recoverability * _expected_recoverability(ref_family, params) + wq.w_value * (
        student_value(params) / _expected_value_max(params)
    )
    assert round(score_student(s, params, now=NOW), 4) == round(expected, 4)


def test_rank_students_orders_by_recoverability_then_student_id() -> None:
    """Deeper-funnel/fresher students rank higher; ties break on student_id (A-24)."""
    params = _params()
    deep = _student(SID_A, Stage.TUITION, stalled_days=1, responsiveness=0.9)
    shallow = _student(SID_B, Stage.INTEREST, stalled_days=40, responsiveness=0.0)
    ranked = rank_students([shallow, deep], params, now=NOW)
    assert [s.student_id for s in ranked] == [SID_A, SID_B]

    # Two identical students ⇒ stable tiebreak by ascending student_id.
    twin1 = _student(SID_C, Stage.ENROLL, stalled_days=5, responsiveness=0.3)
    twin2 = _student(SID_B, Stage.ENROLL, stalled_days=5, responsiveness=0.3)
    ranked_ties = rank_students([twin1, twin2], params, now=NOW)
    assert [s.student_id for s in ranked_ties] == [SID_B, SID_C]


def test_recoverable_now_student_is_positive_and_value_scaled() -> None:
    """recoverable_now_student = student_value × score × freshness (A-24)."""
    params = _params()
    s = _student(SID_A, Stage.ENROLL, stalled_days=3, responsiveness=0.5)
    expected = (
        student_value(params) * score_student(s, params, now=NOW) * freshness(s, params, now=NOW)
    )
    assert round(recoverable_now_student(s, params, now=NOW), 4) == round(expected, 4)
    assert recoverable_now_student(s, params, now=NOW) > 0.0


# ---------------------------------------------------------------------------
# R2 — deadline-proximity term: an at-risk family near a voucher deadline (e.g.
# AWARDED/SELECTED but not yet RECONFIRMED) is about to LOSE its award, so it
# ranks to the TOP of the queue. The term is 0 when there is no deadline / the
# family is not at risk, so families without a voucher deadline are UNCHANGED
# (the existing scoring tests stay green).
# ---------------------------------------------------------------------------


def _expected_deadline_proximity(
    days_remaining: int | None, at_risk: bool, params: Params
) -> float:
    """Reference deadline-proximity ∈ [0,1], computed FROM params (not a literal).

    Zero when not at risk or no deadline; otherwise rises linearly as the
    deadline nears, ``clamp01(1 - days_remaining / horizon)`` over the params
    ``deadline_horizon_days`` window (a past-due deadline ⇒ 1.0).
    """
    if not at_risk or days_remaining is None:
        return 0.0
    horizon = params.work_queue.deadline_horizon_days
    return _clamp01(1.0 - days_remaining / horizon)


def test_deadline_proximity_zero_without_deadline_or_risk() -> None:
    """No deadline / not at risk ⇒ proximity is exactly 0.0 (families unchanged)."""
    params = _params()
    # No days_remaining (no reconfirm deadline) ⇒ 0, even if flagged at_risk.
    assert deadline_proximity(None, at_risk=True, params=params) == 0.0
    # Has a deadline but NOT at risk (already reconfirmed) ⇒ 0.
    assert deadline_proximity(3, at_risk=False, params=params) == 0.0


def test_deadline_proximity_rises_as_deadline_nears() -> None:
    """For an at-risk family, proximity rises toward 1.0 as days_remaining → 0."""
    params = _params()
    horizon = params.work_queue.deadline_horizon_days

    far = deadline_proximity(horizon, at_risk=True, params=params)
    near = deadline_proximity(1, at_risk=True, params=params)
    at = deadline_proximity(0, at_risk=True, params=params)
    overdue = deadline_proximity(-5, at_risk=True, params=params)

    # Matches the params-derived reference to 4 dp (pins the formula, INV-11).
    for dr in (horizon, 1, 0):
        assert round(deadline_proximity(dr, at_risk=True, params=params), 4) == round(
            _expected_deadline_proximity(dr, True, params), 4
        )

    assert far == 0.0  # a full horizon out ⇒ no urgency yet
    assert at == 1.0  # the deadline is today ⇒ maximal urgency
    assert overdue == 1.0  # clamped — past due stays at 1.0, never above
    assert far < near < at  # monotonic: closer ⇒ higher
    for dr in (horizon, 5, 1, 0, -10):
        assert 0.0 <= deadline_proximity(dr, at_risk=True, params=params) <= 1.0


def test_score_family_unchanged_without_deadline() -> None:
    """REGRESSION: with no deadline signal, score is EXACTLY the old formula (R2).

    The deadline term defaults to 0 so a family without a voucher deadline scores
    byte-identically to before the R2 change — the existing scoring tests stay
    green. Asserted to full precision (==, not rounded) across all three fixtures.
    """
    params = _params()
    for family in (_family_high(), _family_mid(), _family_low()):
        # No deadline arg passed ⇒ identical to the pre-R2 expected formula.
        assert score_family(family, params, now=NOW) == _expected_score(family, params)
        # Explicit zero proximity is the same as omitting it.
        assert score_family(family, params, now=NOW, deadline_proximity=0.0) == _expected_score(
            family, params
        )


def test_score_family_adds_weighted_deadline_term() -> None:
    """score gains ``w_deadline · deadline_proximity`` on top of the base (R2)."""
    params = _params()
    wq = params.work_queue
    family = _family_mid()
    base = _expected_score(family, params)

    proximity = 1.0  # deadline today
    scored = score_family(family, params, now=NOW, deadline_proximity=proximity)
    assert round(scored, 4) == round(base + wq.w_deadline * proximity, 4)
    # The term genuinely moves the score (w_deadline is non-trivial).
    assert scored > base


def test_at_risk_near_deadline_outranks_higher_value_non_urgent() -> None:
    """An at-risk near-deadline family outranks a higher-value non-urgent one (R2).

    The demo behavior: a family AWARDED/SELECTED but not yet RECONFIRMED with the
    voucher deadline at hand jumps to the TOP of the queue over a richer, deeper-
    funnel family that has no deadline pressure. All numbers read from params.
    """
    params = _params()

    # The "richer / deeper" family: top score-bearing attributes, but NO deadline.
    rich = _family_high()  # near Tuition, fresh, responsive, 4 children
    rich_score = score_family(rich, params, now=NOW)  # no deadline term

    # The at-risk family: a thinner base (top-of-funnel, fewer children) but its
    # voucher deadline is today ⇒ proximity 1.0.
    at_risk = _family_low()  # Interest, 1 child, unresponsive ⇒ low base
    prox = deadline_proximity(0, at_risk=True, params=params)
    assert prox == 1.0
    at_risk_score = score_family(at_risk, params, now=NOW, deadline_proximity=prox)

    # Sanity: without the deadline term the at-risk family would LOSE outright.
    assert score_family(at_risk, params, now=NOW) < rich_score
    # With the deadline term it leaps ahead — the at-risk award is about to lapse.
    assert at_risk_score > rich_score

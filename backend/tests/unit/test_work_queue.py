"""Work-queue scorer tests (S1; ARCHITECTURE.md §5.1, §8, CLAUDE.md §4.1, A-1).

The work-queue scorer (FR-2.5) is the headline deterministic unit:

    score = w_recoverability · recoverability + w_value · (value / value_max)

with both terms in [0,1]. `recoverability` composes three normalized
sub-factors (stall-recency, stage-proximity, responsiveness) weighted per §8;
`value` derives from the tuition baseline × the applicable funded multiplier and
is normalized by `value_max` (A-1) so the value term stays in [0,1]. No LLM, no
adapters — a pure function of the typed inputs + params (CLAUDE.md §3, INV-2).

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

from app.core.params import Params, load_params
from app.core.work_queue import WorkQueueFamily, rank_families, score_family
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


def _is_funded(funding_type: FundingType | None) -> bool:
    return funding_type is not None and funding_type is not FundingType.SELF_PAY


def _expected_value(family: WorkQueueFamily, params: Params) -> float:
    value_cfg = params.work_queue.value
    multiplier = value_cfg.funded_multiplier if _is_funded(family.funding_type) else 1.0
    return value_cfg.tuition_annual_default * multiplier


def _expected_value_max(params: Params) -> float:
    # A-1: tuition_annual_default × max applicable funded_multiplier.
    value_cfg = params.work_queue.value
    return value_cfg.tuition_annual_default * max(1.0, value_cfg.funded_multiplier)


def _expected_score(family: WorkQueueFamily, params: Params) -> float:
    wq = params.work_queue
    recoverability = _expected_recoverability(family, params)
    value_term = _expected_value(family, params) / _expected_value_max(params)
    return wq.w_recoverability * recoverability + wq.w_value * value_term


# ---------------------------------------------------------------------------
# Three fixture families spanning different recoverability/value.
# ---------------------------------------------------------------------------


def _family_high() -> WorkQueueFamily:
    """Near Tuition, freshly stalled, responsive, TEFA-funded ⇒ high score."""
    return WorkQueueFamily(
        family_id=FID_A,
        current_stage=Stage.TUITION,
        stalled_since=NOW - timedelta(days=2),
        responsiveness=0.9,
        funding_type=FundingType.TEFA_STANDARD,
    )


def _family_mid() -> WorkQueueFamily:
    """Mid-funnel, half-window stall, middling responsiveness, self-pay."""
    return WorkQueueFamily(
        family_id=FID_B,
        current_stage=Stage.ENROLL,
        stalled_since=NOW - timedelta(days=7),
        responsiveness=0.5,
        funding_type=FundingType.SELF_PAY,
    )


def _family_low() -> WorkQueueFamily:
    """Top-of-funnel, long-since stalled (beyond window), unresponsive, no funding."""
    return WorkQueueFamily(
        family_id=FID_C,
        current_stage=Stage.INTEREST,
        stalled_since=NOW - timedelta(days=30),
        responsiveness=0.0,
        funding_type=None,
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


def test_value_normalized_to_max() -> None:
    """`value` = tuition_default × funded_multiplier, scored as value/value_max (A-1).

    TEFA-funded families take the funded multiplier; self-pay/no-funding take the
    1.0 baseline. The value term is normalized by `value_max` so it stays in
    [0,1]. With the committed funded_multiplier == 1.0, funded and self-pay tie
    at the cap — the test pins the derivation from params, not the literal.
    """
    params = _params()
    from app.core.work_queue import value as value_fn
    from app.core.work_queue import value_max as value_max_fn

    funded = _family_high()
    self_pay = _family_mid()

    vmax = value_max_fn(params)
    assert vmax == _expected_value_max(params)
    assert vmax > 0.0

    funded_value = value_fn(funded, params)
    self_pay_value = value_fn(self_pay, params)
    assert funded_value == _expected_value(funded, params)
    assert self_pay_value == _expected_value(self_pay, params)

    # Normalized value term stays within [0,1] for every funding path (A-1).
    for v in (funded_value, self_pay_value):
        assert 0.0 <= v / vmax <= 1.0

    # Funded value is never below self-pay (multiplier ≥ 1.0 by §8).
    assert funded_value >= self_pay_value


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

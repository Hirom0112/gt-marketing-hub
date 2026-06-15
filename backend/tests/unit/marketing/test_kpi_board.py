"""KPI rollup board tests (FR-3.11).

The board takes per-channel observed metrics and compares each to the params
baseline/target (`params.kpi.levers`, INV-11). These tests pin the rollup math
against values READ FROM `load_params()` — never hardcoded — so a param drift
(e.g. a changed target) fails the build (CLAUDE.md §4.1).
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import Params, load_params
from app.marketing.kpi_board import ChannelKpi, roll_up, summary

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def test_kpi_rollup_matches_fixture() -> None:
    """Per-channel rollups + lever deltas equal params-derived expected values."""
    params = _params()
    levers = params.kpi.levers

    # A fixture of observed metrics; the rest of the channels default to baseline.
    metrics = {"instagram": 0.04, "email": 0.12}

    rollups = roll_up(metrics, params=params)
    by_channel = {r.channel: r for r in rollups}

    # The rollup covers exactly the params channels — all 8.
    assert len(rollups) == 8
    assert set(by_channel) == set(levers)

    # Ordering is deterministic: sorted by channel name.
    assert [r.channel for r in rollups] == sorted(levers)

    # Every channel's baseline/target are READ FROM params (drift-failing).
    for ch, lever in levers.items():
        r = by_channel[ch]
        assert r.baseline == lever.baseline
        assert r.target == lever.target

    # Observed-metric channels: delta, gap, and met computed against params.
    ig = by_channel["instagram"]
    assert ig.metric == 0.04
    assert ig.lever_delta == 0.04 - levers["instagram"].baseline
    assert ig.target_gap == levers["instagram"].target - 0.04
    assert ig.target_met == (0.04 >= levers["instagram"].target)

    em = by_channel["email"]
    assert em.metric == 0.12
    assert em.lever_delta == 0.12 - levers["email"].baseline
    assert em.target_gap == levers["email"].target - 0.12
    assert em.target_met == (0.12 >= levers["email"].target)


def test_missing_metric_defaults_to_baseline() -> None:
    """A channel with no observed metric defaults to its params baseline (delta 0)."""
    params = _params()
    levers = params.kpi.levers

    rollups = roll_up({"instagram": 0.04}, params=params)
    by_channel = {r.channel: r for r in rollups}

    for ch, lever in levers.items():
        if ch == "instagram":
            continue
        r = by_channel[ch]
        assert r.metric == lever.baseline
        assert r.lever_delta == 0.0
        assert r.target_gap == lever.target - lever.baseline
        assert r.target_met == (lever.baseline >= lever.target)


def test_channel_kpi_is_frozen() -> None:
    """`ChannelKpi` is an immutable result record."""
    params = _params()
    r = roll_up({}, params=params)[0]
    assert isinstance(r, ChannelKpi)
    try:
        r.metric = 1.0  # type: ignore[misc]
    except (AttributeError, TypeError, ValueError):
        return
    raise AssertionError("ChannelKpi should be frozen")


def test_summary_counts_target_met() -> None:
    """`summary` aggregates the count of target-met channels (params-derived)."""
    params = _params()
    levers = params.kpi.levers

    # Beat every channel's target by setting each metric just above target.
    metrics = {ch: lever.target + 0.01 for ch, lever in levers.items()}
    rollups = roll_up(metrics, params=params)

    s = summary(rollups)
    assert s["channels"] == len(levers)
    assert s["target_met"] == len(levers)

    # And with all-default (baseline) metrics, none beat a positive target.
    s_default = summary(roll_up({}, params=params))
    expected_met = sum(1 for lv in levers.values() if lv.baseline >= lv.target)
    assert s_default["target_met"] == expected_met

"""KPI rollup board (FR-3.11) — per-channel observed metric vs params lever.

The board takes a mapping of channel -> observed metric (an engagement/conversion
rate) and rolls each channel up against the params lever (`params.kpi.levers`):
its `baseline` (current) and `target`. The lift over baseline is the
`lever_delta = metric - baseline`; the distance still to go is
`target_gap = target - metric`; `target_met = metric >= target`.

Baselines and targets are ALWAYS read from params (INV-11) — never hardcoded —
so a param drift moves the rollup, and the tests fail on drift (CLAUDE.md §4.1).

Pure module (ARCHITECTURE.md §3): imports only `app.core.params`; no
`anthropic` / `langgraph` / I/O / `datetime` (guarded by `test_core_purity`).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from app.core.params import Params


class ChannelKpi(BaseModel):
    """One channel's rollup: observed metric compared to its params lever.

    Frozen result record. `baseline` / `target` are copied from
    `params.kpi.levers[channel]`; the remaining fields are derived from the
    observed `metric`. A channel with no observed metric has `metric == baseline`
    (so `lever_delta == 0.0`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str
    metric: float
    baseline: float
    target: float
    lever_delta: float
    target_gap: float
    target_met: bool


def roll_up(metrics: Mapping[str, float], *, params: Params) -> list[ChannelKpi]:
    """Roll observed channel metrics up against the params KPI levers (FR-3.11).

    Produces one :class:`ChannelKpi` per channel present in
    `params.kpi.levers`, reading `baseline`/`target` from params (INV-11). A
    channel absent from `metrics` defaults to `metric = baseline` (delta 0).

    Ordering is deterministic: sorted by channel name.

    Args:
        metrics: channel token -> observed metric value (rate). Channels not in
            `params.kpi.levers` are ignored.
        params: loaded params; `params.kpi.levers` supplies baseline/target.

    Returns:
        Channel rollups, sorted by channel name.
    """
    levers = params.kpi.levers
    rollups: list[ChannelKpi] = []
    for channel in sorted(levers):
        lever = levers[channel]
        metric = metrics.get(channel, lever.baseline)
        rollups.append(
            ChannelKpi(
                channel=channel,
                metric=metric,
                baseline=lever.baseline,
                target=lever.target,
                lever_delta=metric - lever.baseline,
                target_gap=lever.target - metric,
                target_met=metric >= lever.target,
            )
        )
    return rollups


def summary(rollups: list[ChannelKpi]) -> dict[str, int]:
    """Aggregate a rollup list for the API/board header.

    Returns the channel count and how many channels met their target.
    """
    return {
        "channels": len(rollups),
        "target_met": sum(1 for r in rollups if r.target_met),
    }

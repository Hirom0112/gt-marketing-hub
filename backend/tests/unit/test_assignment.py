"""M4 assignment-engine routing tests (TODO.md M4; ASSUMPTIONS A-32; CLAUDE §4.1).

`route_family(signals, agents, params)` is the PURE, DETERMINISTIC routing
rule-table over the EXISTING work-queue signals (no new scoring math — A-32 /
MULTI_AGENT_COCKPIT §4). A family routes to the **closer tier** if ANY of
R-1…R-4 holds (first-match precedence), else the **setter tier** (R-5); R-6 is
deterministic within-tier round-robin in ascending rank order.

These tests pin each rule against PARAMS-DERIVED expectations (never hardcoded
literals): a param drift MUST change the routing (the INV-11 guard). Pure unit:
no I/O, no adapters, no LLM — only the registry, params, and the router.
"""

from __future__ import annotations

from pathlib import Path

from app.core.assignment import RoutingSignals, route_family
from app.core.params import Params, load_params
from app.core.sales_agents import SALES_AGENTS, SalesAgent

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _closers(params: Params) -> list[SalesAgent]:
    """The closer tier — agents at rank <= closer_rank_max, ascending rank."""
    cutoff = params.assignment.closer_rank_max
    return sorted((a for a in SALES_AGENTS if a.rank <= cutoff), key=lambda a: a.rank)


def _setters(params: Params) -> list[SalesAgent]:
    """The setter tier — agents at rank > closer_rank_max, ascending rank."""
    cutoff = params.assignment.closer_rank_max
    return sorted((a for a in SALES_AGENTS if a.rank > cutoff), key=lambda a: a.rank)


def _standard_signals() -> RoutingSignals:
    """A family that hits NONE of R-1…R-4 (routes setter by R-5)."""
    return RoutingSignals(
        recoverability=0.0,
        value=0.0,
        value_max=1.0,
        at_risk=False,
        days_remaining=None,
        num_children=1,
    )


def test_route_family_rule_table() -> None:
    params = _params()
    closers = _closers(params)
    setters = _setters(params)
    assert closers, "demo registry must have at least one closer"
    assert setters, "demo registry must have at least one setter"

    # R-5 standard → setter (none of R-1…R-4): the baseline.
    tier, agent_id = route_family(_standard_signals(), SALES_AGENTS, params)
    assert tier == "setter"
    assert agent_id == setters[0].agent_id

    # R-1 deadline-imminent → closer, via at_risk.
    sig = _standard_signals()
    sig = sig.model_copy(update={"at_risk": True, "days_remaining": 5})
    tier, agent_id = route_family(sig, SALES_AGENTS, params)
    assert tier == "closer"
    assert agent_id == closers[0].agent_id

    # R-1 deadline-imminent → closer, via days_remaining <= deadline_alarm_days
    # (params-derived boundary: at the alarm threshold is imminent).
    alarm = params.assignment.deadline_alarm_days
    sig = _standard_signals().model_copy(update={"at_risk": False, "days_remaining": alarm})
    assert route_family(sig, SALES_AGENTS, params)[0] == "closer"
    # One day beyond the alarm window is NOT imminent (drops to setter on R-1 alone).
    sig = _standard_signals().model_copy(update={"days_remaining": alarm + 1})
    assert route_family(sig, SALES_AGENTS, params)[0] == "setter"

    # R-2 high-value → closer: RAW value at/above the params threshold.
    threshold = params.assignment.high_value_threshold
    sig = _standard_signals().model_copy(update={"value": threshold})
    assert route_family(sig, SALES_AGENTS, params)[0] == "closer"
    # Just below the threshold is NOT high-value (R-2 alone → setter).
    sig = _standard_signals().model_copy(update={"value": threshold - 0.01})
    assert route_family(sig, SALES_AGENTS, params)[0] == "setter"

    # R-3 multi-child household → closer: > 1 student.
    sig = _standard_signals().model_copy(update={"num_children": 2})
    assert route_family(sig, SALES_AGENTS, params)[0] == "closer"
    sig = _standard_signals().model_copy(update={"num_children": 1})
    assert route_family(sig, SALES_AGENTS, params)[0] == "setter"

    # R-4 high-likelihood → closer: recoverability at/above the params threshold.
    hl = params.assignment.high_likelihood_threshold
    sig = _standard_signals().model_copy(update={"recoverability": hl})
    assert route_family(sig, SALES_AGENTS, params)[0] == "closer"
    sig = _standard_signals().model_copy(update={"recoverability": hl - 0.01})
    assert route_family(sig, SALES_AGENTS, params)[0] == "setter"


def test_route_family_thresholds_read_from_params() -> None:
    """A param drift MUST change routing — thresholds are not hardcoded (INV-11)."""
    params = _params()

    # A value just below the SHIPPED high_value_threshold routes setter…
    just_below = _standard_signals().model_copy(
        update={"value": params.assignment.high_value_threshold - 1.0}
    )
    assert route_family(just_below, SALES_AGENTS, params)[0] == "setter"

    # …but lowering the threshold (a param drift) flips that SAME family to closer.
    lowered = params.model_copy(
        update={
            "assignment": params.assignment.model_copy(
                update={"high_value_threshold": params.assignment.high_value_threshold - 2.0}
            )
        }
    )
    assert route_family(just_below, SALES_AGENTS, lowered)[0] == "closer"


def test_route_family_round_robin_is_deterministic() -> None:
    """R-6: same-tier families distribute across eligible agents in rank order.

    With a multi-agent closer tier, two identical high-value families round-robin
    across the closer agents (ascending rank) by stable input index — never the
    same agent twice in a row, and stable across runs.
    """
    params = _params()
    # Force a 2-agent closer tier so round-robin is observable (the demo ships 1
    # closer; this raises closer_rank_max so BOTH demo agents are closers).
    two_closer = params.model_copy(
        update={
            "assignment": params.assignment.model_copy(
                update={"closer_rank_max": max(a.rank for a in SALES_AGENTS)}
            )
        }
    )
    closers = _closers(two_closer)
    assert len(closers) >= 2, "this test needs a >=2-agent closer tier"

    high_value = _standard_signals().model_copy(
        update={"value": two_closer.assignment.high_value_threshold}
    )

    # Round-robin by stable input index: index 0 → first closer, index 1 → second.
    first = route_family(high_value, SALES_AGENTS, two_closer, index=0)
    second = route_family(high_value, SALES_AGENTS, two_closer, index=1)
    assert first[0] == "closer" and second[0] == "closer"
    assert first[1] == closers[0].agent_id
    assert second[1] == closers[1].agent_id
    assert first[1] != second[1]

    # Determinism: re-running the same (signals, index) yields the same agent.
    assert route_family(high_value, SALES_AGENTS, two_closer, index=1) == second
    # Wrap-around: index N returns to the first closer (round-robin modulo tier size).
    wrapped = route_family(high_value, SALES_AGENTS, two_closer, index=len(closers))
    assert wrapped[1] == closers[0].agent_id

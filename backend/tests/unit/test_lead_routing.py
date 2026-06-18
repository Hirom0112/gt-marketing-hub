"""Deterministic lead-routing core tests (LEAD_ASSIGNMENT.md §2–§9; CLAUDE §4.1).

`route_lead` / `resolve_owner` / `is_sla_breached` are PURE and DETERMINISTIC: a
function of typed inputs + params alone (no I/O, no now()/random). These tests pin
each rule against PARAMS-DERIVED expectations (territory map, weights, caps, SLA
timer read from the committed example params), so a param drift MUST change the
routing (the INV-11 guard). The fairness test proves the weighted distribution
matches the params weight ratio over N leads; the overflow test pins
cap-beats-weight precedence.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.core.lead_routing import (
    LeadSignals,
    OwnerOutcome,
    is_hot,
    is_sla_breached,
    resolve_owner,
    route_lead,
)
from app.core.params import AgentPolicy, Params, RoundRobin, Territory, load_params
from app.core.sales_agents import SALES_AGENTS, SalesAgent
from app.data.models import FundingState, IncomeTier, Stage

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# The two seeded demo agents: A = Riley (rank 1, FL, closer), B = Jordan (rank 2, CA, qualifier).
_A = SALES_AGENTS[0].agent_id  # FL closer
_B = SALES_AGENTS[1].agent_id  # CA qualifier


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _with_registry(
    policies: dict[UUID, AgentPolicy],
    *,
    mode: str = "weighted",
    fallback: str = "round_robin_all",
) -> tuple[tuple[SalesAgent, ...], Params]:
    """Build a custom (agents, params) pair for multi-agent-per-pool tests.

    The 2-agent demo (one agent per state) hides round-robin/readiness — those
    need ≥2 agents in one pool (LEAD_ASSIGNMENT.md §16 / §17). The SalesAgent rank
    is derived from insertion order; agent_id keys the params policy.
    """
    base = _params()
    agents = tuple(
        SalesAgent(agent_id=aid, rank=i + 1, synthetic_name=f"Agent-{i + 1}", tier="closer")
        for i, aid in enumerate(policies)
    )
    new_assignment = base.assignment.model_copy(
        update={
            "agents": {str(aid): pol for aid, pol in policies.items()},
            "round_robin": RoundRobin(mode=mode),  # type: ignore[arg-type]
            "territory": Territory(fallback=fallback),  # type: ignore[arg-type]
        }
    )
    return agents, base.model_copy(update={"assignment": new_assignment})


def _pol(
    territory: list[str], role: str, *, weight: int = 1, cap: int = 40, status: str = "available"
) -> AgentPolicy:
    return AgentPolicy(
        territory=territory, role=role, status=status, weight=weight, capacity_cap=cap
    )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LA-9 — resolve_owner (the §3 gate).
# ---------------------------------------------------------------------------


def test_existing_owner_is_respected_and_short_circuits() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=1), state="CA", assigned_rep_id=_A)
    res = resolve_owner(sig, SALES_AGENTS, params)
    assert res.outcome is OwnerOutcome.OWNED and res.agent_id == _A and res.via == "assigned"
    # route_lead routes to the incumbent — NOT by territory (A covers FL, not CA).
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.owner_match and d.agent_id == _A and d.rule == "owner-match"


def test_self_report_resolves_to_ownership() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=2), state="FL", reported_rep_id=_B)
    res = resolve_owner(sig, SALES_AGENTS, params)
    assert res.outcome is OwnerOutcome.OWNED and res.agent_id == _B and res.via == "self_report"
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.owner_match and d.agent_id == _B  # sticky to the self-reported rep, not FL territory


def test_existing_owner_beats_self_report() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=3), state="FL", assigned_rep_id=_A, reported_rep_id=_B)
    res = resolve_owner(sig, SALES_AGENTS, params)
    assert res.agent_id == _A and res.via == "assigned"


def test_unknown_self_report_grants_no_ownership() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=4), state="FL", reported_rep_id=UUID(int=999))
    res = resolve_owner(sig, SALES_AGENTS, params)
    assert res.outcome is OwnerOutcome.NEW  # mistyped/stale name dropped silently, fail-closed
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert not d.owner_match and d.agent_id == _A  # routes as new (FL → A)


def test_ambiguous_identity_is_held_not_guessed() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=5), state="FL", identity_ambiguous=True)
    assert resolve_owner(sig, SALES_AGENTS, params).outcome is OwnerOutcome.AMBIGUOUS
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.agent_id is None and d.rule == "held-ambiguous-identity"


def test_two_distinct_identity_owners_is_ambiguous() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=6), state="FL", identity_owner_ids=(_A, _B))
    assert resolve_owner(sig, SALES_AGENTS, params).outcome is OwnerOutcome.AMBIGUOUS


def test_single_identity_owner_is_owned() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=7), state="CA", identity_owner_ids=(_A, _A))
    res = resolve_owner(sig, SALES_AGENTS, params)
    assert res.outcome is OwnerOutcome.OWNED and res.agent_id == _A and res.via == "identity"


# ---------------------------------------------------------------------------
# LA-11 — territory routing (FL → A, CA → B, uncovered → fallback).
# ---------------------------------------------------------------------------


def test_fl_family_routes_to_agent_a() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=10), state="FL", current_stage=Stage.ENROLL)
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.agent_id == _A and "territory: state=FL" in d.reason


def test_ca_family_routes_to_agent_b() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=11), state="CA", current_stage=Stage.INTEREST)
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.agent_id == _B and "territory: state=CA" in d.reason


def test_uncovered_state_takes_the_fallback_pool() -> None:
    params = _params()
    sig = LeadSignals(family_id=UUID(int=12), state="TX", current_stage=Stage.INTEREST)
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    assert d.agent_id in {_A, _B} and d.rule == "fallback" and "uncovered" in d.reason


def test_uncovered_state_can_be_parked() -> None:
    # intake_park fallback ⇒ HELD for a human, not auto-routed.
    a1, a2 = UUID(int=21), UUID(int=22)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer"), a2: _pol(["CA"], "qualifier")}, fallback="intake_park"
    )
    sig = LeadSignals(family_id=UUID(int=13), state="TX")
    d = route_lead(sig, agents, params, cursors={}, loads={})
    assert d.agent_id is None and d.rule == "held-territory-park"


# ---------------------------------------------------------------------------
# LA-12 — readiness / role (hot → closer, early-stage → qualifier).
# ---------------------------------------------------------------------------


def test_readiness_routes_hot_to_closer_early_to_qualifier() -> None:
    closer, qualifier = UUID(int=31), UUID(int=32)
    agents, params = _with_registry(
        {closer: _pol(["FL"], "closer"), qualifier: _pol(["FL"], "qualifier")}
    )
    hot = LeadSignals(family_id=UUID(int=33), state="FL", current_stage=Stage.ENROLL)
    early = LeadSignals(family_id=UUID(int=34), state="FL", current_stage=Stage.INTEREST)
    assert is_hot(hot, params) and not is_hot(early, params)
    assert route_lead(hot, agents, params, cursors={}, loads={}).agent_id == closer
    assert route_lead(early, agents, params, cursors={}, loads={}).agent_id == qualifier


def test_apply_stage_is_hot_only_when_funding_receipt_ready() -> None:
    params = _params()
    ready = LeadSignals(
        family_id=UUID(int=35),
        state="FL",
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
    )
    not_ready = LeadSignals(
        family_id=UUID(int=36),
        state="FL",
        current_stage=Stage.APPLY,
        funding_state=FundingState.APPLIED,
    )
    assert is_hot(ready, params) and not is_hot(not_ready, params)


# ---------------------------------------------------------------------------
# LA-13 — weighted round-robin is provably fair over N leads.
# ---------------------------------------------------------------------------


def _run_n(agents: tuple[SalesAgent, ...], params: Params, signal_for, n: int) -> Counter[UUID]:
    cursors: dict[str, int] = {}
    counts: Counter[UUID] = Counter()
    for i in range(n):
        d = route_lead(signal_for(i), agents, params, cursors={**cursors}, loads={})
        assert d.agent_id is not None
        counts[d.agent_id] += 1
        if d.cursor_advanced_to is not None:
            cursors[d.pool_key] = d.cursor_advanced_to
    return counts


def test_weighted_distribution_matches_ratio_over_n() -> None:
    a1, a2 = UUID(int=41), UUID(int=42)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer", weight=2), a2: _pol(["FL"], "closer", weight=1)}
    )
    counts = _run_n(
        agents,
        params,
        lambda i: LeadSignals(family_id=UUID(int=1000 + i), state="FL", current_stage=Stage.ENROLL),
        30,
    )
    # weights 2:1 over 30 ⇒ exactly 20 / 10 (the params ratio, INV-11).
    assert counts[a1] == 20 and counts[a2] == 10


def test_flat_distribution_is_even() -> None:
    a1, a2 = UUID(int=43), UUID(int=44)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer", weight=2), a2: _pol(["FL"], "closer", weight=1)}, mode="flat"
    )
    counts = _run_n(
        agents,
        params,
        lambda i: LeadSignals(family_id=UUID(int=2000 + i), state="FL", current_stage=Stage.ENROLL),
        30,
    )
    # flat ignores weights ⇒ 15 / 15.
    assert counts[a1] == 15 and counts[a2] == 15


# ---------------------------------------------------------------------------
# LA-T8 — cap-beats-weight overflow (the explicit precedence the user required).
# ---------------------------------------------------------------------------


def test_cap_beats_weight_overflows_to_next_agent() -> None:
    a1, a2 = UUID(int=51), UUID(int=52)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer", weight=2, cap=5), a2: _pol(["FL"], "closer", weight=1, cap=5)}
    )
    sig = LeadSignals(family_id=UUID(int=53), state="FL", current_stage=Stage.ENROLL)
    # a1 is AT capacity (load == cap) ⇒ despite its higher weight, every lead
    # overflows to a2 (cap beats weight).
    loads = {a1: 5, a2: 0}
    for _ in range(4):
        d = route_lead(sig, agents, params, cursors={}, loads=loads)
        assert d.agent_id == a2


def test_all_capped_is_held_not_overassigned() -> None:
    a1, a2 = UUID(int=54), UUID(int=55)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer", weight=2, cap=5), a2: _pol(["FL"], "closer", weight=1, cap=5)}
    )
    sig = LeadSignals(family_id=UUID(int=56), state="FL", current_stage=Stage.ENROLL)
    d = route_lead(sig, agents, params, cursors={}, loads={a1: 5, a2: 5})
    assert d.agent_id is None and d.rule == "held-all-capped"


# ---------------------------------------------------------------------------
# LA-14 — a paused agent is skipped in rotation / absent from every pool.
# ---------------------------------------------------------------------------


def test_paused_agent_is_skipped() -> None:
    a1, a2 = UUID(int=61), UUID(int=62)
    agents, params = _with_registry(
        {a1: _pol(["FL"], "closer", status="out"), a2: _pol(["FL"], "closer")}
    )
    sig = LeadSignals(family_id=UUID(int=63), state="FL", current_stage=Stage.ENROLL)
    for _ in range(5):
        d = route_lead(sig, agents, params, cursors={}, loads={})
        assert d.agent_id == a2  # a1 paused ⇒ never chosen


def test_excluded_agent_is_skipped_for_sla_reroute() -> None:
    a1, a2 = UUID(int=64), UUID(int=65)
    agents, params = _with_registry({a1: _pol(["FL"], "closer"), a2: _pol(["FL"], "closer")})
    sig = LeadSignals(family_id=UUID(int=66), state="FL", current_stage=Stage.ENROLL)
    d = route_lead(sig, agents, params, cursors={}, loads={}, exclude=frozenset({a1}))
    assert d.agent_id == a2  # the breached rep is excluded from the reroute pool


# ---------------------------------------------------------------------------
# LA-16 — SLA breach predicate (the pure part of §9).
# ---------------------------------------------------------------------------


def test_sla_breached_when_unworked_past_timer() -> None:
    params = _params()
    now = datetime(2026, 6, 15, tzinfo=UTC)
    days = params.assignment.sla.unworked_reassign_days
    stale = now - timedelta(days=days + 1)
    fresh = now - timedelta(days=days - 1)
    # assigned long ago, never contacted ⇒ breached.
    assert is_sla_breached(stale, None, now, params)
    # assigned long ago but contacted AFTER assignment ⇒ worked, not breached.
    assert not is_sla_breached(stale, stale + timedelta(hours=1), now, params)
    # assigned recently ⇒ not yet breached.
    assert not is_sla_breached(fresh, None, now, params)
    # unassigned (no owner) ⇒ never breaches here (intake's concern).
    assert not is_sla_breached(None, None, now, params)


def test_income_tier_signal_is_logged_not_a_gate() -> None:
    params = _params()
    sig = LeadSignals(
        family_id=UUID(int=70),
        state="FL",
        current_stage=Stage.ENROLL,
        income_tier=IncomeTier.LT_65K,
    )
    d = route_lead(sig, SALES_AGENTS, params, cursors={}, loads={})
    # income-tier shows in the reason as a TEFA-eligible prioritization, but the
    # pool is unchanged (still FL → A).
    assert d.agent_id == _A and "TEFA-eligible" in d.reason

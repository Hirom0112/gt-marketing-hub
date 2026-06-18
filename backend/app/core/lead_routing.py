"""The deterministic lead-routing core (LEAD_ASSIGNMENT.md §2–§9).

``route_lead(signals, agents, params, ...)`` is the first-match precedence router
that auto-assigns one inbound lead to a concrete sales agent **and produces a
human-readable reason** for every decision (the deterministic-and-explainable
mandate). The precedence (LEAD_ASSIGNMENT.md §2):

* **R0 owner-match** (`resolve_owner`): an existing ``assigned_rep_id``, an
  identity-linked owned household, or a resolved self-reported prior agent →
  route to that owner, **never silently reassign** (the user's "one source of
  truth"). An ambiguous identity is **held**, never guessed (INV-4 fail-closed).
* **R1 territory**: restrict to agents whose params territory covers the family's
  ``state``; an uncovered state takes the configured fallback (§4).
* **R2 readiness/role**: hot / ready-to-deposit → ``closer`` role; early-stage →
  ``qualifier`` (the BDR seat).
* **R3 income-tier**: a logged prioritization/tiebreak signal (§6), not a hard gate.
* **R4 weighted round-robin** over the surviving pool, **cap-beats-weight** (a
  capped agent overflows to the next in ring order), with a deterministic
  per-pool cursor (§7).

PURE + DETERMINISTIC (CLAUDE.md §3, INV-2): a function of its typed inputs +
params alone — no I/O, no LLM, no adapter, no ``now()``/random. The *write* that
persists an assignment is the deterministic API route; this module only DECIDES.
Every threshold/weight/cap reads from ``params`` (INV-11): there is no routing
literal here, so a rule test fails if a param drifts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.params import Params
from app.core.sales_agents import SalesAgent
from app.data.models import FundingState, IncomeTier, Stage

# A routed ROLE — the product term: ``closer`` (hot / ready-to-deposit leads) or
# ``qualifier`` (the setter/BDR seat; early-stage leads). A TYPE, not a number
# (INV-11 governs numbers), so it is a code literal.
Role = Literal["closer", "qualifier"]

# The funding states at/after which a family is "ready-to-deposit" (the hot path
# for an APPLY-stage lead) — GT-confirmed and beyond (no Odyssey API; INV-10).
_RECEIPT_READY: frozenset[FundingState] = frozenset(
    {
        FundingState.GT_CONFIRMED,
        FundingState.FIRST_INSTALLMENT_RECEIVED,
        FundingState.FUNDED,
    }
)


class LeadSignals(BaseModel):
    """The signals the router reads for ONE family (LEAD_ASSIGNMENT.md §2–§6).

    A projection of the family record + existing work-queue/voucher signals (no
    new scoring math). Frozen so a routed family cannot mutate mid-decision.
    ``identity_owner_ids`` / ``identity_ambiguous`` are the dedup verdict the API
    layer computes from the cohort (``app.core.identity``) and hands in — the core
    stays pure (it does not scan the cohort itself).
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    # Territory + income routing inputs (§4/§6).
    state: str | None = None
    income_tier: IncomeTier | None = None
    # Readiness inputs (§5) — stage + funding progression + work-queue signals.
    current_stage: Stage = Stage.INTEREST
    funding_state: FundingState = FundingState.NONE
    at_risk: bool = False
    days_remaining: int | None = None
    value: float = 0.0
    num_children: int = 1
    # Ownership inputs (§3). assigned_rep_id = the current DB owner; reported_rep_id
    # = the applicant's self-reported prior agent (resolved to an agent_id at
    # capture); identity_* = the dedup verdict (owners of identity-matched
    # households; ambiguous ⇒ hold).
    assigned_rep_id: UUID | None = None
    reported_rep_id: UUID | None = None
    identity_owner_ids: tuple[UUID, ...] = ()
    identity_ambiguous: bool = False


class OwnerOutcome(StrEnum):
    """The owner-resolution verdict (LEAD_ASSIGNMENT.md §3)."""

    OWNED = "owned"  # a confident existing owner → route to them, never reassign
    AMBIGUOUS = "ambiguous"  # review-queue / >1 distinct owner → HOLD, do not guess
    NEW = "new"  # genuinely new → fall through to territory routing


@dataclass(frozen=True)
class OwnerResolution:
    """The result of :func:`resolve_owner` — an outcome + (for OWNED) the agent."""

    outcome: OwnerOutcome
    agent_id: UUID | None
    reason: str
    via: str  # 'assigned' | 'identity' | 'self_report' | '' (NEW/AMBIGUOUS)


@dataclass(frozen=True)
class RoutingDecision:
    """One routing decision (LEAD_ASSIGNMENT.md §2). ``agent_id is None`` ⇒ HELD
    (ambiguous identity, parked fallback, or every eligible agent capped) — a
    fail-closed non-assignment, never a guess."""

    family_id: UUID
    agent_id: UUID | None
    routed_role: Role | None
    reason: str
    rule: str  # 'owner-match' | 'territory' | 'fallback' | 'held-*'
    owner_match: bool
    pool_key: str
    cursor_advanced_to: int | None


# ---------------------------------------------------------------------------
# Agent policy access (the params-driven registry view; INV-11).
# ---------------------------------------------------------------------------


def _name(agent_id: UUID, agents: tuple[SalesAgent, ...]) -> str:
    for a in agents:
        if a.agent_id == agent_id:
            return a.synthetic_name
    return str(agent_id)


def _names(pool: list[SalesAgent]) -> str:
    return ", ".join(a.synthetic_name for a in pool)


def _available_agents(
    agents: tuple[SalesAgent, ...], params: Params, exclude: frozenset[UUID]
) -> list[SalesAgent]:
    """Agents that have a routing policy, are ``available``, and are not excluded.

    A non-``available`` status (``out``/``onboarding``) or an excluded agent (an
    SLA-breached rep being routed away from) is absent from every pool, so the
    round-robin cursor can never land on them (§8).
    """
    out: list[SalesAgent] = []
    for a in sorted(agents, key=lambda x: x.rank):
        policy = params.assignment.agents.get(str(a.agent_id))
        if policy is None or policy.status != "available":
            continue
        if a.agent_id in exclude:
            continue
        out.append(a)
    return out


def is_hot(signals: LeadSignals, params: Params) -> bool:
    """True iff a lead is HOT / ready-to-deposit → the ``closer`` role (§5).

    Deterministic, derived from existing signals (no new score): an enroll/tuition
    family, an at-risk/deadline-imminent voucher, a high-value family (raw value ≥
    the params threshold), or an APPLY-stage family whose funding is receipt-ready.
    Every threshold reads from ``params.assignment`` (INV-11). Else early-stage ⇒
    the ``qualifier`` role.
    """
    cfg = params.assignment
    if signals.current_stage in (Stage.ENROLL, Stage.TUITION):
        return True
    if signals.at_risk:
        return True
    if signals.days_remaining is not None and signals.days_remaining <= cfg.deadline_alarm_days:
        return True
    if signals.value >= cfg.high_value_threshold:
        return True
    if signals.current_stage is Stage.APPLY and signals.funding_state in _RECEIPT_READY:
        return True
    return False


# ---------------------------------------------------------------------------
# R0 — owner resolution (the gate above the router; §3).
# ---------------------------------------------------------------------------


def resolve_owner(
    signals: LeadSignals, agents: tuple[SalesAgent, ...], params: Params
) -> OwnerResolution:
    """Resolve a lead's existing owner, fail-closed (LEAD_ASSIGNMENT.md §3).

    Precedence: an existing ``assigned_rep_id`` (the strongest — already owned) >
    an identity-linked owned household > a resolved self-reported prior agent. An
    ambiguous identity (review-queue, or >1 distinct owner) is **never guessed** —
    it returns AMBIGUOUS so the caller HOLDS the lead (INV-4). A self-reported id
    that does not resolve to a known agent grants no ownership (it is dropped
    silently → NEW), so a mistyped/stale name can never misroute (§3 guardrails).
    """
    known = {a.agent_id for a in agents}

    # (1) Existing DB owner — already owned, never reassigned by the router.
    if signals.assigned_rep_id is not None and signals.assigned_rep_id in known:
        nm = _name(signals.assigned_rep_id, agents)
        return OwnerResolution(
            OwnerOutcome.OWNED,
            signals.assigned_rep_id,
            f"owner-match: family already owned by {nm} "
            f"(rep_id={signals.assigned_rep_id}); not reassigned",
            "assigned",
        )

    # (2) Ambiguous identity — review-queue or >1 distinct owner ⇒ HOLD, never guess.
    distinct_identity_owners = {oid for oid in signals.identity_owner_ids if oid in known}
    if signals.identity_ambiguous or len(distinct_identity_owners) > 1:
        return OwnerResolution(
            OwnerOutcome.AMBIGUOUS,
            None,
            "ambiguous identity: matches review-queue / >1 owner — "
            "held for human review, not assigned",
            "",
        )

    # (3) Identity-linked to exactly one owned household → that household's owner.
    if len(distinct_identity_owners) == 1:
        oid = next(iter(distinct_identity_owners))
        nm = _name(oid, agents)
        return OwnerResolution(
            OwnerOutcome.OWNED,
            oid,
            f"owner-match: identity-linked to a household owned by {nm}; not reassigned",
            "identity",
        )

    # (4) Self-reported prior agent — ownership ONLY if it resolves to a known agent.
    if signals.reported_rep_id is not None:
        if signals.reported_rep_id in known:
            nm = _name(signals.reported_rep_id, agents)
            return OwnerResolution(
                OwnerOutcome.OWNED,
                signals.reported_rep_id,
                f"self-reported prior contact → owner {nm}; "
                "sticky through SIS (one source of truth)",
                "self_report",
            )
        # Unresolved self-report — dropped silently, no ownership (fail-closed).

    return OwnerResolution(OwnerOutcome.NEW, None, "no existing owner — routing as a new lead", "")


# ---------------------------------------------------------------------------
# R4 — weighted round-robin with cap-beats-weight (§7).
# ---------------------------------------------------------------------------


def _weighted_pick(
    pool: list[SalesAgent],
    params: Params,
    loads: dict[UUID, int],
    start_cursor: int,
) -> tuple[SalesAgent, int] | None:
    """Pick one agent from ``pool`` by weighted round-robin, cap-beats-weight.

    A weight-expanded virtual ring (each agent repeated ``weight`` times in rank
    order; weight 1 in ``flat`` mode) is scanned from ``start_cursor``; the first
    ring slot whose agent has headroom (``load < capacity_cap``) wins. So weight
    governs SHARE among agents with headroom, but a CAPPED agent is skipped
    (overflows to the next) — cap beats weight (§7). Returns ``(agent, next_cursor)``
    where ``next_cursor`` is the position after the chosen slot (mod ring length),
    or ``None`` when EVERY agent in the pool is capped (fail-closed → the caller
    holds the lead rather than over-assign).
    """
    weighted = params.assignment.round_robin.mode == "weighted"
    ring: list[SalesAgent] = []
    for a in sorted(pool, key=lambda x: x.rank):
        policy = params.assignment.agents[str(a.agent_id)]
        repeat = max(1, policy.weight) if weighted else 1
        ring.extend([a] * repeat)
    n = len(ring)
    if n == 0:
        return None
    start = start_cursor % n
    for offset in range(n):
        idx = (start + offset) % n
        agent = ring[idx]
        cap = params.assignment.agents[str(agent.agent_id)].capacity_cap
        if loads.get(agent.agent_id, 0) < cap:
            return agent, (idx + 1) % n
    return None  # every agent in the pool is at capacity


# ---------------------------------------------------------------------------
# The router (LEAD_ASSIGNMENT.md §2).
# ---------------------------------------------------------------------------


def _held(family_id: UUID, rule: str, reason: str) -> RoutingDecision:
    return RoutingDecision(
        family_id=family_id,
        agent_id=None,
        routed_role=None,
        reason=reason,
        rule=rule,
        owner_match=False,
        pool_key="",
        cursor_advanced_to=None,
    )


def route_lead(
    signals: LeadSignals,
    agents: tuple[SalesAgent, ...],
    params: Params,
    *,
    cursors: dict[str, int],
    loads: dict[UUID, int],
    exclude: frozenset[UUID] = frozenset(),
) -> RoutingDecision:
    """Route one lead to ``(agent_id, role, reason)`` — the §2 precedence table.

    PURE + DETERMINISTIC. ``cursors`` (pool_key → next ring index) and ``loads``
    (agent_id → current open-family count) are passed IN; the chosen pool's new
    cursor is returned on the decision so the API route can persist it. ``exclude``
    drops agents from every pool (the SLA sweep routes a breached lead away from
    the breached rep). Same inputs ⇒ same decision, always.
    """
    fid = signals.family_id

    # --- R0: owner match (sticky) / ambiguous (hold) ------------------------
    owner = resolve_owner(signals, agents, params)
    if owner.outcome is OwnerOutcome.AMBIGUOUS:
        return _held(fid, "held-ambiguous-identity", owner.reason)

    prefix = ""
    if owner.outcome is OwnerOutcome.OWNED:
        assert owner.agent_id is not None
        policy = params.assignment.agents.get(str(owner.agent_id))
        available = policy is not None and policy.status == "available"
        if available and owner.agent_id not in exclude:
            return RoutingDecision(
                family_id=fid,
                agent_id=owner.agent_id,
                routed_role=policy.role if policy else None,
                reason=owner.reason,
                rule="owner-match",
                owner_match=True,
                pool_key="owner",
                cursor_advanced_to=None,
            )
        # R0a: owner is paused/excluded ⇒ fall through to routing, noted loudly.
        nm = _name(owner.agent_id, agents)
        status = policy.status if policy else "unknown"
        prefix = f"owner {nm} unavailable ({status}); re-routing. "

    # --- R1: territory --------------------------------------------------------
    eligible = _available_agents(agents, params, exclude)
    if not eligible:
        return _held(fid, "held-no-agents", prefix + "no available agents — held for intake")

    territory_pool = [
        a
        for a in eligible
        if signals.state is not None
        and signals.state in params.assignment.agents[str(a.agent_id)].territory
    ]
    if territory_pool:
        pool = territory_pool
        territory_reason = f"territory: state={signals.state} → pool [{_names(pool)}]"
        rule = "territory"
    else:
        # No agent covers this state → the configured fallback (§4).
        if params.assignment.territory.fallback == "intake_park":
            return _held(
                fid,
                "held-territory-park",
                prefix
                + f"territory: state={signals.state or 'unknown'} uncovered → parked for intake",
            )
        pool = eligible
        territory_reason = (
            f"territory: state={signals.state or 'unknown'} uncovered → "
            f"fallback pool [{_names(pool)}]"
        )
        rule = "fallback"

    # --- R2: readiness / role -------------------------------------------------
    hot = is_hot(signals, params)
    role: Role = "closer" if hot else "qualifier"
    role_pool = [a for a in pool if params.assignment.agents[str(a.agent_id)].role == role]
    label = "hot/ready-to-deposit" if hot else "early-stage"
    if role_pool:
        pool = role_pool
        readiness_reason = f"readiness: {label} → {role} pool [{_names(pool)}]"
    else:
        # No agent of the wanted role in this territory pool → keep the pool
        # (never unrouted); the routed role becomes whatever the pool offers.
        roles_present = {params.assignment.agents[str(a.agent_id)].role for a in pool}
        role = "closer" if "closer" in roles_present else "qualifier"
        readiness_reason = (
            f"readiness: {label} preferred but no {('closer' if hot else 'qualifier')} "
            f"in territory pool; keeping [{_names(pool)}]"
        )

    # --- R3: income-tier signal (tiebreak / log only in v1; §6) --------------
    if signals.income_tier is None:
        income_reason = "income-tier: not on file — no income signal"
    elif signals.income_tier.value in params.assignment.income_routing.tefa_eligible_tiers:
        income_reason = (
            f"income-tier: {signals.income_tier.value} (TEFA-eligible) — "
            "prioritized; pool unchanged"
        )
    else:
        income_reason = (
            f"income-tier: {signals.income_tier.value} (full-pay) — noted; pool unchanged"
        )

    # --- R4: weighted round-robin (cap beats weight) over the surviving pool --
    pool_key = "|".join(sorted(str(a.agent_id) for a in pool))
    start = cursors.get(pool_key, 0)
    picked = _weighted_pick(pool, params, loads, start)
    over_cap = False
    if picked is None:
        # Every eligible agent is at capacity. The cap is a SOFT load-governance
        # preference, NOT a hard reject — a lead is NEVER left unrouted by a full
        # pool (A-32; the user's "everyone who comes in should be routed"). Route
        # over cap via the same weighted round-robin with caps ignored (empty loads
        # ⇒ every agent has headroom), logged as a load-governance breach so the
        # over-assignment is visible.
        picked = _weighted_pick(pool, params, {}, start)
        over_cap = True
    assert picked is not None  # pool is non-empty ⇒ the cap-ignoring pass always picks
    chosen, new_cursor = picked
    mode = params.assignment.round_robin.mode
    weights = {a.synthetic_name: params.assignment.agents[str(a.agent_id)].weight for a in pool}
    rr_reason = f"{mode} round-robin (w={weights}, cursor={start}) → {chosen.synthetic_name}"
    if over_cap:
        rr_reason += " [ALL at capacity — routed over cap; load-governance breach]"

    reason = prefix + "; ".join([territory_reason, readiness_reason, income_reason, rr_reason])
    return RoutingDecision(
        family_id=fid,
        agent_id=chosen.agent_id,
        routed_role=role,
        reason=reason,
        rule=rule,
        owner_match=False,
        pool_key=pool_key,
        cursor_advanced_to=new_cursor,
    )


# ---------------------------------------------------------------------------
# SLA reassignment predicate (the pure part of §9; the sweep is the I/O caller).
# ---------------------------------------------------------------------------


def is_sla_breached(
    assigned_at: datetime | None,
    last_contact_at: datetime | None,
    now: datetime,
    params: Params,
) -> bool:
    """True iff an assigned lead is UNWORKED past the SLA timer (§9).

    Unworked = assigned at least ``params.assignment.sla.unworked_reassign_days``
    ago AND no logged contact since it was assigned (``last_contact_at`` is None or
    predates ``assigned_at``). Pure: ``now`` is passed in (the cockpit's
    deterministic demo clock), never ``datetime.now()``. An unassigned lead
    (``assigned_at is None``) is not "unworked by its owner" — it is the intake
    pool's concern, so it never breaches here.
    """
    if assigned_at is None:
        return False
    threshold = timedelta(days=params.assignment.sla.unworked_reassign_days)
    if now - assigned_at < threshold:
        return False
    worked = last_contact_at is not None and last_contact_at >= assigned_at
    return not worked

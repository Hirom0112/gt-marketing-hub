"""The deterministic assignment ROUTER — the M4 rule-table (ASSUMPTIONS A-32).

`route_family(signals, agents, params)` routes one family to a sales-agent
**tier** + a concrete agent, from the EXISTING work-queue signals — no new
scoring math (MULTI_AGENT_COCKPIT §4 / A-32). It is part of the deterministic
core and stays PURE: a function of its typed inputs + params alone, with no LLM,
no adapter, no DB, and no ``now()``/random (the core-purity test guards the
import boundary; CLAUDE.md §3, INV-2). The write that PERSISTS an assignment is
the deterministic API route (``bulk-assign``); this module only DECIDES.

The rule table (A-32 — the authoritative spec). Tier eligibility: **closers** =
agents with ``rank <= params.assignment.closer_rank_max`` (demo = 1 ⇒ rank 1);
**setters** = ``rank > closer_rank_max``. A family routes to the **closer tier**
if ANY of R-1…R-4 holds (first-match precedence), else the **setter tier**
(R-5); R-6 is the within-tier selection:

- **R-1 deadline-imminent** → closer: ``at_risk`` OR
  ``days_remaining <= params.assignment.deadline_alarm_days``.
- **R-2 high-value** → closer: the family's RAW value (annual tuition × children
  — the work-queue value NUMERATOR, not the normalized term)
  ``>= params.assignment.high_value_threshold``.
- **R-3 multi-child household** → closer: ``> 1`` student in the household.
- **R-4 high-likelihood** → closer: ``recoverability >=
  params.assignment.high_likelihood_threshold``.
- **R-5 standard** → setter: none of R-1…R-4.
- **R-6 within-tier selection**: deterministic round-robin across the eligible
  agents of the chosen tier in ascending ``rank`` order, by the stable input
  ``index`` (no ``now()``/random — determinism). ``per_tier_load_cap`` is a
  tie/secondary preference (M3 roster), NOT a hard reject — a family is never
  left unrouted by a full tier (A-32).

Every threshold reads from ``params.assignment`` (INV-11): there is no routing
literal here, so the rule-table test fails if a param drifts.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.params import Params
from app.core.sales_agents import SalesAgent

# The two agent tiers (A-32 / MULTI_AGENT_COCKPIT §2.2). A tier is a TYPE, not a
# numeric tunable, so it is a code literal (INV-11 governs numbers); the cutoff
# BETWEEN tiers is the ``closer_rank_max`` param.
Tier = Literal["closer", "setter"]


class RoutingSignals(BaseModel):
    """The work-queue signals the router reads for ONE family (A-32; FR-2.5).

    A projection of the EXISTING deterministic signals (no new scoring math): the
    composite ``recoverability`` and the raw ``value``/``value_max`` from
    ``app.core.work_queue``, the voucher-deadline ``at_risk``/``days_remaining``
    from ``app.core.voucher`` (``deadline_proximity``), plus the household child
    count. Frozen so a routed family cannot mutate mid-decision.

    Attributes:
        recoverability: The composite recoverability/likelihood in [0,1]
            (``work_queue.recoverability``) — drives R-4.
        value: The RAW queue value NUMERATOR — annual tuition × children
            (``work_queue.value``), a dollar figure, NOT the normalized term —
            drives R-2.
        value_max: The value normalizer (``work_queue.value_max``); carried for
            completeness so a caller can render the normalized term — the router
            compares the RAW ``value`` against the dollar threshold (A-32 R-2).
        at_risk: Whether the family is in the "$X lost on a deadline" gap
            (``voucher_standing.at_risk``) — drives R-1.
        days_remaining: Days until the reconfirm/select deadline, or ``None`` when
            the family has no deadline (``voucher_standing.days_remaining``) —
            drives R-1.
        num_children: The household child count (the Interest form's "How many
            children?") — drives R-3.
    """

    model_config = ConfigDict(frozen=True)

    recoverability: float
    value: float
    value_max: float
    at_risk: bool = False
    days_remaining: int | None = None
    num_children: int = 1


def _is_closer_tier(signals: RoutingSignals, params: Params) -> bool:
    """True iff ANY of R-1…R-4 holds (first-match precedence → closer tier; A-32).

    Every threshold reads from ``params.assignment`` (INV-11) — no routing literal.
    """
    cfg = params.assignment
    # R-1 deadline-imminent: at-risk now, or the deadline is within the alarm window.
    if signals.at_risk:
        return True
    if signals.days_remaining is not None and signals.days_remaining <= cfg.deadline_alarm_days:
        return True
    # R-2 high-value: RAW value (tuition × children) at/above the dollar threshold.
    if signals.value >= cfg.high_value_threshold:
        return True
    # R-3 multi-child household: more than one student in the household.
    if signals.num_children > 1:
        return True
    # R-4 high-likelihood: recoverability at/above the likelihood threshold.
    if signals.recoverability >= cfg.high_likelihood_threshold:  # noqa: SIM103
        return True
    # R-5 standard: none of R-1…R-4 ⇒ the setter tier.
    return False


def _eligible_agents(
    tier: Tier, agents: tuple[SalesAgent, ...], params: Params
) -> list[SalesAgent]:
    """The agents in ``tier``, ascending ``rank`` (the R-6 round-robin order; A-32).

    The closer/setter split is the single ``closer_rank_max`` cutoff (INV-11):
    rank <= cutoff is a closer, else a setter.
    """
    cutoff = params.assignment.closer_rank_max
    if tier == "closer":
        members = [a for a in agents if a.rank <= cutoff]
    else:
        members = [a for a in agents if a.rank > cutoff]
    return sorted(members, key=lambda a: a.rank)


def route_family(
    signals: RoutingSignals,
    agents: tuple[SalesAgent, ...],
    params: Params,
    *,
    index: int = 0,
) -> tuple[Tier, UUID]:
    """Route one family to a ``(tier, agent_id)`` — the A-32 rule table (M4).

    PURE + DETERMINISTIC: no I/O, no ``now()``/random. The tier is chosen by the
    first-match R-1…R-5 precedence (:func:`_is_closer_tier`); the concrete agent
    is R-6 deterministic round-robin across the eligible tier in ascending
    ``rank`` order, indexed by the stable input ``index`` (modulo the tier size,
    so the family at input position ``index`` lands on a stable agent and a full
    tier still routes — never an unrouted family, A-32).

    Args:
        signals: The family's work-queue routing signals (no new scoring).
        agents: The static agent registry (``sales_agents.SALES_AGENTS``).
        params: Loaded params (§8); supplies the ``assignment`` thresholds + the
            ``closer_rank_max`` tier cutoff (INV-11).
        index: The family's stable position in the routed cohort — the
            round-robin selector within the chosen tier (default 0). Same
            ``(signals, index)`` ⇒ same agent, always.

    Returns:
        The chosen ``(tier, agent_id)``.

    Raises:
        ValueError: if the chosen tier has no eligible agents (a misconfigured
            registry — fail-closed rather than route to nobody).
    """
    tier: Tier = "closer" if _is_closer_tier(signals, params) else "setter"
    eligible = _eligible_agents(tier, agents, params)
    if not eligible:
        raise ValueError(
            f"no eligible agents in tier {tier!r} (check the registry / closer_rank_max)"
        )
    chosen = eligible[index % len(eligible)]
    return tier, chosen.agent_id

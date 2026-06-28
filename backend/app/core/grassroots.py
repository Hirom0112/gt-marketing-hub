"""Pure Grassroots-Engine derivations (Module 2; INV-2 / INV-6 / INV-11).

The deterministic core behind the Grassroots surface: given the roster, the referral
sprints, the market-map nodes, and the attributed-enrollment count, compute

1. the ambassador PIPELINE counts (Prospect → Outreached → Onboarded → Active →
   Champion),
2. the four GOAL-PROGRESS bars (active ambassadors / warm intros / p2p calls /
   influenced enrollments) vs the params targets,
3. the per-category MARKET-MAP coverage summary, and
4. per-sprint HEALTH (on_pace / behind), derived from the elapsed window vs the
   conversions, with the reference date ``as_of`` INJECTED (the core reads no clock —
   mirrors :mod:`app.core.budget`'s as_of injection).

This is the deterministic, *pure* core (mirrors :mod:`app.core.budget` /
:mod:`app.core.ambassador_reconcile`): a function of its inputs + the params dials
alone — no repository, adapter, decision-queue, httpx, or LLM import (the core-purity
test guards this). Every threshold/target is read from params (INV-11); nothing is a
code literal. Aggregate + adult-only (INV-6): segment/region/category are aggregate
labels and no child-keyed field ever enters here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------------------
# Pipeline stages — the closed ordered set (named wire tokens, not tunables; the
# INV-11 carve-out, like budget.HEALTH_*). The migration's CHECK mirrors these.
# ---------------------------------------------------------------------------
STAGE_PROSPECT = "prospect"
STAGE_OUTREACHED = "outreached"
STAGE_ONBOARDED = "onboarded"
STAGE_ACTIVE = "active"
STAGE_CHAMPION = "champion"

# Display/iteration order (Prospect → Champion).
PIPELINE_STAGES: tuple[str, ...] = (
    STAGE_PROSPECT,
    STAGE_OUTREACHED,
    STAGE_ONBOARDED,
    STAGE_ACTIVE,
    STAGE_CHAMPION,
)

# The stages that count as an "active ambassador" for the goal bar — an ambassador
# is active once they are ACTIVE or CHAMPION (a champion is a fortiori active).
ACTIVE_STAGES: frozenset[str] = frozenset({STAGE_ACTIVE, STAGE_CHAMPION})

# The sprint-health bands (named tokens — INV-11 carve-out, like budget.HEALTH_*).
SPRINT_ON_PACE = "on_pace"
SPRINT_BEHIND = "behind"
SPRINT_CLOSED = "closed"


# ---------------------------------------------------------------------------
# Core-local, source-agnostic views of the inputs. The store dataclasses are
# converted to these (or duck-typed against them) so the pure core never imports
# the store/adapter layer (the ambassador_reconcile.AmbassadorRecord pattern).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AmbassadorView:
    """One ambassador as the core reads it (synthetic/aggregate adult data; INV-1/INV-6).

    Attributes:
        status: The pipeline stage (one of :data:`PIPELINE_STAGES`).
        intros: Warm intros credited to this ambassador.
        p2p_calls: Peer-to-peer calls logged for this ambassador.
    """

    status: str
    intros: int = 0
    p2p_calls: int = 0


@dataclass(frozen=True, slots=True)
class SprintView:
    """One referral sprint as the core reads it (the sprint-health input).

    Attributes:
        window_start: The sprint window's first day.
        window_end: The sprint window's last day.
        families_identified: Families the sprint identified (the pace denominator).
        conversions: Conversions to date (the pace numerator).
        status: The sprint status (``planned`` / ``active`` / ``closed``).
    """

    window_start: date
    window_end: date
    families_identified: int
    conversions: int
    status: str = "active"


@dataclass(frozen=True, slots=True)
class NodeView:
    """One market-map node as the core reads it.

    Attributes:
        category: The aggregate community-category label (INV-6).
        status: The node status (``cold`` / ``outreach`` / ``in_conversation`` /
            ``active`` / ``closed``).
        leads_generated: Leads this node has generated.
    """

    category: str
    status: str
    leads_generated: int = 0


# A node is "contacted" once it has left the COLD state (any active outreach counts).
_COLD_STATUS = "cold"


@dataclass(frozen=True, slots=True)
class GoalBar:
    """One goal-progress bar — value vs target + the integer percent (no fake delta).

    Attributes:
        value: The current measured value.
        target: The params target (INV-11).
        pct: ``round(100 * value / target)`` clamped to ``[0, 100]`` (the bar never
            reads over 100% even when the value exceeds the target).
    """

    value: int
    target: int
    pct: int


@dataclass(frozen=True, slots=True)
class CategorySummary:
    """One market-map category's coverage row.

    Attributes:
        category: The aggregate category label.
        total: Total nodes in this category.
        contacted: Nodes that have left the COLD state.
        leads: Sum of ``leads_generated`` across the category's nodes.
        coverage_pct: ``round(100 * contacted / total)`` (0 for an empty category).
    """

    category: str
    total: int
    contacted: int
    leads: int
    coverage_pct: int


def _pct(value: int, target: int) -> int:
    """Integer percent of ``value`` against ``target``, clamped to ``[0, 100]``.

    Returns ``0`` for a non-positive target (the bar is undefined — never a div-by-0;
    params guards target ``>= 1`` so this only guards a degenerate caller).
    """
    if target <= 0:
        return 0
    return max(0, min(100, round(100 * value / target)))


def pipeline_counts(ambassadors: Iterable[AmbassadorView]) -> dict[str, int]:
    """Count ambassadors per pipeline stage (Prospect → Champion), zero-filled.

    Every stage in :data:`PIPELINE_STAGES` is present in the result (a stage with no
    ambassadors reads ``0``), in pipeline order, so the UI funnel never has a gap. An
    unknown status is ignored (the migration CHECK keeps the column to the known set).
    """
    counts = dict.fromkeys(PIPELINE_STAGES, 0)
    for amb in ambassadors:
        if amb.status in counts:
            counts[amb.status] += 1
    return counts


def goal_progress(
    ambassadors: Sequence[AmbassadorView],
    influenced_enrollments: int,
    *,
    target_active_ambassadors: int,
    target_warm_intros: int,
    target_p2p_calls: int,
    target_influenced_enrollments: int,
) -> dict[str, GoalBar]:
    """The four goal-progress bars vs the params targets (no fake deltas; INV-11).

    Each bar is value/target/pct only — a real measurement against the target, never a
    fabricated week-over-week delta (the honesty mandate):

    - ``active_ambassadors`` — count of ambassadors in :data:`ACTIVE_STAGES`.
    - ``warm_intros`` — sum of ``intros`` across the roster.
    - ``p2p_calls`` — sum of ``p2p_calls`` across the roster.
    - ``influenced_enrollments`` — the attributed-enrollment count passed in (see
      :func:`attribute_enrollments`).

    The targets are INJECTED (read from ``params.grassroots.targets`` at the API edge),
    so the core stays params-free and trivially testable.
    """
    active = sum(1 for a in ambassadors if a.status in ACTIVE_STAGES)
    warm_intros = sum(a.intros for a in ambassadors)
    p2p = sum(a.p2p_calls for a in ambassadors)
    return {
        "active_ambassadors": GoalBar(
            value=active,
            target=target_active_ambassadors,
            pct=_pct(active, target_active_ambassadors),
        ),
        "warm_intros": GoalBar(
            value=warm_intros,
            target=target_warm_intros,
            pct=_pct(warm_intros, target_warm_intros),
        ),
        "p2p_calls": GoalBar(
            value=p2p,
            target=target_p2p_calls,
            pct=_pct(p2p, target_p2p_calls),
        ),
        "influenced_enrollments": GoalBar(
            value=influenced_enrollments,
            target=target_influenced_enrollments,
            pct=_pct(influenced_enrollments, target_influenced_enrollments),
        ),
    }


def market_map_summary(nodes: Iterable[NodeView]) -> list[CategorySummary]:
    """Per-category market-map coverage (contacted/total, leads, coverage %).

    Groups nodes by ``category`` (first-seen order, deterministic over a stable input
    order) and rolls up: ``total`` nodes, ``contacted`` (nodes that have left the COLD
    state), ``leads`` (sum of ``leads_generated``), and ``coverage_pct`` =
    ``round(100 * contacted / total)``. Pure structural grouping — no threshold (INV-11
    governs numbers, not the contacted/cold definition).
    """
    order: list[str] = []
    totals: dict[str, int] = {}
    contacted: dict[str, int] = {}
    leads: dict[str, int] = {}
    for node in nodes:
        cat = node.category
        if cat not in totals:
            order.append(cat)
            totals[cat] = 0
            contacted[cat] = 0
            leads[cat] = 0
        totals[cat] += 1
        leads[cat] += node.leads_generated
        if node.status != _COLD_STATUS:
            contacted[cat] += 1
    return [
        CategorySummary(
            category=cat,
            total=totals[cat],
            contacted=contacted[cat],
            leads=leads[cat],
            coverage_pct=_pct(contacted[cat], totals[cat]),
        )
        for cat in order
    ]


def sprint_health(sprint: SprintView, *, as_of: date, behind_pace_frac: float) -> str:
    """Classify one sprint as on_pace / behind / closed (pace vs elapsed window) — pure.

    A CLOSED sprint reads :data:`SPRINT_CLOSED` (its pacing is moot). Otherwise the
    pace is linear: by ``as_of`` a sprint is EXPECTED to have converted
    ``families_identified * elapsed_frac`` families, where ``elapsed_frac`` is the
    fraction of the window elapsed (clamped to ``[0, 1]``). The sprint reads
    :data:`SPRINT_ON_PACE` when its actual ``conversions`` are at least
    ``behind_pace_frac`` of that expectation, else :data:`SPRINT_BEHIND`.

    ``as_of`` is INJECTED (the core reads no clock). A zero/negative-length window (or
    an ``as_of`` before the window) yields ``elapsed_frac`` clamped so the expectation
    is never negative and the result never divides by zero.
    """
    if sprint.status == SPRINT_CLOSED:
        return SPRINT_CLOSED

    span_days = (sprint.window_end - sprint.window_start).days
    if span_days <= 0:
        # A point/degenerate window — treat as fully elapsed (the whole goal is due).
        elapsed_frac = 1.0
    else:
        elapsed_days = (as_of - sprint.window_start).days
        elapsed_frac = max(0.0, min(1.0, elapsed_days / span_days))

    expected = sprint.families_identified * elapsed_frac
    if expected <= 0:
        # Nothing is expected yet (window not started / no families) ⇒ on pace.
        return SPRINT_ON_PACE
    if sprint.conversions >= behind_pace_frac * expected:
        return SPRINT_ON_PACE
    return SPRINT_BEHIND


def attribute_enrollments(
    sprints: Iterable[SprintView],
    events: Iterable[object] | None = None,
) -> int:
    """The grassroots-INFLUENCED enrollment count (with attribution) — a documented stand-in.

    The honest attribution path would JOIN ``app_form.attribution_source`` /
    referral-code back to the ambassador who drove each enrollment and DEDUPE a family
    counted by two sources (the :mod:`app.core.ambassador_reconcile` dedup idea). That
    join data is NOT readily available on the synthetic Grassroots slice, so this is a
    DOCUMENTED stand-in: the influenced-enrollment count is the sum of the referral
    sprints' recorded ``conversions`` (each sprint's conversions are the families it
    converted). ``events`` is accepted for forward-compatibility (a future path could
    corroborate via ``ambassador_event.conversions_influenced``) but is NOT summed here
    to avoid double-counting a conversion already credited to a sprint.

    Deterministic and side-effect free. Returns ``0`` for an empty sprint set.
    """
    _ = events  # forward-compat hook; deliberately not summed (see docstring).
    return sum(s.conversions for s in sprints)

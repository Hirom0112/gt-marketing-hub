"""Later-lifecycle nurture policy — pure, params-driven (INV-11).

The cockpit owns the nurture POLICY (cadence, school-year re-engagement windows);
HubSpot owns nurture EXECUTION (the drip sends). This module is the deterministic
core for the policy half: it computes, for a given day, how hard a parked family's
re-engagement should ramp because a school-year window is approaching.

Pure: imports only the typed params model + stdlib (the core-purity test guards
this). No I/O, no LLM, no clock — ``today`` is passed in so it is fully testable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.params import NurtureAnchor, PresumedLost
from app.observability.log_store import NO_RESPONSE_DISPOSITIONS, ContactOutcomeRecord


@dataclass(frozen=True)
class AnchorPressure:
    """The strongest school-year re-engagement signal for a given day.

    ``pressure`` ∈ [0,1] rises from 0 (≥ ``ramp_days`` before the nearest anchor) to
    1.0 ON the anchor date; ``anchor`` names which window drove it (None at zero).
    """

    pressure: float
    anchor: str | None


def _days_until_next(today: date, month: int, day: int) -> int | None:
    """Whole days from ``today`` to the next yearly occurrence of (month, day).

    0 when today IS the anchor date; rolls to next year once this year's date has
    passed. Returns None for an impossible calendar date (e.g. Feb 30) so a bad
    anchor is skipped rather than crashing the deriver.
    """
    for year in (today.year, today.year + 1):
        try:
            occ = date(year, month, day)
        except ValueError:
            return None
        if occ >= today:
            return (occ - today).days
    return None


def anchor_pressure(today: date, anchors: list[NurtureAnchor]) -> AnchorPressure:
    """The max re-engagement pressure across all anchors for ``today``.

    For each anchor, pressure is ``1 - days_until / ramp_days`` while within the
    ramp window (and ``ramp_days > 0``), else 0. The strongest wins; ties keep the
    first anchor in config order.
    """
    best = AnchorPressure(0.0, None)
    for a in anchors:
        days = _days_until_next(today, a.month, a.day)
        if days is None or a.ramp_days <= 0 or days > a.ramp_days:
            continue
        pressure = 1.0 - days / a.ramp_days
        if pressure > best.pressure:
            best = AnchorPressure(pressure, a.name)
    return best


def is_cold(*, stall_date: datetime, now: datetime, cold_after_days: int) -> bool:
    """Whether a stalled family has gone COLD — stalled longer than the threshold.

    True once ``now - stall_date`` reaches ``cold_after_days`` (inclusive boundary).
    COLD is a more-urgent STALLED (still active — an annotation, not a removal); the
    recency precedence (a contacted family is WORKING, not COLD) is the recovery
    deriver's job — this only decides the age threshold. ``stall_date`` is the API
    layer's derived stall-anchor; ``now`` is read once per request (INV-2: the pure
    core never reads a clock).
    """
    return (now - stall_date) >= timedelta(days=cold_after_days)


def count_no_response(
    outcomes: Iterable[ContactOutcomeRecord], *, now: datetime, within_days: int
) -> int:
    """How many no-response contact attempts fall within the trailing window.

    Counts only the no-response dispositions (the silence that accrues toward
    presumed-lost — :data:`NO_RESPONSE_DISPOSITIONS`); a live ``REACHED`` contact or
    a payment commitment is not silence. Window is ``[now - within_days, now]`` on
    each outcome's ``created_at``, so old attempts age out.
    """
    cutoff = now - timedelta(days=within_days)
    return sum(
        1 for o in outcomes if o.disposition in NO_RESPONSE_DISPOSITIONS and o.created_at >= cutoff
    )


def is_presumed_lost(
    outcomes: Iterable[ContactOutcomeRecord], policy: PresumedLost, *, now: datetime
) -> bool:
    """Whether a family should be SURFACED as 'presumed lost' (a human then confirms).

    True once ``policy.after_attempts`` no-response attempts have accrued within
    ``policy.within_days``. This only raises the suggestion — it never removes the
    family; ``policy.requires_human_confirm`` gates the actual LOST transition at the
    API layer (the machine never auto-drops a warm lead).
    """
    return (
        count_no_response(outcomes, now=now, within_days=policy.within_days)
        >= policy.after_attempts
    )


# ===========================================================================
# Module 5 (Nurture & Lifecycle) view derivations — the 6 sub-views' pure cores.
# All aggregate + PII-free (INV-1/INV-6); every threshold/label set is INJECTED from
# ``params.nurture.lifecycle`` (INV-11). ``now`` is injected (the core reads no clock).
# ===========================================================================

# Planning tiers — the closed set (named wire tokens, not tunables; the INV-11
# carve-out). The 0040 ``nurture_segment`` CHECK mirrors these.
TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_T3 = "T3"
TIERS: tuple[str, ...] = (TIER_T1, TIER_T2, TIER_T3)


def _pct(numerator: int, denominator: int) -> int:
    """Integer percent of ``numerator`` against ``denominator``, clamped to ``[0, 100]``.

    Returns ``0`` for a non-positive denominator (the rate is undefined — never a
    div-by-0).
    """
    if denominator <= 0:
        return 0
    return max(0, min(100, round(100 * numerator / denominator)))


# ---------------------------------------------------------------- 1) tier_mix
@dataclass(frozen=True, slots=True)
class TierMix:
    """The engagement-tier mix rollup (the 5a hero figure).

    Attributes:
        clicked: Contacts in the top (clicked) tier.
        opened: Contacts in the middle (opened) tier.
        cold: Contacts in the cold tier (never opened/clicked).
        total: clicked + opened + cold (the denominator).
        reachable: clicked + opened — the contacts still ENGAGING (reachable).
        reachability_pct: ``round(100 * reachable / total)`` (0 when none).
    """

    clicked: int
    opened: int
    cold: int
    total: int
    reachable: int
    reachability_pct: int


def tier_mix(clicked: int, opened: int, cold: int) -> TierMix:
    """The engagement-tier mix — reachability COMPUTED, never faked.

    Reachability is the share still engaging (clicked + opened) over the whole read
    cohort; a cold contact is unreachable by email. Aggregate only (counts; INV-6).
    """
    total = clicked + opened + cold
    reachable = clicked + opened
    return TierMix(
        clicked=clicked,
        opened=opened,
        cold=cold,
        total=total,
        reachable=reachable,
        reachability_pct=_pct(reachable, total),
    )


# -------------------------------------------- 2) engagement_attribute_heatmap
@dataclass(frozen=True, slots=True)
class HeatmapFamily:
    """One family's synthetic engagement tier + real aggregate attributes (INV-6).

    Attributes:
        engagement_tier: The family's engagement tier (one of the params tier labels).
        attributes: dimension label → aggregate BUCKET value (income tier / region /
            persona / grade band). Never PII — bucket labels only.
        converted: Whether the family reached a handoff/enroll outcome (the matrix's
            numerator) — the genuine conversion signal, not a fabricated rate.
    """

    engagement_tier: str
    attributes: Mapping[str, str]
    converted: bool


@dataclass(frozen=True, slots=True)
class HeatmapCell:
    """One cell of the engagement-tier × attribute-bucket conversion matrix."""

    engagement_tier: str
    attribute_value: str
    total: int
    converted: int
    conversion_pct: int


def engagement_attribute_heatmap(
    families: Iterable[HeatmapFamily],
    *,
    tiers: Sequence[str],
    dimensions: Sequence[str],
) -> dict[str, list[HeatmapCell]]:
    """Per-dimension conversion% matrix of engagement tier × attribute bucket — pure.

    For each attribute ``dimension`` the result is the FULL grid: one row per tier (in
    the injected ``tiers`` order) × one column per observed bucket value (sorted), with
    ``conversion_pct = round(100 * converted / total)`` computed from the data (0 when a
    cell is empty). The insight (which engagement tier × income/geo/… band converts
    best) is therefore BAKED from the rows, never hardcoded. Aggregate only (INV-6).
    """
    rows = list(families)
    result: dict[str, list[HeatmapCell]] = {}
    for dim in dimensions:
        tally: dict[tuple[str, str], list[int]] = {}
        values: set[str] = set()
        for fam in rows:
            value = str(fam.attributes.get(dim, "unknown"))
            values.add(value)
            cell = tally.setdefault((fam.engagement_tier, value), [0, 0])
            cell[0] += 1
            if fam.converted:
                cell[1] += 1
        ordered_values = sorted(values)
        cells: list[HeatmapCell] = []
        for tier in tiers:
            for value in ordered_values:
                total, converted = tally.get((tier, value), [0, 0])
                cells.append(
                    HeatmapCell(
                        engagement_tier=tier,
                        attribute_value=value,
                        total=total,
                        converted=converted,
                        conversion_pct=_pct(converted, total),
                    )
                )
        result[dim] = cells
    return result


# ----------------------------------------------------------- 3) sla_compliance
@dataclass(frozen=True, slots=True)
class SlaContactView:
    """One first-contact SLA timer row as the core reads it (synthetic; INV-1).

    Attributes:
        applicant_label: A SYNTHETIC applicant token (never PII).
        entered_at: When the applicant entered the SLA queue.
        contacted_at: When first contacted, or ``None`` if not yet contacted.
        owner: The owning rep/workstream token (not PII).
    """

    applicant_label: str
    entered_at: datetime
    contacted_at: datetime | None
    owner: str


@dataclass(frozen=True, slots=True)
class SlaLateItem:
    """One late SLA contact (a breach) — the 5f red list row."""

    applicant_label: str
    owner: str
    hours_waiting: int
    contacted: bool


@dataclass(frozen=True, slots=True)
class SlaOwnerStat:
    """One owner's SLA compliance (the per-owner breakdown row)."""

    owner: str
    total: int
    in_window: int
    compliance_pct: int


@dataclass(frozen=True, slots=True)
class SlaCompliance:
    """The SLA-compliance rollup (the 5f figures)."""

    total: int
    in_window: int
    pending: int
    compliance_pct: int
    late: list[SlaLateItem]
    per_owner: list[SlaOwnerStat]


def sla_compliance(
    contacts: Iterable[SlaContactView],
    *,
    now: datetime,
    window_hours: int,
) -> SlaCompliance:
    """First-contact SLA compliance — ``now`` INJECTED, every figure computed (INV-2).

    A contact is ``in_window`` when it was contacted within ``window_hours`` of entering.
    A contact is LATE when it was contacted AFTER the window, OR it is still uncontacted
    and the window has already elapsed (``now - entered > window``). An uncontacted
    contact still inside its window is ``pending`` (on time, not yet late). The headline
    ``compliance_pct`` is ``in_window / total``; ``per_owner`` repeats the split per
    owner (first-appearance order, deterministic).
    """
    window = timedelta(hours=window_hours)
    rows = list(contacts)
    total = len(rows)
    in_window = 0
    pending = 0
    late: list[SlaLateItem] = []
    owner_tally: dict[str, list[int]] = {}
    owner_order: list[str] = []

    for c in rows:
        if c.owner not in owner_tally:
            owner_order.append(c.owner)
            owner_tally[c.owner] = [0, 0]
        owner_tally[c.owner][0] += 1

        if c.contacted_at is not None:
            delay = c.contacted_at - c.entered_at
            if delay <= window:
                in_window += 1
                owner_tally[c.owner][1] += 1
            else:
                late.append(
                    SlaLateItem(
                        applicant_label=c.applicant_label,
                        owner=c.owner,
                        hours_waiting=int(delay.total_seconds() // 3600),
                        contacted=True,
                    )
                )
        else:
            waited = now - c.entered_at
            if waited > window:
                late.append(
                    SlaLateItem(
                        applicant_label=c.applicant_label,
                        owner=c.owner,
                        hours_waiting=int(waited.total_seconds() // 3600),
                        contacted=False,
                    )
                )
            else:
                pending += 1

    per_owner = [
        SlaOwnerStat(
            owner=owner,
            total=owner_tally[owner][0],
            in_window=owner_tally[owner][1],
            compliance_pct=_pct(owner_tally[owner][1], owner_tally[owner][0]),
        )
        for owner in owner_order
    ]
    return SlaCompliance(
        total=total,
        in_window=in_window,
        pending=pending,
        compliance_pct=_pct(in_window, total),
        late=late,
        per_owner=per_owner,
    )


# ------------------------------------------------------ 4) pipeline_distribution
@dataclass(frozen=True, slots=True)
class PipelineStageView:
    """One pipeline stage's aggregate as the core reads it (counts only; INV-6).

    Attributes:
        stage: The deal-stage label.
        count: Deals currently in this stage.
        stuck: Deals in this stage idle beyond the stuck-in-stage window (aggregate).
    """

    stage: str
    count: int
    stuck: int


@dataclass(frozen=True, slots=True)
class PipelineStageDist:
    """One stage's distribution row (count + share + stuck)."""

    stage: str
    count: int
    pct: int
    stuck: int


@dataclass(frozen=True, slots=True)
class PipelineDistribution:
    """The pipeline-distribution rollup (the 5c figures)."""

    stages: list[PipelineStageDist]
    total: int
    stuck_total: int
    velocity_pct: int


def pipeline_distribution(
    stages: Iterable[PipelineStageView],
    *,
    stage_order: Sequence[str],
) -> PipelineDistribution:
    """Per-stage distribution + stuck total + velocity (progression) rate — pure.

    Stages are rendered in the injected ``stage_order`` (a stage absent from the input is
    a zero row; an input stage outside the order is appended after, deterministically).
    ``velocity_pct`` is the PROGRESSION rate: the share of deals that have moved beyond
    the FIRST stage (``(total - first_stage_count) / total``) — a deal still sitting in
    the first stage has not progressed. ``stuck_total`` sums the per-stage stuck counts.
    """
    by_stage = {s.stage: s for s in stages}
    ordered: list[str] = list(stage_order)
    for s in by_stage:
        if s not in ordered:
            ordered.append(s)

    total = sum(s.count for s in by_stage.values())
    stuck_total = sum(s.stuck for s in by_stage.values())
    rows: list[PipelineStageDist] = []
    for stage in ordered:
        view = by_stage.get(stage)
        count = view.count if view is not None else 0
        stuck = view.stuck if view is not None else 0
        rows.append(
            PipelineStageDist(stage=stage, count=count, pct=_pct(count, total), stuck=stuck)
        )

    first_count = rows[0].count if rows else 0
    velocity_pct = _pct(total - first_count, total)
    return PipelineDistribution(
        stages=rows,
        total=total,
        stuck_total=stuck_total,
        velocity_pct=velocity_pct,
    )


# ---------------------------------------------------------- 5) handoff_metrics
@dataclass(frozen=True, slots=True)
class HandoffMetrics:
    """The marketing→onboarding handoff rollup (the 5c handoff figures)."""

    weekly: int
    monthly: int
    cumulative: int
    total_deals: int
    conversion_pct: int


def handoff_metrics(
    stages: Iterable[PipelineStageView],
    *,
    handoff_stages: Sequence[str],
    handoff_week: int,
    handoff_month: int,
) -> HandoffMetrics:
    """Weekly/monthly/cumulative handoff counts + the handoff conversion rate — pure.

    ``cumulative`` is the count of deals that have reached ANY handoff stage (e.g.
    enroll/tuition); ``conversion_pct`` is that over the whole pipeline. The windowed
    ``weekly``/``monthly`` counts come from the dated handoff aggregate at the edge
    (deals that ENTERED a handoff stage within the look-back), so the core stays
    clock-free. Aggregate only (counts; INV-6).
    """
    handoff = set(handoff_stages)
    rows = list(stages)
    cumulative = sum(s.count for s in rows if s.stage in handoff)
    total = sum(s.count for s in rows)
    return HandoffMetrics(
        weekly=handoff_week,
        monthly=handoff_month,
        cumulative=cumulative,
        total_deals=total,
        conversion_pct=_pct(cumulative, total),
    )


# ------------------------------------------------------------- 6) sms_theme_tag
def _keyword_tags(message: str, keyword_rules: Mapping[str, Sequence[str]]) -> list[str]:
    """The v1 KEYWORD tags: every theme whose keyword set matches ``message`` (sorted)."""
    low = message.lower()
    tags = [
        theme
        for theme, keywords in keyword_rules.items()
        if any(str(k).lower() in low for k in keywords)
    ]
    return sorted(tags)


def sms_theme_tag(
    message: str,
    *,
    keyword_rules: Mapping[str, Sequence[str]],
    llm_tagger: Callable[[str], Sequence[str] | None] | None = None,
) -> tuple[list[str], str]:
    """Tag one inbound SMS with theme labels — keyword v1, optional LLM layer (INV-2).

    The v1 path is deterministic KEYWORD rules (``keyword_rules`` is the params home).
    An OPTIONAL ``llm_tagger`` (injected, never imported — the core stays pure) may
    propose richer tags, but it DEGRADES to the keyword path on ANY failure or a ``None``
    result — the LLM is a proposal only (INV-2), never load-bearing. Returns
    ``(tags, mode)`` where ``mode`` is ``"llm"`` or ``"keyword"`` so the UI labels the
    provenance honestly.
    """
    if llm_tagger is not None:
        try:
            proposed = llm_tagger(message)
            if proposed:
                return (sorted({str(t) for t in proposed}), "llm")
        except Exception:  # noqa: BLE001 - degrade to keyword on ANY llm failure (INV-2)
            pass
    return (_keyword_tags(message, keyword_rules), "keyword")


# ----------------------------------------------------------- 7) sequence_health
@dataclass(frozen=True, slots=True)
class SequenceStepView:
    """One sequence step's performance as the core reads it (synthetic mirror)."""

    step: int
    open_pct: float
    click_pct: float


def sequence_health(
    steps: Iterable[SequenceStepView],
    *,
    min_open_pct: float,
    min_click_pct: float,
) -> bool:
    """Return ``True`` when a sequence is UNHEALTHY (below the open/click floors) — pure.

    A sequence whose AVERAGE open rate is below ``min_open_pct`` OR whose average click
    rate is below ``min_click_pct`` flags unhealthy. A sequence with NO steps flags
    unhealthy (there is nothing performing). Both floors are PERCENTS (0–100), injected
    from params (INV-11).
    """
    rows = list(steps)
    if not rows:
        return True
    avg_open = sum(s.open_pct for s in rows) / len(rows)
    avg_click = sum(s.click_pct for s in rows) / len(rows)
    return avg_open < min_open_pct or avg_click < min_click_pct


# ----------------------------------------------------------- 8) segment_builder
@dataclass(frozen=True, slots=True)
class SegmentCandidate:
    """One candidate in the segment-builder population (synthetic tier + attributes)."""

    engagement_tier: str
    attributes: Mapping[str, str]


def segment_builder(
    population: Iterable[SegmentCandidate],
    *,
    engagement_tiers: Sequence[str] | None = None,
    attribute_filters: Mapping[str, Sequence[str]] | None = None,
) -> int:
    """Count the candidates matching an engagement × attribute segment — pure.

    A candidate matches when its engagement tier is in ``engagement_tiers`` (``None`` ⇒
    any tier) AND, for every dimension in ``attribute_filters``, its attribute value is
    in that dimension's allowed set. An empty/None filter set matches everyone. The
    returned count is the segment's audience size (the 5b builder output).
    """
    tier_set = set(engagement_tiers) if engagement_tiers else None
    filters = attribute_filters or {}
    size = 0
    for cand in population:
        if tier_set is not None and cand.engagement_tier not in tier_set:
            continue
        if any(cand.attributes.get(dim) not in set(allowed) for dim, allowed in filters.items()):
            continue
        size += 1
    return size

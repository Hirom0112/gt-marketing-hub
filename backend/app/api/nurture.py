"""Nurture & Lifecycle endpoints (Module 5) — the 6 sub-views + 4 cross-links.

The composition layer that wires the pure Nurture core (:mod:`app.core.nurture`), the
store seam (:mod:`app.data.nurture_store`, migration 0040), the CRM adapter's AGGREGATE
reads (engagement-tier mix + deal-pipeline distribution — INV-6), and the Supabase
source-of-truth family attributes behind REST. Thin by design: the math is pure/owned
core (INV-2); this router only orchestrates, gates writes by owner, and adapts shapes.

DATA-SOURCE SPLIT (honored exactly, surfaced via a ``source`` label per response):
- LIVE HubSpot (aggregate-only): engagement-tier mix + pipeline-stage distribution +
  handoff — via the CRM adapter (simulate seam offline).
- Supabase source-of-truth: family attributes (income tier / region) for the heatmap
  join + the SLA denominator.
- Supabase synthetic mirror (0040, labeled ``synthetic_mirror``): sequences (read-only —
  the Sales-Hub Sequences API is unavailable in this portal), SMS inbox, segments,
  SLA contact log.

The 6 sub-views (5a overview / 5b segments / 5c pipeline / 5d sequences / 5e sms / 5f
sla) plus 4 cross-links: hot-family → Decision Queue (B2 feeder), objection → a Content
calendar DRAFT brief, the KPI feed for the Dashboard module, and the per-piece
attribution feed for Content Performance.

This module may import ``app.api`` / ``app.core`` (it is the composition root);
``app/core/`` stays pure. No live external send is ever made here. INV-1/INV-6: every
label is synthetic/aggregate; ``raised_by``/``owner`` come from the VERIFIED principal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.decisions import DecisionResponse, _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_content_metrics_store,
    get_crm_adapter_dep,
    get_decisions_store,
    get_nurture_store,
    get_params,
    get_principal,
    get_repository,
)
from app.core import nurture as core
from app.core.content_analytics import STATUS_DRAFT
from app.core.params import Params
from app.core.program import Program
from app.data.content_metrics_store import ContentMetricsStore
from app.data.decisions_store import DecisionsStore
from app.data.nurture_store import NurtureStore
from app.data.repository import FamilyRepository

router = APIRouter(tags=["nurture"])

# The Decision-Queue workstream every nurture escalation belongs to (one of
# decisions_store.WORKSTREAMS). Named, not a bare literal (INV-11 carve-out).
NURTURE_WORKSTREAM = "nurture"
# The source tag a hot-family SMS escalation Decision-Queue item carries.
NURTURE_HOT_FAMILY_SOURCE = "sms_hot_family"

# The owner-routing token an OPERATOR must own to WRITE nurture state. A LEADER/ADMIN may
# write any; the demo operator owns ``grassroots`` only, so nurture writes are admin/leader-
# only in the demo. Named wire tokens, not tunables (INV-11 carve-out, mirroring field_events).
NURTURE_OWNER_WORKSTREAM = "nurture"
DEMO_OPERATOR_WORKSTREAM = "grassroots"
OPERATOR_WORKSTREAMS: dict[str, str] = {}

# The synthetic-mirror provenance label every sequence row carries (the Sequences API is
# unavailable in this portal — INV-9 honesty mandate). A fixed wire token (INV-11 carve-out).
SOURCE_SYNTHETIC_MIRROR = "synthetic_mirror"
# The LIVE-aggregate provenance label engagement/pipeline reads carry.
SOURCE_CRM_AGGREGATE = "crm_aggregate"

# The heatmap attribute dimensions joined from the Supabase source-of-truth FamilyRecord
# (aggregate bucket labels only — INV-6). persona/grade are not on the family record, so
# the honest heatmap covers the available aggregate dims (income tier + region).
_HEATMAP_DIMENSIONS: tuple[str, ...] = ("income", "region")

# Dependency aliases (Annotated keeps the call in the type — ruff B008).
StoreDep = Annotated[NurtureStore, Depends(get_nurture_store)]
CrmDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
RepoDep = Annotated[FamilyRepository, Depends(get_repository)]
ContentStoreDep = Annotated[ContentMetricsStore, Depends(get_content_metrics_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]


# ===========================================================================
# Owner gate — copied from app.api.field_events (the IDOR/spoof posture, INV-1).
# ===========================================================================
def _operator_workstream(principal: Principal) -> str:
    """The single workstream an OPERATOR owns (keyed by the verified agent_id only)."""
    if principal.agent_id is not None:
        return OPERATOR_WORKSTREAMS.get(str(principal.agent_id), DEMO_OPERATOR_WORKSTREAM)
    return DEMO_OPERATOR_WORKSTREAM


def _require_nurture_owner(principal: Principal) -> None:
    """OWNER gate for every nurture WRITE — 403 on a deny.

    A LEADER/ADMIN may write any workstream. An OPERATOR may write ONLY when the
    workstream they own is ``nurture``; the demo operator owns ``grassroots``, so nurture
    writes are admin/leader-only in the demo. The verified ROLE decides — never a client claim.
    """
    if principal.role in ("admin", "leader"):
        return
    if _operator_workstream(principal) != NURTURE_OWNER_WORKSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"operator does not own the {NURTURE_OWNER_WORKSTREAM!r} workstream",
        )


# ===========================================================================
# Helpers — synthetic engagement tier + the heatmap/segment family rows.
# ===========================================================================
def _synthetic_tier(family_id: UUID, tiers: list[str]) -> str:
    """A DETERMINISTIC synthetic engagement tier per family (matches the simulate seam).

    Per INV-6 the per-family engagement tier is NEVER read per-person from HubSpot; it is
    synthesized deterministically from the family id (the same posture as the simulated
    CRM adapter's mix), then joined to the family's REAL aggregate attributes.
    """
    return tiers[family_id.int % len(tiers)] if tiers else "cold"


def _heatmap_families(
    repo: FamilyRepository, *, tiers: list[str], handoff_stages: set[str]
) -> list[core.HeatmapFamily]:
    """Build the per-family (synthetic tier × real aggregate attribute) heatmap rows.

    Attributes are joined from the Supabase source-of-truth FamilyRecord (income tier +
    region — aggregate bucket labels, never PII; INV-6). ``converted`` is the genuine
    signal: the family reached a handoff (enroll/tuition) stage.
    """
    rows: list[core.HeatmapFamily] = []
    for fam in repo.list_families():
        income = fam.income_tier.value if fam.income_tier is not None else "unknown"
        region = fam.state or "unknown"
        rows.append(
            core.HeatmapFamily(
                engagement_tier=_synthetic_tier(fam.family_id, tiers),
                attributes={"income": income, "region": region},
                converted=fam.current_stage.value in handoff_stages,
            )
        )
    return rows


def _cohort_family_ids(repo: FamilyRepository) -> list[UUID]:
    """The active program's cohort family ids (the aggregate-read cohort)."""
    return [fam.family_id for fam in repo.list_families()]


def _pipeline_views(
    crm: CRMAdapter, repo: FamilyRepository, params: Params, *, now: datetime
) -> tuple[list[core.PipelineStageView], int, int]:
    """Read the LIVE/aggregate pipeline snapshot and adapt it to the core stage views."""
    lc = params.nurture.lifecycle
    snapshot = crm.read_pipeline_snapshot(
        _cohort_family_ids(repo),
        stage_order=lc.pipeline_stage_order,
        handoff_stages=lc.handoff_stages,
        now=now,
        stuck_days=lc.stuck_in_stage_days,
        week_days=lc.week_days,
        month_days=lc.month_days,
    )
    views = [
        core.PipelineStageView(stage=s.stage, count=s.count, stuck=s.stuck) for s in snapshot.stages
    ]
    return views, snapshot.handoff_week, snapshot.handoff_month


# ===========================================================================
# Wire models.
# ===========================================================================
class TierMixModel(BaseModel):
    clicked: int
    opened: int
    cold: int
    total: int
    reachable: int
    reachability_pct: int


class TierPanel(BaseModel):
    tier: str
    audience_size: int
    reachability_pct: int
    planning_size: int
    segment_count: int


class HeatmapCellModel(BaseModel):
    engagement_tier: str
    attribute_value: str
    total: int
    converted: int
    conversion_pct: int


class PipelineStageModel(BaseModel):
    stage: str
    count: int
    pct: int
    stuck: int


class HandoffModel(BaseModel):
    weekly: int
    monthly: int
    cumulative: int
    total_deals: int
    conversion_pct: int


class OverviewResponse(BaseModel):
    """The 5a overview rollup (every figure computed, never faked)."""

    tiers: list[TierPanel]
    engagement_tier_mix: TierMixModel
    engagement_source: str
    sequences_total: int
    sequences_healthy: int
    top_sequence: str | None
    sla_compliance_pct: int
    sms_reply_count_this_week: int
    sms_replied_total: int
    cold_segment_count: int
    pipeline_stage_distribution: list[PipelineStageModel]
    handoff_this_week: int
    engagement_attribute_crosstab: list[HeatmapCellModel]


class SegmentModel(BaseModel):
    segment_id: UUID
    tier: str
    sub_bucket: str
    label: str
    attribute_filters: dict[str, Any]
    size: int
    reachability_pct: float
    owner: str
    notes: str


class SegmentsResponse(BaseModel):
    """The 5b segments view — T1/T2/T3 panels + segments + the engagement×attribute heatmap."""

    tiers: list[TierPanel]
    segments: list[SegmentModel]
    heatmap: dict[str, list[HeatmapCellModel]]
    source: str


class SegmentBuildRequest(BaseModel):
    """Body for ``POST /nurture/segments/build`` — owner-gated segment builder.

    There is DELIBERATELY no ``owner`` field: the row is stamped with the nurture owner
    token from the verified principal (INV-1).
    """

    tier: str = Field(min_length=1)
    sub_bucket: str = ""
    label: str = ""
    engagement_tiers: list[str] | None = None
    attribute_filters: dict[str, list[str]] = Field(default_factory=dict)
    notes: str = ""


class PipelineResponse(BaseModel):
    """The 5c pipeline view — stage distribution + stuck + velocity + handoff (LIVE)."""

    stages: list[PipelineStageModel]
    total: int
    stuck_total: int
    velocity_pct: int
    handoff: HandoffModel
    source: str


class SequenceStepModel(BaseModel):
    step: int
    open_pct: float
    click_pct: float
    conversion_pct: float


class SequenceModel(BaseModel):
    sequence_id: UUID
    name: str
    seq_type: str
    audience_size: int
    step_count: int
    steps: list[SequenceStepModel]
    health_flag: bool
    status: str


class SequencesResponse(BaseModel):
    sequences: list[SequenceModel]
    source: str


class SmsThreadModel(BaseModel):
    thread_id: UUID
    contact_label: str
    last_message: str
    theme_tags: list[str]
    tag_mode: str
    status: str
    replied: bool
    inbound_at: datetime | None


class SmsResponse(BaseModel):
    threads: list[SmsThreadModel]
    source: str


class ObjectionBriefRequest(BaseModel):
    """Body for ``POST /nurture/sms/objection-brief`` — a content-brief DRAFT stub."""

    theme: str = Field(min_length=1)
    title: str = ""


class ObjectionBriefResponse(BaseModel):
    entry_id: UUID
    title: str
    channel: str
    status: str


class SlaLateModel(BaseModel):
    applicant_label: str
    owner: str
    hours_waiting: int
    contacted: bool


class SlaOwnerModel(BaseModel):
    owner: str
    total: int
    in_window: int
    compliance_pct: int


class SlaResponse(BaseModel):
    """The 5f SLA view — applicants today, 24h compliance, late list, per-owner, history."""

    total: int
    applicants_today: int
    compliance_pct: int
    pending: int
    late: list[SlaLateModel]
    per_owner: list[SlaOwnerModel]
    history_30d_count: int
    window_hours: int
    source: str


class KpiFeedResponse(BaseModel):
    """The 5→Dashboard KPI feed (pipeline stage dist + handoff count) [CROSS-LINK 3]."""

    pipeline_stage_distribution: list[PipelineStageModel]
    handoff: HandoffModel
    source: str


class AttributionRow(BaseModel):
    piece_title: str
    channel: str
    conversions: int
    conversion_pct: int
    utm_attributed: bool


class AttributionResponse(BaseModel):
    """The per-piece conversion attribution feeding Content Performance [CROSS-LINK 4]."""

    pieces: list[AttributionRow]
    unattributable_count: int
    source: str


# ===========================================================================
# Internal builders (shared by overview + the sub-views).
# ===========================================================================
def _tier_panels(store: NurtureStore, program: Program, params: Params) -> list[TierPanel]:
    """Per-tier (T1/T2/T3) audience size + size-weighted reachability + planning target."""
    planning = params.nurture.lifecycle.tier_planning_sizes
    panels: list[TierPanel] = []
    segments = store.list_segments(program)
    for tier in core.TIERS:
        rows = [s for s in segments if s.tier == tier]
        size = sum(s.size for s in rows)
        weighted = sum(s.size * s.reachability_pct for s in rows)
        reach = round(weighted / size) if size else 0
        panels.append(
            TierPanel(
                tier=tier,
                audience_size=size,
                reachability_pct=reach,
                planning_size=int(planning.get(tier, 0)),
                segment_count=len(rows),
            )
        )
    return panels


def _heatmap_models(repo: FamilyRepository, params: Params) -> dict[str, list[HeatmapCellModel]]:
    """The engagement-tier × attribute conversion heatmap (per dimension), as wire models."""
    lc = params.nurture.lifecycle
    families = _heatmap_families(
        repo, tiers=list(lc.engagement_tiers), handoff_stages=set(lc.handoff_stages)
    )
    matrix = core.engagement_attribute_heatmap(
        families, tiers=lc.engagement_tiers, dimensions=_HEATMAP_DIMENSIONS
    )
    return {
        dim: [
            HeatmapCellModel(
                engagement_tier=c.engagement_tier,
                attribute_value=c.attribute_value,
                total=c.total,
                converted=c.converted,
                conversion_pct=c.conversion_pct,
            )
            for c in cells
        ]
        for dim, cells in matrix.items()
    }


def _top_sequence(store: NurtureStore, program: Program) -> str | None:
    """The sequence with the highest average conversion rate (None when none)."""
    best_name: str | None = None
    best_score = -1.0
    for seq in store.list_sequences(program):
        if not seq.steps:
            continue
        avg_conv = sum(s.conversion_pct for s in seq.steps) / len(seq.steps)
        if avg_conv > best_score:
            best_score = avg_conv
            best_name = seq.name
    return best_name


# ===========================================================================
# READ endpoints (any authenticated seat).
# ===========================================================================
@router.get("/nurture/overview", response_model=OverviewResponse)
def get_overview(
    store: StoreDep,
    crm: CrmDep,
    repo: RepoDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> OverviewResponse:
    """The 5a overview — every widget computed from the seeded/aggregate sources."""
    now = datetime.now(UTC)
    lc = params.nurture.lifecycle

    mix = core.tier_mix(*_mix_counts(crm, repo))
    segments = store.list_segments(program)
    sequences = store.list_sequences(program)
    sequences_healthy = sum(
        0
        if core.sequence_health(
            [core.SequenceStepView(s.step, s.open_pct, s.click_pct) for s in seq.steps],
            min_open_pct=lc.sequence_health_min_open_pct,
            min_click_pct=lc.sequence_health_min_click_pct,
        )
        else 1
        for seq in sequences
    )

    contacts = [
        core.SlaContactView(c.applicant_label, c.entered_at, c.contacted_at, c.owner)
        for c in store.list_sla_contacts(program)
    ]
    sla = core.sla_compliance(contacts, now=now, window_hours=lc.sla_window_hours)

    threads = store.list_sms_threads(program)
    week_cutoff = now - _days(lc.week_days)
    reply_this_week = sum(
        1 for t in threads if t.replied and t.inbound_at is not None and t.inbound_at >= week_cutoff
    )
    cold_segment_count = sum(
        1 for s in segments if str(s.attribute_filters.get("engagement_tier")) == "cold"
    )

    stage_views, handoff_week, handoff_month = _pipeline_views(crm, repo, params, now=now)
    dist = core.pipeline_distribution(stage_views, stage_order=lc.pipeline_stage_order)
    crosstab = _heatmap_models(repo, params).get("income", [])

    return OverviewResponse(
        tiers=_tier_panels(store, program, params),
        engagement_tier_mix=TierMixModel(
            clicked=mix.clicked,
            opened=mix.opened,
            cold=mix.cold,
            total=mix.total,
            reachable=mix.reachable,
            reachability_pct=mix.reachability_pct,
        ),
        engagement_source=SOURCE_CRM_AGGREGATE,
        sequences_total=len(sequences),
        sequences_healthy=sequences_healthy,
        top_sequence=_top_sequence(store, program),
        sla_compliance_pct=sla.compliance_pct,
        sms_reply_count_this_week=reply_this_week,
        sms_replied_total=sum(1 for t in threads if t.replied),
        cold_segment_count=cold_segment_count,
        pipeline_stage_distribution=[
            PipelineStageModel(stage=s.stage, count=s.count, pct=s.pct, stuck=s.stuck)
            for s in dist.stages
        ],
        handoff_this_week=handoff_week,
        engagement_attribute_crosstab=crosstab,
    )


@router.get("/nurture/segments", response_model=SegmentsResponse)
def get_segments(
    store: StoreDep,
    repo: RepoDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> SegmentsResponse:
    """The 5b segments view — tier panels + saved segments + the engagement×attribute heatmap."""
    segments = [
        SegmentModel(
            segment_id=s.segment_id,
            tier=s.tier,
            sub_bucket=s.sub_bucket,
            label=s.label,
            attribute_filters=s.attribute_filters,
            size=s.size,
            reachability_pct=s.reachability_pct,
            owner=s.owner,
            notes=s.notes,
        )
        for s in store.list_segments(program)
    ]
    return SegmentsResponse(
        tiers=_tier_panels(store, program, params),
        segments=segments,
        heatmap=_heatmap_models(repo, params),
        source="supabase_mirror+source_of_truth",
    )


@router.get("/nurture/pipeline", response_model=PipelineResponse)
def get_pipeline(
    crm: CrmDep,
    repo: RepoDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> PipelineResponse:
    """The 5c pipeline view — LIVE/aggregate stage distribution + stuck + velocity + handoff."""
    now = datetime.now(UTC)
    lc = params.nurture.lifecycle
    stage_views, handoff_week, handoff_month = _pipeline_views(crm, repo, params, now=now)
    dist = core.pipeline_distribution(stage_views, stage_order=lc.pipeline_stage_order)
    handoff = core.handoff_metrics(
        stage_views,
        handoff_stages=lc.handoff_stages,
        handoff_week=handoff_week,
        handoff_month=handoff_month,
    )
    return PipelineResponse(
        stages=[
            PipelineStageModel(stage=s.stage, count=s.count, pct=s.pct, stuck=s.stuck)
            for s in dist.stages
        ],
        total=dist.total,
        stuck_total=dist.stuck_total,
        velocity_pct=dist.velocity_pct,
        handoff=_handoff_model(handoff),
        source=SOURCE_CRM_AGGREGATE,
    )


@router.get("/nurture/sequences", response_model=SequencesResponse)
def get_sequences(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> SequencesResponse:
    """The 5d sequences view — read-only synthetic mirror + per-step perf + health flag."""
    lc = params.nurture.lifecycle
    out: list[SequenceModel] = []
    for seq in store.list_sequences(program):
        steps = [core.SequenceStepView(s.step, s.open_pct, s.click_pct) for s in seq.steps]
        flag = core.sequence_health(
            steps,
            min_open_pct=lc.sequence_health_min_open_pct,
            min_click_pct=lc.sequence_health_min_click_pct,
        )
        out.append(
            SequenceModel(
                sequence_id=seq.sequence_id,
                name=seq.name,
                seq_type=seq.seq_type,
                audience_size=seq.audience_size,
                step_count=seq.step_count,
                steps=[
                    SequenceStepModel(
                        step=s.step,
                        open_pct=s.open_pct,
                        click_pct=s.click_pct,
                        conversion_pct=s.conversion_pct,
                    )
                    for s in seq.steps
                ],
                health_flag=flag,
                status=seq.status,
            )
        )
    return SequencesResponse(sequences=out, source=SOURCE_SYNTHETIC_MIRROR)


@router.get("/nurture/sms", response_model=SmsResponse)
def get_sms(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
    status: Annotated[str | None, Query()] = None,
) -> SmsResponse:
    """The 5e SMS inbox — threads + optional status filter + (re-derived) theme tags."""
    rules = params.nurture.lifecycle.theme_keyword_rules
    threads = store.list_sms_threads(program)
    if status is not None:
        threads = [t for t in threads if t.status == status]
    out: list[SmsThreadModel] = []
    for t in threads:
        tags, mode = core.sms_theme_tag(t.last_message, keyword_rules=rules)
        out.append(
            SmsThreadModel(
                thread_id=t.thread_id,
                contact_label=t.contact_label,
                last_message=t.last_message,
                theme_tags=tags or t.theme_tags,
                tag_mode=mode,
                status=t.status,
                replied=t.replied,
                inbound_at=t.inbound_at,
            )
        )
    return SmsResponse(threads=out, source=SOURCE_SYNTHETIC_MIRROR)


@router.get("/nurture/sla", response_model=SlaResponse)
def get_sla(
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> SlaResponse:
    """The 5f SLA view — applicants today, 24h compliance, late list, per-owner, 30d history."""
    now = datetime.now(UTC)
    lc = params.nurture.lifecycle
    rows = store.list_sla_contacts(program)
    contacts = [
        core.SlaContactView(c.applicant_label, c.entered_at, c.contacted_at, c.owner) for c in rows
    ]
    sla = core.sla_compliance(contacts, now=now, window_hours=lc.sla_window_hours)
    today_cutoff = now - _days(1)
    history_cutoff = now - _days(30)
    return SlaResponse(
        total=sla.total,
        applicants_today=sum(1 for c in rows if c.entered_at >= today_cutoff),
        compliance_pct=sla.compliance_pct,
        pending=sla.pending,
        late=[
            SlaLateModel(
                applicant_label=i.applicant_label,
                owner=i.owner,
                hours_waiting=i.hours_waiting,
                contacted=i.contacted,
            )
            for i in sla.late
        ],
        per_owner=[
            SlaOwnerModel(
                owner=o.owner,
                total=o.total,
                in_window=o.in_window,
                compliance_pct=o.compliance_pct,
            )
            for o in sla.per_owner
        ],
        history_30d_count=sum(1 for c in rows if c.entered_at >= history_cutoff),
        window_hours=lc.sla_window_hours,
        source="supabase_mirror",
    )


@router.get("/nurture/kpi-feed", response_model=KpiFeedResponse)
def get_kpi_feed(
    crm: CrmDep,
    repo: RepoDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> KpiFeedResponse:
    """The KPI feed (pipeline dist + handoff), shaped for the Dashboard module [CROSS-LINK 3]."""
    now = datetime.now(UTC)
    lc = params.nurture.lifecycle
    stage_views, handoff_week, handoff_month = _pipeline_views(crm, repo, params, now=now)
    dist = core.pipeline_distribution(stage_views, stage_order=lc.pipeline_stage_order)
    handoff = core.handoff_metrics(
        stage_views,
        handoff_stages=lc.handoff_stages,
        handoff_week=handoff_week,
        handoff_month=handoff_month,
    )
    return KpiFeedResponse(
        pipeline_stage_distribution=[
            PipelineStageModel(stage=s.stage, count=s.count, pct=s.pct, stuck=s.stuck)
            for s in dist.stages
        ],
        handoff=_handoff_model(handoff),
        source=SOURCE_CRM_AGGREGATE,
    )


@router.get("/nurture/attribution", response_model=AttributionResponse)
def get_attribution(
    content_store: ContentStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> AttributionResponse:
    """Per-piece conversion attribution feeding Content Performance [CROSS-LINK 4].

    Reads the Content module's per-piece perf rows and surfaces each piece's conversion +
    computed conversion rate, with the UTM-attribution honesty flag preserved (an
    un-attributed piece is surfaced as such — the broken-UTM reality stays visible).
    """
    pieces = content_store.list_piece_perf(program)
    rows = [
        AttributionRow(
            piece_title=p.piece_title,
            channel=p.channel,
            conversions=p.conversions,
            conversion_pct=(
                max(0, min(100, round(100 * p.conversions / p.clicks))) if p.clicks else 0
            ),
            utm_attributed=p.utm_attributed,
        )
        for p in pieces
    ]
    return AttributionResponse(
        pieces=rows,
        unattributable_count=sum(1 for p in pieces if not p.utm_attributed),
        source="content_metrics",
    )


# ===========================================================================
# WRITE endpoints (owner-gated) + cross-links.
# ===========================================================================
@router.post("/nurture/segments/build", response_model=SegmentModel)
def build_segment(
    body: SegmentBuildRequest,
    store: StoreDep,
    repo: RepoDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> SegmentModel:
    """Build + save an engagement × attribute segment — owner-gated (5b).

    The audience size + reachability are COMPUTED by the pure segment builder over the
    cohort (never fabricated). ``tier`` must be one of T1/T2/T3 (clean 422; INV-2).
    """
    _require_nurture_owner(principal)
    if body.tier not in core.TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"tier must be one of {list(core.TIERS)}, got {body.tier!r}",
        )
    lc = params.nurture.lifecycle
    candidates = [
        core.SegmentCandidate(
            engagement_tier=_synthetic_tier(fam.family_id, list(lc.engagement_tiers)),
            attributes={
                "income": fam.income_tier.value if fam.income_tier is not None else "unknown",
                "region": fam.state or "unknown",
            },
        )
        for fam in repo.list_families()
    ]
    size = core.segment_builder(
        candidates,
        engagement_tiers=body.engagement_tiers,
        attribute_filters=body.attribute_filters,
    )
    # Reachability = the reachable (clicked+opened) share within the SAME segment filter.
    reachable_tiers = [t for t in (body.engagement_tiers or lc.engagement_tiers) if t != "cold"]
    reachable = core.segment_builder(
        candidates,
        engagement_tiers=reachable_tiers,
        attribute_filters=body.attribute_filters,
    )
    reachability_pct = round(100 * reachable / size) if size else 0.0

    filters: dict[str, Any] = dict(body.attribute_filters)
    if body.engagement_tiers:
        filters["engagement_tier"] = body.engagement_tiers
    seg = store.create_segment(
        program,
        tier=body.tier,
        sub_bucket=body.sub_bucket,
        label=body.label,
        attribute_filters=filters,
        size=size,
        reachability_pct=reachability_pct,
        owner=NURTURE_OWNER_WORKSTREAM,
        notes=body.notes,
    )
    return SegmentModel(
        segment_id=seg.segment_id,
        tier=seg.tier,
        sub_bucket=seg.sub_bucket,
        label=seg.label,
        attribute_filters=seg.attribute_filters,
        size=seg.size,
        reachability_pct=seg.reachability_pct,
        owner=seg.owner,
        notes=seg.notes,
    )


@router.post("/nurture/sms/{thread_id}/flag-hot-family", response_model=DecisionResponse)
def flag_hot_family(
    thread_id: UUID,
    store: StoreDep,
    decisions_store: DecisionsStoreDep,
    program: ProgramDep,
    principal: AnyPrincipalDep,
) -> DecisionResponse:
    """Escalate a hot-family SMS thread into the leadership Decision Queue [CROSS-LINK 1].

    Open to ANY authenticated seat (the rep flags it). Marks the thread ``hot_family`` and
    enqueues ONE open ``sms_hot_family`` decision on the ``nurture`` workstream via the B2
    feeder; ``raised_by`` is STAMPED from the verified principal (never the body; INV-1).
    404 on an unknown thread.
    """
    thread = store.get_thread(program, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="sms thread not found")
    store.update_thread(program, thread_id, status="hot_family")
    decision = flag_decision(
        decisions_store,
        program,
        source=NURTURE_HOT_FAMILY_SOURCE,
        payload={"thread_id": str(thread_id), "contact_label": thread.contact_label},
        question=f"Hot family flagged from SMS: {thread.contact_label}",
        raised_by=_actor_token(principal),
        workstream=NURTURE_WORKSTREAM,
        recommendation="Reach out now — inbound buying signal in the SMS thread.",
    )
    return DecisionResponse.of(decision)


@router.post("/nurture/sms/objection-brief", response_model=ObjectionBriefResponse)
def objection_brief(
    body: ObjectionBriefRequest,
    content_store: ContentStoreDep,
    program: ProgramDep,
    params: ParamsDep,
    principal: AnyPrincipalDep,
) -> ObjectionBriefResponse:
    """Turn a recurring SMS objection theme into a Content calendar DRAFT brief [CROSS-LINK 2].

    Creates one ``content_calendar_entry`` in DRAFT status via the Content store so the
    Content team picks it up — closing the objection→content loop. Open to ANY
    authenticated seat. ``owner`` is the nurture token (server-stamped, INV-1).
    """
    now = datetime.now(UTC)
    channel = params.content.channels[0] if params.content.channels else "email"
    title = body.title or f"Objection brief: {body.theme}"
    entry = content_store.upsert_calendar_entry(
        program,
        title=title,
        channel=channel,
        scheduled_date=now.date(),
        status=STATUS_DRAFT,
        piece_ref=None,
        owner=NURTURE_OWNER_WORKSTREAM,
    )
    return ObjectionBriefResponse(
        entry_id=entry.entry_id,
        title=entry.title,
        channel=entry.channel,
        status=entry.status,
    )


# ===========================================================================
# Small local helpers.
# ===========================================================================
def _mix_counts(crm: CRMAdapter, repo: FamilyRepository) -> tuple[int, int, int]:
    """Read the LIVE/aggregate clicked/opened/cold counts for the cohort."""
    mix = crm.read_engagement_mix(_cohort_family_ids(repo))
    return mix.clicked, mix.opened, mix.cold


def _handoff_model(handoff: core.HandoffMetrics) -> HandoffModel:
    """Project the core handoff rollup onto the wire model."""
    return HandoffModel(
        weekly=handoff.weekly,
        monthly=handoff.monthly,
        cumulative=handoff.cumulative,
        total_deals=handoff.total_deals,
        conversion_pct=handoff.conversion_pct,
    )


def _days(n: int) -> timedelta:
    """A ``timedelta`` of ``n`` days (the edge owns the clock math; the core stays clock-free)."""
    return timedelta(days=n)

"""Marketing-breadth endpoints — the S6 surface (FR-3.6/3.8/3.10/3.11/3.12; ARCH §6).

The composition layer wiring the S6 marketing-breadth core behind REST. Like
``app/api/funding.py`` it is deliberately THIN: every decision-bearing step —
creator surfacing, KPI rollup, the simulated dispatch gate, the cheapest-first
pipeline guard — lives in an owned/pure module it orchestrates (INV-2). This
router only assembles inputs, calls the logic/adapter, shapes the response, maps
HTTP errors, and (for the schedule action) logs to the audit spine (NFR-6). No
business logic, no magic numbers (thresholds/baselines come from ``params`` —
INV-11). No live external call is ever made here — dispatch is SIMULATED (INV-9).

  ``GET  /creators``   — the §8.1 creator-discovery list, surfaced/filtered by
                         ``params.creator_scoring.surface_threshold`` and sorted
                         fit desc then id. Aggregate/synthetic + adults-only by
                         construction (INV-6) — ``surface`` filters defensively.
  ``GET  /sentiment``  — an AGGREGATE-only sentiment summary (``source_mode``
                         placeholder, INV-6) from the dep'd adapter, plus the
                         seeded records for context (no real-user PII, INV-1).
  ``GET  /kpi``        — the §3.11 per-channel rollup; baselines/targets read
                         FROM ``params.kpi.levers`` (INV-11), observed metrics
                         from a deterministic per-channel seed map.
  ``GET  /content/schedule`` — the current simulated post queue.
  ``POST /content/schedule`` — build a ``ScheduledPost`` (``dispatch_mode``
                         ALWAYS simulated, from ``params.scheduler.dispatch_mode``)
                         then ``gate_dispatch`` + ``simulate_send``: a passing
                         validation AND ``approve`` ⇒ ``simulated_sent``, else
                         ``blocked`` (200, fail-closed — never a 500, never live).
                         The action is logged to observability (NFR-6).
  ``GET  /content/pipeline`` — the seeded concept→image→video artifacts (image/
                         video PLACEHOLDER, OUT-1). Namespaced under ``/content``
                         so it does not collide with the enrollment ``/pipeline``
                         board (the staged-content pipeline is a distinct domain).
  ``POST /content/pipeline/advance`` — the §4 cheapest-first guard: a stage may advance
                         only when human-``selected`` AND holding a passing
                         validation, else 422 (fail-closed, INV-3).
  ``GET  /recipes``    — the §8.5 Tom-Babb-attributed recipe templates (INV-7).

This module may import ``app.adapters`` / ``app.data`` / ``app.marketing`` /
``app.observability`` (it is the composition root); ``app/core/`` stays pure.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.adapters.sentiment.base import SentimentAdapter, SentimentWindow
from app.ai.schemas.brand import MarketingRecipe
from app.ai.schemas.content import (
    Channel,
    Decision,
    GeneratedBy,
    HumanDecision,
    Provenance,
)
from app.api.deps import (
    get_crm_adapter_dep,
    get_observability_log,
    get_params,
    get_repository,
    get_sentiment_adapter_dep,
)
from app.core.eval_gate import RuleVerdict, ValidationResult
from app.core.params import Params
from app.data.repository import FamilyRepository
from app.data.synthetic import (
    generate_content_pipeline,
    generate_creator_records,
    generate_recipes,
    generate_sentiment_records,
)
from app.marketing.creator_scoring import surface
from app.marketing.kpi_board import ChannelKpi, roll_up
from app.marketing.pipeline import PipelineAdvanceBlocked, advance
from app.marketing.scheduler import simulate_send
from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    ImageArtifact,
    Stage,
    StageArtifact,
    VideoArtifact,
)
from app.marketing.schemas.discovery import SentimentRecord
from app.marketing.schemas.scheduling import (
    DispatchMode,
    DispatchStatus,
    ScheduledPost,
)
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["marketing"])

# The §10 flow + schema version + eval name surfaced on each logged schedule
# action (NFR-6) — labeled distinctly so the audit records the dispatch subject.
SCHEDULE_FLOW = "content_schedule"
SCHEDULE_SCHEMA_VERSION = "1"
SCHEDULE_EVAL_NAME = "dispatch_gate"

# Composition-layer fixtures for the deterministic reads (NOT domain tunables —
# the params-owned tunables are surface_threshold / kpi.levers / dispatch_mode,
# read per request, INV-11). These describe the simulation harness only.
#
# Per-channel observed rates for `GET /kpi`. The honest current state per
# ANALYSIS/growth-strategy.md ("Acquisition: near-zero" — 0% email nurture, 0%
# paid, 0% AI-search/GEO, branded-only organic): we do NOT fabricate progress.
# Every acquisition channel's observed rate equals its 0.0 params baseline, so the
# board truthfully shows each lever BELOW target ("not turned on yet") — which is
# the motivation for the levers, not a bug. Channels absent here default to
# baseline in `roll_up` (lever_delta 0). Real organic baselines are hydrated from
# the scraped 440-post catalog in the ingest slice (Phase 1). Keyed by `Channel`.
_OBSERVED_RATES: dict[str, float] = {
    Channel.INSTAGRAM.value: 0.0,
    Channel.EMAIL.value: 0.0,
    Channel.BLOG.value: 0.0,
    Channel.GEO.value: 0.0,
}

# The default sentiment window the placeholder adapter aggregates over (opaque
# date strings — the placeholder source is offline/synthetic, no wall clock).
_SENTIMENT_WINDOW = SentimentWindow(start="2026-01-01", end="2026-12-31")

# Composition-layer fixture for `GET /geo-targeting`: the demand metros the
# growth strategy NAMES (the summer-camp city set in the scraped catalog —
# Austin/Houston/Dallas/Raleigh). NOT a domain tunable (no scoring/threshold
# reads from it) and NOT params-owned (per the breadth-agent ownership rule) —
# it is a fixed, documented composition-layer constant like `_OBSERVED_RATES`,
# describing the strategy's stated target metros so the panel can surface them.
# These are AGGREGATE metro labels only (INV-6) — no individual/minor keying.
_DEMAND_METROS: tuple[tuple[str, str], ...] = (
    ("Austin", "TX"),
    ("Houston", "TX"),
    ("Dallas", "TX"),
    ("Raleigh", "NC"),
)

# The in-memory simulated post queue (A-3): posts that reached `simulated_sent`
# are appended here so `GET /content/schedule` can surface the queue. Held in a
# one-slot pattern; v1 is per-process (swapped for the social adapter's queue in
# prod). It only ever holds SIMULATED posts (INV-9).
_SCHEDULE_QUEUE: list[ScheduledPost] = []

# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/funding.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
SentimentAdapterDep = Annotated[SentimentAdapter, Depends(get_sentiment_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]


# --------------------------------------------------------------------------- #
# GET /creators — the §8.1 surfaced creator-discovery list (FR-3.8, INV-6).
# --------------------------------------------------------------------------- #


class CreatorOut(BaseModel):
    """The projected creator-discovery record the UI builds to (snake_case).

    A flat projection of :class:`CreatorRecord` — only the fields the discovery
    card renders. ``data_mode`` is the aggregate/synthetic badge; ``is_minor`` is
    ALWAYS false (the schema rejects a minor at parse time; `surface` filters
    defensively — INV-6).
    """

    id: UUID
    display_handle: str
    channel: Channel
    audience_segment: str
    fit_score: float
    authenticity_score: float
    rationale: str | None
    data_mode: str
    is_minor: bool


@router.get("/creators", response_model=list[CreatorOut])
def get_creators(params: ParamsDep) -> list[CreatorOut]:
    """Surfaced creator-discovery list — filtered by the params threshold (FR-3.8).

    Keeps creators with ``fit_score >= params.creator_scoring.surface_threshold``,
    sorted fit desc then id (via :func:`surface`), aggregate/synthetic + adults-
    only by construction (INV-6). A read-only projection; nothing is logged.
    """
    surfaced = surface(generate_creator_records(), params=params)
    return [
        CreatorOut(
            id=creator.id,
            display_handle=creator.display_handle,
            channel=creator.channel,
            audience_segment=creator.audience_segment.value,
            fit_score=creator.fit_score,
            authenticity_score=creator.authenticity_score,
            rationale=creator.rationale,
            data_mode=creator.data_mode.value,
            is_minor=creator.is_minor,
        )
        for creator in surfaced
    ]


# --------------------------------------------------------------------------- #
# GET /sentiment — the §8.2 aggregate sentiment view (FR-3.10, INV-6/INV-1).
# --------------------------------------------------------------------------- #


class SentimentSummaryOut(BaseModel):
    """The aggregate sentiment summary the UI badges (AGGREGATE-only, INV-6).

    Bucket counts + total + ``source_mode`` (``placeholder`` in v1, never a live
    feed). There is deliberately NO per-person or child-keyed field (INV-6).
    """

    positive: int
    neutral: int
    negative: int
    total: int
    source_mode: str


class SentimentView(BaseModel):
    """The §8.2 sentiment view — the aggregate summary + the seeded records."""

    summary: SentimentSummaryOut
    records: list[SentimentRecord]


@router.get("/sentiment", response_model=SentimentView)
def get_sentiment(adapter: SentimentAdapterDep) -> SentimentView:
    """Aggregate sentiment summary (placeholder source) + records (FR-3.10).

    The summary comes from the dep'd placeholder adapter (``source_mode`` is
    ``placeholder``, never ``live_feed`` — INV-6/INV-9); the records are the
    seeded synthetic mentions (no real-user PII, INV-1). Read-only; not logged.
    """
    summary = adapter.fetch(_SENTIMENT_WINDOW)
    return SentimentView(
        summary=SentimentSummaryOut(
            positive=summary.positive,
            neutral=summary.neutral,
            negative=summary.negative,
            total=summary.total,
            source_mode=summary.source_mode,
        ),
        records=generate_sentiment_records(),
    )


# --------------------------------------------------------------------------- #
# GET /geo-targeting — the FR-3.9 AGGREGATE region rollup (INV-6).
#
# A DISTINCT endpoint from the `/geo` GEO board (which owns AI-search citation
# structures): this is the marketing-breadth geo-targeting panel's data source.
# It rolls the synthetic `LeadsNew.region` field — an AGGREGATE region label by
# construction (§4.2, P-4: never a ZIP/lat-long of a minor) — up into per-region
# lead counts, and surfaces the strategy's NAMED demand metros. There is NO
# per-child / per-minor / individual-keyed field anywhere in the response
# (INV-6 — targeting is aggregate-only, never child-keyed).
# --------------------------------------------------------------------------- #


class RegionDemandOut(BaseModel):
    """One AGGREGATE region row — a count + share, never an individual (INV-6)."""

    region: str
    lead_count: int
    share: float


class DemandMetroOut(BaseModel):
    """A strategy-named demand metro (aggregate metro label only, INV-6)."""

    metro: str
    state: str


class GeoTargetingOut(BaseModel):
    """The FR-3.9 geo-targeting view — aggregate region rollup + named metros.

    Deliberately carries ONLY aggregate fields: per-region ``lead_count``/``share``
    and the named ``demand_metros``. No per-child, per-minor, or individual-keyed
    field is representable here (INV-6 — aggregate-only, no child-keyed targeting).
    """

    regions: list[RegionDemandOut]
    demand_metros: list[DemandMetroOut]
    total: int


@router.get("/geo-targeting", response_model=GeoTargetingOut)
def get_geo_targeting(repo: RepositoryDep) -> GeoTargetingOut:
    """AGGREGATE region rollup + named demand metros (FR-3.9; INV-6).

    Rolls the synthetic ``LeadsNew.region`` field up into per-region lead counts
    (region is aggregate by construction — no minor keying, §4.2/P-4), sorted by
    count desc then region for a stable order, with each region's share of the
    total. Then surfaces the strategy's NAMED demand metros (Austin/Houston/
    Dallas/Raleigh). Read-only; nothing is logged. The response has NO per-child
    or individual-keyed field — targeting is aggregate-only (INV-6).
    """
    counts: dict[str, int] = {}
    for joined in repo.list_joined():
        lead = joined.lead
        if lead is None:
            continue
        counts[lead.region] = counts.get(lead.region, 0) + 1

    total = sum(counts.values())
    regions = [
        RegionDemandOut(
            region=region,
            lead_count=count,
            share=(count / total) if total else 0.0,
        )
        # Sort by count desc, then region asc — a stable, deterministic order.
        for region, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    demand_metros = [DemandMetroOut(metro=metro, state=state) for metro, state in _DEMAND_METROS]
    return GeoTargetingOut(regions=regions, demand_metros=demand_metros, total=total)


# --------------------------------------------------------------------------- #
# GET /kpi — the §3.11 per-channel rollup vs the params levers (INV-11).
# --------------------------------------------------------------------------- #


@router.get("/kpi", response_model=list[ChannelKpi])
def get_kpi(params: ParamsDep) -> list[ChannelKpi]:
    """Per-channel KPI rollup — baselines/targets from params (FR-3.11; INV-11).

    Rolls the deterministic per-channel observed-rate fixture up against
    ``params.kpi.levers`` via :func:`roll_up`: ``baseline``/``target`` come from
    params (a drifted param moves the rollup and the test fails). Read-only.
    """
    return roll_up(_OBSERVED_RATES, params=params)


# --------------------------------------------------------------------------- #
# GET/POST /content/schedule — the §6 simulated dispatch gate (FR-3.6, INV-9).
# --------------------------------------------------------------------------- #


class ApprovalIn(BaseModel):
    """The human approval on a schedule request — only ``decision`` is consulted."""

    decision: Decision = Decision.PENDING


class ValidationIn(BaseModel):
    """The validation verdict on a schedule request — only ``passed`` is consulted."""

    passed: bool


class ScheduleRequest(BaseModel):
    """A ``POST /content/schedule`` body (FR-3.6).

    Carries the post target (``channel`` / ``scheduled_for`` + an optional asset
    or candidate ref), the human ``approval`` and the ``validation`` verdict. The
    gate decides ``simulated_sent`` vs ``blocked``; ``dispatch_mode`` is NEVER
    accepted here — it is forced to simulated from params (INV-9, OUT-2).
    """

    asset_ref: UUID | None = None
    candidate_ref: UUID | None = None
    channel: Channel
    scheduled_for: str
    approval: ApprovalIn
    validation: ValidationIn


def _validation_result(passed: bool) -> ValidationResult:
    """A minimal :class:`ValidationResult` mirroring the request's pass state.

    The dispatch gate only consults ``validation.passed``; we build a verdict
    from the request rather than re-running the V-1..V-4 message gate here. A
    failing verdict forces ``blocked`` downstream (fail-closed, INV-3/INV-4).
    """
    verdict = RuleVerdict.PASS if passed else RuleVerdict.FAIL
    return ValidationResult(
        v1_schema=verdict,
        v2_grounding=verdict,
        v3_coppa=verdict,
        v4_onbrand=verdict,
        passed=passed,
        failed_rules=[] if passed else ["validation_failed"],
    )


@router.get("/content/schedule", response_model=list[ScheduledPost], response_model_by_alias=False)
def get_content_schedule() -> list[ScheduledPost]:
    """The current simulated post queue (FR-3.6). Starts empty; never live (INV-9)."""
    return list(_SCHEDULE_QUEUE)


@router.post("/content/schedule", response_model=ScheduledPost, response_model_by_alias=False)
def post_content_schedule(
    request: ScheduleRequest,
    params: ParamsDep,
    log: LogDep,
    crm_adapter: CRMAdapterDep,
) -> ScheduledPost:
    """Build → gate → simulate-send a scheduled post (FR-3.6; INV-9; NFR-6).

    ``dispatch_mode`` is forced to simulated from ``params.scheduler.dispatch_mode``
    (the param is typed shut to ``simulated`` — INV-9/OUT-2; a live attempt would
    fail loud). :func:`simulate_send` runs the §6 gate: a passing validation AND
    ``approve`` ⇒ ``simulated_sent`` (with a deterministic receipt), else
    ``blocked`` — a blocked post returns 200 (fail-closed, NOT a 500). The action
    is appended to the §10 audit log. A simulated_sent post enters the queue.

    Bet 1 (READY-TO-FLIP): an approved+validated **EMAIL** post that reaches
    ``simulated_sent`` ALSO routes through the CRM adapter dep
    (``crm_adapter.send_message(...)``) — a Note / trigger-property write. The
    DEFAULT adapter is simulated (records in-memory, no network; INV-9), so nothing
    hits the portal unless ``CRM_MODE=live`` selects the live HubSpot adapter — the
    SAME call, the config flip. A blocked post never reaches this call (fail-closed,
    INV-3/INV-4): the routing is gated on the terminal ``simulated_sent`` status.
    """
    # dispatch_mode is params-owned and typed shut to simulated (INV-9/OUT-2).
    dispatch_mode = DispatchMode(params.scheduler.dispatch_mode)

    post = ScheduledPost(
        id=uuid4(),
        assetRef=request.asset_ref,
        candidateRef=request.candidate_ref,
        channel=request.channel,
        scheduledFor=request.scheduled_for,
        dispatchMode=dispatch_mode,
        # The gate sets the terminal status; seed BLOCKED so an un-cleared post is
        # fail-closed by default before simulate_send runs.
        dispatchStatus=DispatchStatus.BLOCKED,
        validation="vr-schedule-request",
        approval=HumanDecision(decision=request.approval.decision),
        provenance=Provenance(generated_by=GeneratedBy.HUMAN, created_at=request.scheduled_for),
    )

    validation = _validation_result(request.validation.passed)
    dispatched = simulate_send(post, validation=validation)

    # Log the schedule action + its gate verdict to the audit spine (NFR-6).
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=SCHEDULE_FLOW,
        schema_version=SCHEDULE_SCHEMA_VERSION,
        payload=dispatched.model_dump(mode="json"),
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name=SCHEDULE_EVAL_NAME,
        passed=dispatched.dispatch_status is DispatchStatus.SIMULATED_SENT,
    )

    if dispatched.dispatch_status is DispatchStatus.SIMULATED_SENT:
        _SCHEDULE_QUEUE.append(dispatched)
        # Bet 1: an approved+validated EMAIL post pushes through the CRM adapter
        # (a Note / trigger-property write). Default simulated ⇒ recorded in-memory,
        # no network; CRM_MODE=live ⇒ the SAME call hits the portal (the flip). Only
        # the EMAIL channel routes here; other channels stay on the simulated social
        # queue. Reached ONLY on simulated_sent — a blocked post never calls it.
        if request.channel is Channel.EMAIL:
            crm_adapter.send_message(
                {
                    "channel": Channel.EMAIL.value,
                    "body": f"Scheduled email post for {dispatched.scheduled_for}.",
                    "scheduled_post_id": str(dispatched.id),
                }
            )

    return dispatched


# --------------------------------------------------------------------------- #
# GET /pipeline + POST /pipeline/advance — the §4 cheapest-first guard (INV-3).
# --------------------------------------------------------------------------- #


class PipelineView(BaseModel):
    """The seeded staged-pipeline artifacts, keyed by stage (concept/image/video).

    ``image`` / ``video`` are PLACEHOLDER in v1 (OUT-1) — each carries a synthetic
    ``placeholder_uri`` and a STRING cost-estimate pointer, never a numeric price.
    """

    concept: ConceptArtifact
    image: ImageArtifact
    video: VideoArtifact


@router.get("/content/pipeline", response_model=PipelineView, response_model_by_alias=False)
def get_pipeline() -> PipelineView:
    """The seeded concept→image→video artifacts (§4; image/video PLACEHOLDER, OUT-1)."""
    # The generator returns the chain in pipeline order [concept, image, video]
    # (the seed's locked contract); index by stage so a reorder fails loud.
    by_stage = {piece.stage: piece for piece in generate_content_pipeline()}
    concept = by_stage[Stage.CONCEPT]
    image = by_stage[Stage.IMAGE]
    video = by_stage[Stage.VIDEO]
    assert isinstance(concept, ConceptArtifact)  # noqa: S101 — seed contract.
    assert isinstance(image, ImageArtifact)  # noqa: S101 — seed contract.
    assert isinstance(video, VideoArtifact)  # noqa: S101 — seed contract.
    return PipelineView(concept=concept, image=image, video=video)


class PipelineAdvanceRequest(BaseModel):
    """A ``POST /pipeline/advance`` body — the §4 guard inputs.

    ``stage`` is the current stage; ``status`` its human-selection status;
    ``validation`` its verdict. The guard advances only when ``status==selected``
    AND ``validation.passed`` (cheapest-first), else it refuses (INV-3).
    """

    stage: Stage
    status: ArtifactStatus
    validation: ValidationIn


class PipelineAdvanceResponse(BaseModel):
    """The §4 advance result — the unlocked next (costlier) stage."""

    next_stage: Stage


@router.post("/content/pipeline/advance", response_model=PipelineAdvanceResponse)
def post_pipeline_advance(request: PipelineAdvanceRequest) -> PipelineAdvanceResponse:
    """Apply the §4 cheapest-first advance guard (INV-3 — fail-closed).

    Advances only when the stage is human-``selected`` AND holds a passing
    validation; otherwise :func:`advance` raises ``PipelineAdvanceBlocked`` and we
    return 422 with the blocked reason — never a silent advance, never a 500.
    """
    artifact = StageArtifact(
        id=uuid4(),
        pipelineId=uuid4(),
        stage=request.stage,
        status=request.status,
        costEstimateRef="tech_stack:cost_model#pipeline_advance",
        provenance=Provenance(generated_by=GeneratedBy.HUMAN, created_at="2026-01-01T00:00:00Z"),
    )
    validation = _validation_result(request.validation.passed)
    try:
        nxt = advance(artifact, validation=validation)
    except PipelineAdvanceBlocked as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PipelineAdvanceResponse(next_stage=nxt)


# --------------------------------------------------------------------------- #
# GET /recipes — the §8.5 Tom-Babb-attributed recipe templates (INV-7).
# --------------------------------------------------------------------------- #


@router.get("/recipes", response_model=list[MarketingRecipe])
def get_recipes() -> list[MarketingRecipe]:
    """The §8.5 runnable recipe templates — each attributes Tom Babb (INV-7; FR-3.12)."""
    return generate_recipes()

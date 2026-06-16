"""Publish fan-out + dual-screen monitor endpoints (publish-monitor W4; FR-3.6).

The composition layer wiring the deterministic publish fan-out
(:func:`app.marketing.publish.plan_publish`) into HTTP. Like ``app/api/geo.py``
and ``app/api/marketing.py`` it is deliberately THIN: every decision-bearing step
— the V-1..V-4 grounding gate, the per-platform fan-out + caps, the GT Social Post
HubSpot mirror, the placeholder media gen — lives in an owned/pure module it
orchestrates (INV-2). This router only assembles inputs, runs the gate, calls the
fan-out + adapters, persists the monitor, logs to the audit spine (NFR-6), and
shapes the response.

  ``POST /content/publish``
    Validate the content body through the EXISTING V-1..V-4 gate
    (:func:`app.core.eval_gate.evaluate_message`) → a :class:`ValidationResult`;
    refuse to dispatch if the suite-level grounding eval is RED (fail-closed,
    INV-3); build a :class:`PublishRequest`; fan it out via ``plan_publish`` with
    caps + channels from ``params.scheduler``; mirror each mirrorable dispatch to
    HubSpot via the injected CRM adapter; (optionally) generate placeholder media
    via the injected media adapter ($0, OUT-1); persist the
    :class:`PublishMonitor` (+ media + per-platform body) in the in-process
    registry; LOG proposal + eval + decision (NFR-6). Returns a FLAT per-platform
    response. A FAILED validation BLOCKS all dispatches (the fan-out already does
    this) and the response says so — never softened (INV-4).

  ``GET /publish/monitor``
    The cockpit observability plane: the persisted monitor feed (newest first),
    per-platform status across BOTH screens (cockpit + HubSpot mirror), media, and
    mirror status/ids.

The publish action is gated by the consolidated ``message_safety_grounding`` eval
(FR-4.3): a red row disables the action in the LIVE path and surfaces an
``action_enabled`` flag the UI reads to disable the publish button (INV-3).

This module may import ``app.adapters`` / ``app.ai`` / ``app.marketing`` /
``app.observability`` (it is the composition root); ``app/core/`` stays pure. No
live external call is ever made here — dispatch is SIMULATED, mirror + media are
the simulated/placeholder adapters (INV-9, OUT-1).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.adapters.hubspot.crm_adapter import CRMAdapter, apply_mirror_results, is_mirrorable
from app.adapters.media.base import ImageRef, MediaGenAdapter, MediaSpec, VideoRef
from app.ai.schemas.content import (
    Channel,
    Decision,
    GeneratedBy,
    HumanDecision,
    Provenance,
)
from app.api.deps import (
    get_crm_adapter_dep,
    get_eval_state,
    get_media_gen_adapter_dep,
    get_observability_log,
    get_params,
    get_settings_dep,
)
from app.core.eval_gate import ValidationResult, action_enabled, evaluate_message
from app.core.params import Params
from app.core.settings import Settings
from app.evals.suite import EvalSuiteResult
from app.marketing.publish import plan_publish
from app.marketing.schemas.publish import MirrorStatus, PublishMonitor, PublishRequest
from app.marketing.schemas.scheduling import DispatchStatus
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["publish"])

# The §10 flow + schema version + eval names surfaced on each logged publish action
# (NFR-6). The publish action is an AI content action gated by the grounding eval
# (FR-4.3), so the suite-level kill consults this same eval name (INV-3).
PUBLISH_FLOW = "content_publish"
PUBLISH_SCHEMA_VERSION = "1"
PUBLISH_EVAL_NAME = "message_safety_grounding"

# The validation ref recorded on every fanned-out ScheduledPost (audit link). A
# fixed composition-layer label (one home) — the full ValidationResult is logged
# to the audit spine; the post carries this pointer, mirroring marketing.py's
# "vr-schedule-request" convention.
_VALIDATION_REF = "vr-publish-request"

# The in-process published-monitor registry (FR-3.6) — every POST /content/publish
# appends its persisted PublishMonitor record here so GET /publish/monitor can read
# the feed across requests. In-memory, per-process, deterministic (no DB in v1, the
# same seam pattern as geo._published_registry). Held in a one-slot list so
# reset_published_monitors can rebind for test isolation without a `global`.
_published_monitors: list[list[PublishedRecord]] = [[]]


def reset_published_monitors() -> None:
    """Clear the process-shared published-monitor feed (test isolation only).

    The feed is append-only by design; tests need a clean feed per case so a prior
    publish does not leak rows into an unrelated assertion. Production never calls
    this.
    """
    _published_monitors[0] = []


# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/geo.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
MediaAdapterDep = Annotated[MediaGenAdapter, Depends(get_media_gen_adapter_dep)]
EvalStateDep = Annotated["EvalSuiteResult | None", Depends(get_eval_state)]


# --------------------------------------------------------------------------- #
# Request / response shapes (FLAT, snake_case — the UI builds to these).
# --------------------------------------------------------------------------- #
class ApprovalIn(BaseModel):
    """The human approval on a publish request — only ``decision`` is consulted.

    Defaults to ``pending`` so an un-approved publish fans out to all-blocked
    (fail-closed, INV-3): a dispatch reaches ``simulated_sent`` only on
    ``approve`` AND a passing validation.
    """

    decision: Decision = Decision.PENDING


class PublishIn(BaseModel):
    """A ``POST /content/publish`` body (FR-3.6).

    ``body`` is the content text fanned out across ``channels`` (each a member of
    ``params.scheduler.publish_channels`` — an off-list channel is rejected 422).
    ``approval`` is the human review decision applied to every channel (INV-2).
    ``generate_image`` / ``generate_video`` toggle the placeholder media gen ($0,
    OUT-1). ``asset_ref`` / ``candidate_ref`` link the source content; ``audience``
    feeds V-3 (defaults to the COPPA-safe ``general``). ``dispatch_mode`` is NEVER
    accepted — every dispatch is forced simulated by the fan-out (INV-9, OUT-2).
    """

    body: str = Field(min_length=1)
    channels: list[Channel] = Field(min_length=1)
    scheduled_for: str = Field(min_length=1)
    approval: ApprovalIn = Field(default_factory=ApprovalIn)
    campaign_theme: str | None = None
    asset_ref: UUID | None = None
    candidate_ref: UUID | None = None
    audience: str = "general"
    generate_image: bool = False
    generate_video: bool = False


class MediaRefView(BaseModel):
    """One generated media ref the monitor renders (placeholder in v1, $0 OUT-1).

    ``kind`` is ``image``/``video``; ``placeholder_uri`` is the synthetic stand-in
    the dashboard renders; ``asset_url`` is the live URL (``None`` for a placeholder
    — proving no live gen occurred); ``cost_estimate_ref`` is a STRING pointer into
    the §6 cost model, never a numeric price (INV-11, OUT-1).
    """

    kind: str
    placeholder_uri: str
    asset_url: str | None = None
    cost_estimate_ref: str
    is_placeholder: bool
    brief: str | None = None
    render_hint: str | None = None


class DispatchView(BaseModel):
    """Per-platform dispatch row for the monitor dashboard (FLAT, snake_case).

    One row per target channel: the derived post id, the §6 dispatch verdict,
    whether the per-platform daily cap forced the block, the simulated receipt
    (``None`` when blocked), the HubSpot GT Social Post mirror state, and the
    mirrored object id (``None`` until/unless the mirror ran). ``sent``/``blocked``
    are derived booleans so the UI can chip the row without re-deriving the enum.
    """

    post_id: UUID
    channel: Channel
    dispatch_status: DispatchStatus
    sent: bool
    blocked: bool
    capped: bool
    simulated_receipt: str | None = None
    mirror_status: MirrorStatus
    hubspot_object_id: str | None = None


class PublishMonitorView(BaseModel):
    """One persisted publish fan-out — the dual-screen monitor record (FR-3.6).

    The cockpit observability plane row: the request id + the content body fanned
    out, the per-platform ``dispatches`` (BOTH screens), the generated ``media``,
    the request-level ``hubspot_object_id`` (the representative mirrored GT Social
    Post), and the overall ``validation`` (passed + failed_rules so the UI can
    fail-closed). A FAILED validation ⇒ every dispatch blocked + ``validation_passed``
    False — the gate is shown, never softened (INV-4).
    """

    request_id: UUID
    body: str
    scheduled_for: str
    campaign_theme: str | None = None
    dispatches: list[DispatchView] = Field(default_factory=list)
    media: list[MediaRefView] = Field(default_factory=list)
    hubspot_object_id: str | None = None
    validation_passed: bool
    failed_rules: list[str] = Field(default_factory=list)


class PublishResponse(PublishMonitorView):
    """The ``POST /content/publish`` result — the monitor row + the eval-gate flag.

    Extends :class:`PublishMonitorView` (same flat per-platform shape the monitor
    feed renders) and adds ``action_enabled``: the suite-level grounding-eval state
    the UI reads to DISABLE the publish button when the eval is red (INV-3
    fail-closed). When the action is disabled the publish is refused upstream (422),
    so a returned response always carries ``action_enabled=True``.
    """

    action_enabled: bool = True


class PublishActionStatusView(BaseModel):
    """The publish action's eval-gate status the UI reads to enable/disable publish.

    ``action_enabled`` is the suite-level kill: ``False`` iff the consolidated
    ``message_safety_grounding`` row is red (INV-3). ``eval_name`` names the gating
    eval so the UI can explain the block. Mirrors the ``GET /evals`` disabled-map
    pattern, scoped to the publish action.
    """

    action_enabled: bool
    eval_name: str = PUBLISH_EVAL_NAME


# --------------------------------------------------------------------------- #
# Internal helpers.
# --------------------------------------------------------------------------- #
class PublishedRecord(BaseModel):
    """A persisted publish fan-out held in the in-process feed (FR-3.6).

    Carries the aggregated :class:`PublishMonitor` (per-platform tracking + the
    request-level mirror id) plus the content body, schedule, theme, generated
    media refs, and the gate verdict — everything ``GET /publish/monitor`` projects
    into a :class:`PublishMonitorView`. Insertion order is preserved so the feed
    can render newest-first.
    """

    monitor: PublishMonitor
    body: str
    scheduled_for: str
    campaign_theme: str | None
    image: ImageRef | None
    video: VideoRef | None
    validation_passed: bool
    failed_rules: list[str]


def _validation_to_view_fields(validation: ValidationResult) -> tuple[bool, list[str]]:
    """The (passed, failed_rules) pair the views surface from a gate verdict."""
    return validation.passed, list(validation.failed_rules)


def _media_views(image: ImageRef | None, video: VideoRef | None) -> list[MediaRefView]:
    """Project the optional image/video refs into the flat MediaRefView list."""
    views: list[MediaRefView] = []
    if image is not None:
        views.append(
            MediaRefView(
                kind="image",
                placeholder_uri=image.placeholder_uri,
                asset_url=image.asset_url,
                cost_estimate_ref=image.cost_estimate_ref,
                is_placeholder=image.is_placeholder,
                brief=image.brief,
                render_hint=image.render_hint,
            )
        )
    if video is not None:
        views.append(
            MediaRefView(
                kind="video",
                placeholder_uri=video.placeholder_uri,
                asset_url=video.asset_url,
                cost_estimate_ref=video.cost_estimate_ref,
                is_placeholder=video.is_placeholder,
                brief=video.brief,
                render_hint=video.render_hint,
            )
        )
    return views


def _record_to_view(record: PublishedRecord) -> PublishMonitorView:
    """Project a persisted PublishedRecord into the flat monitor view (both screens)."""
    monitor = record.monitor
    dispatches = [
        DispatchView(
            post_id=d.post_id,
            channel=d.channel,
            dispatch_status=d.dispatch_status,
            sent=d.dispatch_status is DispatchStatus.SIMULATED_SENT,
            blocked=d.dispatch_status is DispatchStatus.BLOCKED,
            capped=d.capped,
            simulated_receipt=d.simulated_result,
            mirror_status=d.mirror_status,
            # The dispatch carries its mirror via the request-level id once mirrored;
            # surface the request id on a MIRRORED row so the UI deep-links the
            # second screen per-platform (the simulated id is per-post-derived).
            hubspot_object_id=(
                monitor.hubspot_object_id if d.mirror_status is MirrorStatus.MIRRORED else None
            ),
        )
        for d in monitor.dispatches
    ]
    return PublishMonitorView(
        request_id=monitor.request_id,
        body=record.body,
        scheduled_for=record.scheduled_for,
        campaign_theme=record.campaign_theme,
        dispatches=dispatches,
        media=_media_views(record.image, record.video),
        hubspot_object_id=monitor.hubspot_object_id,
        validation_passed=record.validation_passed,
        failed_rules=list(record.failed_rules),
    )


def _on_brand_judge(_record: object, _never_rules: list[str]) -> float | None:
    """Deterministic V-4 on-brand judge for the publish gate (no live LLM, INV-9).

    Returns a high conformance score so a genuinely on-brand body clears V-4
    without a live call; V-1/V-2/V-3 and the never-rule check still BLOCK banned
    copy regardless (INV-4 — the gate blocks, never softens). Mirrors the injected
    judge in ``app/api/evals.py`` / ``app/api/geo.py``.
    """
    return 0.99


# --------------------------------------------------------------------------- #
# POST /content/publish — the fan-out + dual-screen mirror (FR-3.6; INV-3/4/9).
# --------------------------------------------------------------------------- #
@router.post("/content/publish", response_model=PublishResponse)
def publish_content(
    request: PublishIn,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    crm_adapter: CRMAdapterDep,
    media_adapter: MediaAdapterDep,
    eval_state: EvalStateDep,
) -> PublishResponse:
    """Validate → fan-out → mirror → media → persist → log one publish (FR-3.6).

    INV-3 fail-closed: if the consolidated ``message_safety_grounding`` eval is red,
    the publish action is DISABLED and the request is refused (422) — no dispatch
    reaches a human while the gate is failing. Otherwise the content body crosses
    the EXISTING V-1..V-4 gate (:func:`evaluate_message`); the verdict is fanned out
    by :func:`plan_publish` with caps + channels from ``params.scheduler`` — a
    FAILED validation blocks ALL dispatches (INV-4, the response says so). Each
    mirrorable dispatch is mirrored to HubSpot as a GT Social Post (the simulated
    recorder by default — INV-9; ``CRM_MODE=live`` flips the same call). Requested
    media is generated via the placeholder adapter ($0, OUT-1). The monitor + media
    + body are persisted to the in-process feed and the proposal + eval + human
    decision are logged (NFR-6).
    """
    # INV-3 — the suite-level kill: a red grounding row disables publish entirely.
    if not action_enabled(eval_state, PUBLISH_EVAL_NAME):
        raise HTTPException(
            status_code=422,
            detail=(
                "publish action disabled — the message_safety_grounding eval is red "
                "(fail-closed, INV-3)"
            ),
        )

    # Channels must be a subset of the params-owned publish set (INV-11). An
    # off-list channel (e.g. email/blog/geo) is rejected before any fan-out.
    allowed = set(params.scheduler.publish_channels)
    off_list = [c.value for c in request.channels if c.value not in allowed]
    if off_list:
        raise HTTPException(
            status_code=422,
            detail=(
                f"channels must be a subset of params.scheduler.publish_channels "
                f"{sorted(allowed)}; got off-list {off_list}"
            ),
        )

    request_id = uuid4()
    publish_request = PublishRequest(
        id=request_id,
        channels=tuple(request.channels),
        body=request.body,
        assetRef=request.asset_ref,
        candidateRef=request.candidate_ref,
        scheduledFor=request.scheduled_for,
        campaignTheme=request.campaign_theme,
    )

    # Run the EXISTING V-1..V-4 grounding gate over the content body (INV-2/INV-4).
    # The gate consumes any GatedRecord structurally; a minimal carrier exposes
    # `.copy_text` (content-candidate shape) + empty `.claims` so V-1/V-2 apply.
    gate_record = _BodyRecord(copy_text=request.body)
    validation = evaluate_message(
        gate_record,
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge,
        audience=request.audience,
    )

    approval = HumanDecision(decision=request.approval.decision)
    provenance = Provenance(
        generated_by=GeneratedBy.HUMAN,
        created_at=request.scheduled_for,
    )

    # Fan out: one gated, capped ScheduledPost per channel + the aggregate monitor.
    # A FAILED validation or non-approve forces every channel to BLOCKED (INV-3/4).
    _posts, monitor = plan_publish(
        publish_request,
        validation=validation,
        validation_ref=_VALIDATION_REF,
        approval=approval,
        provenance=provenance,
        daily_caps=params.scheduler.daily_caps,
    )

    # Mirror each mirrorable dispatch into HubSpot as a GT Social Post (the SECOND
    # screen). Default simulated recorder ⇒ in-memory, no network (INV-9);
    # CRM_MODE=live ⇒ the SAME call writes the portal. A blocked/capped dispatch is
    # not mirrorable (is_mirrorable) ⇒ stays SKIPPED, nothing recorded.
    mirror_ids: dict[UUID, str | None] = {}
    for dispatch in monitor.dispatches:
        if is_mirrorable(dispatch):
            mirror_ids[dispatch.post_id] = crm_adapter.mirror_social_post(
                dispatch, request=publish_request
            )
    monitor = apply_mirror_results(monitor, mirror_ids)

    # Requested media via the placeholder adapter ($0, OUT-1; never live in v1).
    image: ImageRef | None = None
    video: VideoRef | None = None
    brief = request.campaign_theme or request.body
    if request.generate_image:
        image = media_adapter.generate_image(MediaSpec(brief=brief, tier="draft"))
    if request.generate_video:
        video = media_adapter.generate_video(MediaSpec(brief=brief, tier="winner"))

    validation_passed, failed_rules = _validation_to_view_fields(validation)

    record = PublishedRecord(
        monitor=monitor,
        body=request.body,
        scheduled_for=request.scheduled_for,
        campaign_theme=request.campaign_theme,
        image=image,
        video=video,
        validation_passed=validation_passed,
        failed_rules=failed_rules,
    )
    # Persist newest-LAST in the feed; GET reverses for newest-first.
    _published_monitors[0].append(record)

    # LOG proposal + eval + the human decision (NFR-6). The payload is the monitor
    # view (per-platform verdicts + media); the eval mirrors the grounding gate's
    # pass; the decision records the operator's approve/other verdict.
    proposal_id = uuid4()
    view = _record_to_view(record)
    log.log_proposal(
        proposal_id=proposal_id,
        flow=PUBLISH_FLOW,
        schema_version=PUBLISH_SCHEMA_VERSION,
        payload=view.model_dump(mode="json"),
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name=PUBLISH_EVAL_NAME,
        passed=validation.passed,
        score=validation.brand_score,
    )
    decision_action = (
        DecisionAction.APPROVE
        if request.approval.decision is Decision.APPROVE
        else DecisionAction.DISCARD
    )
    log.log_decision(
        proposal_id=proposal_id,
        human="publish-operator",
        action=decision_action,
    )

    return PublishResponse(**view.model_dump(), action_enabled=True)


# --------------------------------------------------------------------------- #
# GET /publish/monitor — the cockpit observability plane feed (FR-3.6).
# --------------------------------------------------------------------------- #
@router.get("/publish/monitor", response_model=list[PublishMonitorView])
def get_publish_monitor() -> list[PublishMonitorView]:
    """The persisted publish-monitor feed, newest first (FR-3.6; INV-9).

    The cockpit observability plane: every POST /content/publish fan-out, with its
    per-platform status across BOTH screens (cockpit + HubSpot mirror), generated
    media, and mirror status/ids. Starts empty; in-memory + deterministic, never
    live (INV-9).
    """
    return [_record_to_view(record) for record in reversed(_published_monitors[0])]


# --------------------------------------------------------------------------- #
# GET /publish/status — the eval-gate flag the UI reads to disable publish (INV-3).
# --------------------------------------------------------------------------- #
@router.get("/publish/status", response_model=PublishActionStatusView)
def get_publish_status(eval_state: EvalStateDep) -> PublishActionStatusView:
    """The publish action's eval-gate status — ``action_enabled`` for the UI (INV-3).

    Reads the last consolidated suite verdict (via the dep so overrides are
    honored): ``action_enabled`` is ``False`` iff the ``message_safety_grounding``
    row is red — the suite-level kill the live gate enforces, surfaced so the UI can
    DISABLE the publish button fail-closed. No suite run yet ⇒ enabled (the
    per-message V-1..V-4 gate still guards every publish).
    """
    return PublishActionStatusView(
        action_enabled=action_enabled(eval_state, PUBLISH_EVAL_NAME),
    )


class _BodyRecord(BaseModel):
    """A minimal GatedRecord carrier for the publish body (V-1..V-4 input).

    Exposes ``copy_text`` (the content-candidate text field the gate reads
    structurally) and an empty ``claims`` sequence so V-1/V-2 apply to the body
    alone — there are no separate empirical claims to source on a publish body.
    Defined here (not imported from ``app.ai``) so the gate stays purely structural.
    """

    copy_text: str

    @property
    def claims(self) -> list[str]:
        """No separate claims on a publish body — the body text is gated directly."""
        return []


__all__ = [
    "PublishActionStatusView",
    "PublishMonitorView",
    "PublishResponse",
    "reset_published_monitors",
    "router",
]

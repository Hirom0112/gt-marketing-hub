"""Content-engine endpoints — the FR-3.1/3.4/3.5 critical path (ARCH §5.3/§6).

The composition layer wiring the §5.3 content-generation doctrine into HTTP — the
marketing analog of `app/api/ai_actions.py`. It is deliberately thin; every
decision-bearing step lives in a pure/owned module it orchestrates (INV-2):

  ``POST /ai/content/generate``
    1. assemble brand-conditioned context + run
       :func:`app.ai.graphs.content_generate.generate_content_batch` (conditioning
       → LLM edge → parse → eval gate per candidate → surface-on-pass);
    2. LOG every candidate's proposal + eval to the §10 observability log — a
       WITHHELD (blocked) candidate is STILL logged with its failing eval (INV-4
       audit side); a surfaced candidate's proposal_id is returned to the client;
    3. surface ONLY passing candidates (INV-3). Blocked candidates do not surface.

  ``POST /content/{proposal_id}/decision``
    The sole content state-write path (FR-3.5; INV-2). 404 if the proposal was
    never logged. ``keep`` REQUIRES the logged eval to have passed (else 409 —
    you cannot keep an un-passed candidate) and promotes a kept LibraryAsset +
    affirms brand memory; ``discard`` strengthens a dont signal, publishes
    nothing. Nothing auto-publishes (review gate, FR-3.5).

  ``GET /content/library``
    The FR-3.4 library search — only kept + validated assets, over ``q`` / ``tag``.

This module may import `app.ai` / `app.adapters` / `app.marketing` /
`app.observability` (it is the composition root); `app/core/` stays pure. No live
LLM call is ever made here — the client degrades without a key and tests inject
transports.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.ai.client import LLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.content_generate import (
    ContentBatchOutcome,
    build_campaign_prompt,
    generate_content_batch,
)
from app.ai.schemas.brand import BrandRule, LibraryAsset
from app.ai.schemas.content import ContentCandidate, Decision
from app.api.deps import (
    get_active_brand_rules,
    get_brand_judge,
    get_brand_memory_store_dep,
    get_content_library_dep,
    get_llm_client,
    get_observability_log,
    get_params,
    get_settings_dep,
)
from app.api.schemas import (
    CampaignEcho,
    CampaignGenerateRequest,
    CampaignGenerateResponse,
    CandidateValidationView,
    ContentCandidateResponse,
    ContentDecisionRequest,
    ContentDecisionResponse,
    ContentGenerateRequest,
    ContentGenerateResponse,
)
from app.core.eval_gate import BrandJudge, RuleVerdict, ValidationResult
from app.core.params import Params
from app.core.settings import Settings
from app.marketing.keep_discard import KeepRefused, discard, keep
from app.marketing.library import ContentLibrary
from app.marketing.review_queue import publishes
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["content"])

# The §5.3 generation flow + schema version surfaced on each logged proposal.
CONTENT_FLOW = "content_generate"
CONTENT_SCHEMA_VERSION = "1"
CONTENT_EVAL_NAME = "message_safety_grounding"

# Slice B campaign cap: the max candidates a single campaign batch may request, so a
# campaign is never silently unbounded (INV-8 — bound the metered edge). `params` has no
# content-batch tunable that fits, so this is a documented module constant (CLAUDE §1
# INV-11 allows a documented constant when no canonical param home exists); a `count`
# above it is clamped down, never errored.
CAMPAIGN_COUNT_MAX = 8

# campaign-tagging-on-keep: the namespaced key under which a campaign batch persists
# its axes (theme + target GEO prompt) into the logged proposal payload at GENERATE
# time, so the INV-2 keep path can read them back from the spine (the candidate is
# rebuilt from the log, never client-trusted). It is NOT a ContentCandidate field
# (that schema is frozen + extra="forbid"); it rides ALONGSIDE the candidate dump in
# the proposal payload and is STRIPPED before the candidate is reconstructed. A
# non-campaign proposal has no such key ⇒ no campaign tags on keep (graceful).
CAMPAIGN_AXES_KEY = "_campaign"

# --- dependency aliases (Annotated keeps the call in the type, not a default arg) ---
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
BrandJudgeDep = Annotated["BrandJudge | None", Depends(get_brand_judge)]
StoreDep = Annotated[object, Depends(get_brand_memory_store_dep)]
LibraryDep = Annotated[ContentLibrary, Depends(get_content_library_dep)]
BrandRulesDep = Annotated[list[BrandRule], Depends(get_active_brand_rules)]


@router.post("/ai/content/generate", response_model=ContentGenerateResponse)
def generate_content(
    request: ContentGenerateRequest,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
    brand_judge: BrandJudgeDep,
    store: StoreDep,
    brand_rules: BrandRulesDep,
) -> ContentGenerateResponse:
    """Generate a gated content batch, log each candidate, surface only on pass (§5.3)."""
    # Per-run budget (INV-8) — built per request, never a singleton.
    budget = RunBudget.from_config(settings=settings, params=params)
    outcome = generate_content_batch(
        request.prompt,
        request.channel,
        store=store,  # type: ignore[arg-type]  # BrandMemoryStore boundary
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
        brand_rules=brand_rules,
    )

    return _project_outcome(outcome, log=log)


def _payload_for(
    candidate: ContentCandidate, campaign_axes: dict[str, object] | None
) -> dict[str, object]:
    """The logged proposal payload: the candidate dump + (optional) campaign axes.

    campaign-tagging-on-keep / INV-2: the campaign axes are persisted ALONGSIDE the
    candidate dump under :data:`CAMPAIGN_AXES_KEY` so the keep path can read them back
    from the spine. A non-campaign batch passes ``None`` ⇒ a plain candidate dump.
    """
    payload: dict[str, object] = candidate.model_dump(mode="json")
    if campaign_axes is not None:
        payload[CAMPAIGN_AXES_KEY] = campaign_axes
    return payload


def _project_outcome(
    outcome: ContentBatchOutcome,
    *,
    log: ObservabilityLog,
    campaign_axes: dict[str, object] | None = None,
) -> ContentGenerateResponse:
    """Log every candidate then FLAT-project the batch into a `ContentGenerateResponse`.

    The shared §5.3 surface logic both `/ai/content/generate` and `/ai/content/campaign`
    use: each candidate (surfaced AND withheld) is logged with its eval (INV-4 audit side)
    BEFORE being projected flat. A surfaced candidate carries ``surfaced=True`` +
    ``passed=True`` (keepable); a withheld candidate carries ``surfaced=False`` + its
    ``failed_rules`` so the operator SEES the gate block it — it has a ``proposal_id`` but
    is never keepable (the keep endpoint 409s on an un-passed eval, INV-3).

    ``campaign_axes`` (theme + target GEO prompt) is persisted into each logged proposal
    payload when the batch is a CAMPAIGN batch, so a kept candidate can be tagged with its
    campaign on the INV-2 rebuild-from-spine keep path (campaign-tagging-on-keep).
    """
    candidates: list[ContentCandidateResponse] = []
    batch_id = ""

    for item in outcome.surfaced:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=CONTENT_FLOW,
            schema_version=CONTENT_SCHEMA_VERSION,
            payload=_payload_for(item.candidate, campaign_axes),
        )
        log.log_eval(
            proposal_id=proposal_id,
            eval_name=CONTENT_EVAL_NAME,
            passed=item.validation.passed,
            score=item.validation.brand_score,
        )
        batch_id = batch_id or item.candidate.batch_id
        candidates.append(
            ContentCandidateResponse(
                proposal_id=proposal_id,
                copy=item.candidate.copy_text,
                channel=item.candidate.channel.value,
                surfaced=True,
                degraded=outcome.degraded,
                failed_rules=[],
                validation=CandidateValidationView(passed=item.validation.passed),
            )
        )

    for blocked in outcome.withheld:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=CONTENT_FLOW,
            schema_version=CONTENT_SCHEMA_VERSION,
            payload=_payload_for(blocked.candidate, campaign_axes),
        )
        log.log_eval(
            proposal_id=proposal_id,
            eval_name=CONTENT_EVAL_NAME,
            passed=blocked.validation.passed,
            score=blocked.validation.brand_score,
        )
        batch_id = batch_id or blocked.candidate.batch_id
        candidates.append(
            ContentCandidateResponse(
                proposal_id=proposal_id,
                copy=blocked.candidate.copy_text,
                channel=blocked.candidate.channel.value,
                surfaced=False,
                degraded=outcome.degraded,
                failed_rules=list(blocked.validation.failed_rules),
                validation=CandidateValidationView(passed=blocked.validation.passed),
            )
        )

    return ContentGenerateResponse(
        batch_id=batch_id,
        candidates=candidates,
        blocked_count=outcome.withheld_count,
        degraded=outcome.degraded,
    )


@router.post("/ai/content/campaign", response_model=CampaignGenerateResponse)
def generate_campaign(
    request: CampaignGenerateRequest,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
    brand_judge: BrandJudgeDep,
    store: StoreDep,
    brand_rules: BrandRulesDep,
) -> CampaignGenerateResponse:
    """Generate a gated CAMPAIGN batch for the four axes, surface only on pass (Slice B).

    A THIN composition over the SAME §5.3 spine `/ai/content/generate` uses: build a
    CAMPAIGN PROMPT embedding the theme/channel/audience/(optional)GEO axes, clamp the
    requested ``count`` to :data:`CAMPAIGN_COUNT_MAX` (never silently unbounded — INV-8),
    feed it to the EXISTING :func:`generate_content_batch` (so it conditions on brand
    memory and DEGRADES to persisted exemplars with no key — INV-8), and reuse the same
    log + gate + flat-projection flow as `generate_content`. Returns the SAME flat batch
    PLUS a ``campaign`` echo of the axes.
    """
    count = min(request.count, CAMPAIGN_COUNT_MAX)
    campaign_prompt = build_campaign_prompt(
        theme=request.theme,
        channel=request.channel,
        audience=request.audience.value,
        target_geo_prompt=request.target_geo_prompt,
        count=count,
    )

    # Per-run budget (INV-8) — built per request, never a singleton.
    budget = RunBudget.from_config(settings=settings, params=params)
    outcome = generate_content_batch(
        campaign_prompt,
        request.channel,
        store=store,  # type: ignore[arg-type]  # BrandMemoryStore boundary
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
        brand_rules=brand_rules,
    )

    # Persist the campaign axes into each logged proposal payload (INV-2 spine) so the
    # keep path can read them back and tag the kept asset with its campaign theme +
    # target GEO prompt (campaign-tagging-on-keep). theme is always present; the GEO
    # prompt is optional (None ⇒ no geo tag on keep).
    campaign_axes: dict[str, object] = {"theme": request.theme}
    if request.target_geo_prompt is not None:
        campaign_axes["target_geo_prompt"] = request.target_geo_prompt
    base = _project_outcome(outcome, log=log, campaign_axes=campaign_axes)
    return CampaignGenerateResponse(
        batch_id=base.batch_id,
        candidates=base.candidates,
        blocked_count=base.blocked_count,
        degraded=base.degraded,
        campaign=CampaignEcho(
            theme=request.theme,
            channel=request.channel,
            audience=request.audience,
            target_geo_prompt=request.target_geo_prompt,
        ),
    )


def _candidate_from_payload(payload: dict[str, object]) -> ContentCandidate | None:
    """Reconstruct the surfaced candidate from its logged proposal payload (INV-2).

    Strips the optional :data:`CAMPAIGN_AXES_KEY` side-channel (campaign axes ride
    ALONGSIDE the candidate dump, not inside it — :class:`ContentCandidate` is frozen +
    extra="forbid") before validating. Returns ``None`` if the remainder does not parse
    as a content candidate (a non-content proposal id was addressed to this route) — the
    caller 404s.
    """
    candidate_payload = {k: v for k, v in payload.items() if k != CAMPAIGN_AXES_KEY}
    try:
        return ContentCandidate.model_validate(candidate_payload)
    except ValidationError:
        return None


def _campaign_axes_from_payload(payload: dict[str, object]) -> tuple[str | None, str | None]:
    """Read the persisted campaign axes (theme, target GEO prompt) back from the spine.

    Returns ``(None, None)`` for a non-campaign proposal (no :data:`CAMPAIGN_AXES_KEY`),
    so keep produces no campaign/geo tags (graceful). INV-2: the axes come from the
    logged proposal, never from client input at keep time.
    """
    axes = payload.get(CAMPAIGN_AXES_KEY)
    if not isinstance(axes, dict):
        return (None, None)
    theme = axes.get("theme")
    geo = axes.get("target_geo_prompt")
    return (
        theme if isinstance(theme, str) else None,
        geo if isinstance(geo, str) else None,
    )


def _verdict_from_eval(passed: bool) -> ValidationResult:
    """A minimal :class:`ValidationResult` reflecting the logged eval's pass state.

    The keep path only consults ``validation.passed``; we reconstruct it from the
    already-logged eval (the gate ran at generation time — INV-3) rather than
    re-judging at decision time. A failing eval yields ``passed=False`` so keep
    is refused (409).
    """
    verdict = RuleVerdict.PASS if passed else RuleVerdict.FAIL
    return ValidationResult(
        v1_schema=verdict,
        v2_grounding=verdict,
        v3_coppa=verdict,
        v4_onbrand=verdict,
        passed=passed,
        failed_rules=[] if passed else ["eval_failed"],
    )


@router.post("/content/{proposal_id}/decision", response_model=ContentDecisionResponse)
def decide_content(
    proposal_id: UUID,
    request: ContentDecisionRequest,
    log: LogDep,
    store: StoreDep,
    library: LibraryDep,
    params: ParamsDep,
) -> ContentDecisionResponse:
    """Apply a human keep/discard — the SOLE content state-write path (FR-3.5; INV-2).

    404 if the proposal was never logged (§10 causality). ``keep`` REQUIRES the
    logged eval to have passed (else 409 — INV-3); on keep it promotes a kept
    LibraryAsset + affirms brand memory. ``discard`` strengthens a dont signal and
    publishes nothing. Nothing auto-publishes (review gate, FR-3.5).
    """
    audit = log.get_audit(proposal_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="proposal not found")

    candidate = _candidate_from_payload(audit.proposal.payload)
    if candidate is None:
        raise HTTPException(status_code=404, detail="not a content proposal")

    if not publishes(request.action) and request.action is not Decision.DISCARD:
        # Only keep/approve publish; discard is the only other handled verdict.
        # Any non-publishing, non-discard verdict cannot advance (FR-3.5 fail-closed).
        raise HTTPException(status_code=409, detail="not a keep/discard decision")

    if request.action is Decision.DISCARD:
        discard(proposal_id, candidate=candidate, store=store, log=log, params=params)
        return ContentDecisionResponse(
            proposal_id=proposal_id, action=request.action, published=False
        )

    # KEEP/APPROVE — the publishing path. Require a PASSED eval (INV-3 / FR-4.3).
    eval_passed = bool(audit.evals and audit.evals[-1].passed)
    validation = _verdict_from_eval(eval_passed)
    # Read the campaign axes back from the logged proposal (INV-2 spine; never client
    # input). A non-campaign proposal yields (None, None) ⇒ no campaign/geo tags.
    campaign_theme, target_geo_prompt = _campaign_axes_from_payload(audit.proposal.payload)
    try:
        asset: LibraryAsset = keep(
            proposal_id,
            candidate=candidate,
            validation=validation,
            store=store,
            library=library,
            log=log,
            params=params,
            campaign_theme=campaign_theme,
            target_geo_prompt=target_geo_prompt,
        )
    except KeepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ContentDecisionResponse(
        proposal_id=proposal_id, action=request.action, published=True, library_asset=asset
    )


@router.get("/content/library", response_model=list[LibraryAsset])
def search_library(
    library: LibraryDep,
    q: Annotated[str | None, Query(description="free-text search over search_text")] = None,
    tag: Annotated[list[str] | None, Query(description="require all of these tags")] = None,
) -> list[LibraryAsset]:
    """Search the content library — only kept + validated assets (FR-3.4; §5)."""
    return library.search(search_text=q, tags=tag)

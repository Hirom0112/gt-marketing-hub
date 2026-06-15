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
from app.ai.graphs.content_generate import generate_content_batch
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
    ContentDecisionRequest,
    ContentDecisionResponse,
    ContentGenerateRequest,
    ContentGenerateResponse,
    SurfacedCandidateResponse,
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

    surfaced: list[SurfacedCandidateResponse] = []

    # LOG every surfaced candidate (proposal + passing eval) BEFORE returning it.
    for item in outcome.surfaced:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=CONTENT_FLOW,
            schema_version=CONTENT_SCHEMA_VERSION,
            payload=item.candidate.model_dump(mode="json"),
        )
        log.log_eval(
            proposal_id=proposal_id,
            eval_name=CONTENT_EVAL_NAME,
            passed=item.validation.passed,
            score=item.validation.brand_score,
        )
        surfaced.append(
            SurfacedCandidateResponse(
                proposal_id=proposal_id,
                candidate=item.candidate,
                validation=item.validation,
            )
        )

    # LOG every withheld (blocked) candidate with its FAILING eval — the audit
    # proof that no unverifiable claim escaped (INV-4); it does NOT surface.
    for blocked in outcome.withheld:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=CONTENT_FLOW,
            schema_version=CONTENT_SCHEMA_VERSION,
            payload=blocked.candidate.model_dump(mode="json"),
        )
        log.log_eval(
            proposal_id=proposal_id,
            eval_name=CONTENT_EVAL_NAME,
            passed=blocked.validation.passed,
            score=blocked.validation.brand_score,
        )

    return ContentGenerateResponse(
        candidates=surfaced,
        blocked_count=outcome.withheld_count,
        degraded=outcome.degraded,
    )


def _candidate_from_payload(payload: dict[str, object]) -> ContentCandidate | None:
    """Reconstruct the surfaced candidate from its logged proposal payload (INV-2).

    Returns ``None`` if the payload does not parse as a content candidate (a
    non-content proposal id was addressed to this route) — the caller 404s.
    """
    try:
        return ContentCandidate.model_validate(payload)
    except ValidationError:
        return None


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
    try:
        asset: LibraryAsset = keep(
            proposal_id,
            candidate=candidate,
            validation=validation,
            store=store,
            library=library,
            log=log,
            params=params,
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

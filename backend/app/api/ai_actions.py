"""Eval-gated AI action endpoints — the FR-2.4 critical path (ARCH §5.2/§6).

This router is the composition layer that wires the §5.2 draft doctrine into
HTTP. It is the ONLY place an AI output is applied to (simulated) state, and it
is deliberately thin: every decision-bearing step lives in a pure/owned module
it merely orchestrates (CLAUDE §1, INV-2):

  ``POST /ai/enrollment/draft``
    1. load the grounded `JoinedFamily` (404 if unknown);
    2. run :func:`app.ai.graphs.enrollment_draft.draft_enrollment_message`
       (context pack → LLM edge → parse → eval gate → surface-on-pass);
    3. LOG the proposal, then its eval, to the §10 observability log REGARDLESS
       of pass/fail — a blocked proposal is still logged (INV-4 audit side);
    4. surface the proposal body **only** when ``outcome.surfaced`` is True; on a
       block/degrade return ``surfaced=False`` + the failing rules + a usable
       ``proposal_id`` but NO proposal body (the UI offers the template fallback).

  ``POST /proposals/{proposal_id}/decision``
    The sole state-applying path (ARCH §6; NFR-6). 404 if the proposal was never
    logged. Logs the human decision. On ``approve``: perform a SIMULATED send via
    the CRM adapter (records, never sends — INV-9) and recompute the family's
    §4.7 seam status (derive-and-return; A-7). On ``edit``/``discard``: log only,
    no send.

  ``GET /proposals`` / ``GET /proposals/{proposal_id}``
    The §10 audit view — proposal + evals + decisions (NFR-6). 404 on unknown id.

This module may import `app.ai` / `app.adapters` / `app.observability` (it is the
composition root); `app/core/` stays pure (it does not). No live LLM call is ever
made here — the client degrades without a key and tests inject transports.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.ai.client import LLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.enrollment_draft import draft_enrollment_message
from app.api.deps import (
    get_brand_judge,
    get_crm_adapter_dep,
    get_eval_state,
    get_llm_client,
    get_observability_log,
    get_params,
    get_repository,
    get_settings_dep,
)
from app.api.schemas import (
    AuditResponse,
    DecisionRequest,
    DecisionResponse,
    DraftRequest,
    DraftResponse,
)
from app.core.eval_gate import BrandJudge, action_enabled
from app.core.params import Params
from app.core.seam import derive_seam_status
from app.core.settings import Settings
from app.data.repository import FamilyRepository
from app.evals.suite import EvalSuiteResult
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["ai-actions"])

# The §5.2 draft schema version surfaced on each logged proposal (the audit head).
DRAFT_FLOW = "enrollment_draft"
DRAFT_SCHEMA_VERSION = "1"
# The audited reviewer identity. v1 has no auth; the operator is a fixed seam (A-3).
DEFAULT_HUMAN = "operator"

# The consolidated suite eval whose RED row kills the enrollment-draft action,
# fail-closed in the LIVE path (FR-4.5; INV-3) — the same eval name the per-draft
# grounding eval logs under, lifted to the suite-level kill.
DRAFT_GATING_EVAL = "message_safety_grounding"
# The signal added to `failed_rules` when the suite-level kill suppresses a draft —
# distinct from the per-message V-1..V-4 rule names so the UI can tell them apart.
EVAL_SUITE_RED_RULE = "eval_suite_red"

# --- dependency aliases (Annotated keeps the call in the type, not a default arg) ---
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
BrandJudgeDep = Annotated["BrandJudge | None", Depends(get_brand_judge)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
# Injected as a Depends so tests can override the live suite-level kill state.
EvalStateDep = Annotated["EvalSuiteResult | None", Depends(get_eval_state)]


@router.post("/ai/enrollment/draft", response_model=DraftResponse)
def draft_enrollment(
    request: DraftRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
    brand_judge: BrandJudgeDep,
    eval_state: EvalStateDep,
) -> DraftResponse:
    """Draft an enrollment message, eval-gate it, log it, surface only on pass (§5.2).

    The SUITE-LEVEL kill rides on top of the per-message gate (FR-4.5; INV-3): if
    the last consolidated suite has the ``message_safety_grounding`` row RED, the
    action is disabled in the LIVE path — the proposal is still produced and LOGGED
    (INV-4 audit side), but it is NOT surfaced (``surfaced=False``, no body) and
    ``eval_suite_red`` is added to ``failed_rules``. Fail-closed: a red eval
    disables the action, not merely the UI.
    """
    joined = repository.get_family(request.family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    # Suite-level kill (FR-4.5; INV-3): a RED consolidated row disables surfacing
    # in the live path. Computed BEFORE the gate so it can suppress an otherwise
    # passing proposal; the proposal is still produced + logged below (INV-4).
    eval_suite_red = not action_enabled(eval_state, DRAFT_GATING_EVAL)

    # Per-run budget (INV-8) — built per request, never a singleton.
    budget = RunBudget.from_config(settings=settings, params=params)
    outcome = draft_enrollment_message(
        joined,
        request.action,
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
    )

    # LOG before a human sees anything (ARCH §10). A new id per attempt: the
    # observability spine is append-only and keyed once per proposal.
    proposal_id = uuid4()
    payload: dict[str, object] = (
        outcome.proposal.model_dump(mode="json") if outcome.proposal is not None else {}
    )
    log.log_proposal(
        proposal_id=proposal_id,
        flow=DRAFT_FLOW,
        schema_version=DRAFT_SCHEMA_VERSION,
        payload=payload,
        family_id=request.family_id,
    )
    # Log the eval REGARDLESS of pass/fail — a blocked proposal is still logged
    # with its failing eval (INV-4 audit side). A parse failure yields no
    # validation (INV-2), so we record a failed schema eval to keep the audit
    # chain complete (every surfaced/blocked attempt has an eval record).
    validation = outcome.validation
    log.log_eval(
        proposal_id=proposal_id,
        eval_name="message_safety_grounding",
        passed=validation.passed if validation is not None else False,
        score=validation.brand_score if validation is not None else None,
    )

    failed_rules = list(validation.failed_rules) if validation is not None else ["v1_schema"]
    # A RED consolidated suite kills surfacing even when the per-message gate passed
    # (FR-4.5; INV-3 fail-closed). The proposal was already LOGGED above (INV-4) —
    # the kill suppresses SURFACING, not logging.
    surfaced = outcome.surfaced and not eval_suite_red
    if eval_suite_red and EVAL_SUITE_RED_RULE not in failed_rules:
        failed_rules.append(EVAL_SUITE_RED_RULE)
    return DraftResponse(
        proposal_id=proposal_id,
        surfaced=surfaced,
        degraded=outcome.degraded,
        failed_rules=failed_rules,
        # Surface the body ONLY on pass AND when the suite is not red (§5.2 step 5;
        # FR-4.5). On block/degrade/kill the client gets no usable proposal — the
        # UI offers the deterministic template.
        proposal=outcome.proposal if surfaced else None,
        validation=validation,
    )


@router.post("/proposals/{proposal_id}/decision", response_model=DecisionResponse)
def decide_proposal(
    proposal_id: UUID,
    request: DecisionRequest,
    repository: RepositoryDep,
    log: LogDep,
    crm_adapter: CRMAdapterDep,
) -> DecisionResponse:
    """Apply a human verdict — the SOLE state-applying path (ARCH §6; NFR-6).

    404 if the proposal was never logged (§10 causality). Logs the decision. On
    ``approve``: simulate a send (INV-9) and recompute the §4.7 seam; on
    edit/discard: log only, no send.
    """
    if log.get_audit(proposal_id) is None:
        raise HTTPException(status_code=404, detail="proposal not found")

    log.log_decision(
        proposal_id=proposal_id,
        human=DEFAULT_HUMAN,
        action=request.action,
        edited_payload=request.edited_payload if request.action is DecisionAction.EDIT else None,
    )

    if request.action is not DecisionAction.APPROVE:
        # Edit / discard: decision recorded, nothing sent, no state derived.
        return DecisionResponse(proposal_id=proposal_id, action=request.action)

    # APPROVE: the only branch that applies an AI output to (simulated) state.
    audit = log.get_audit(proposal_id)
    assert audit is not None  # re-checked above; narrows for the type checker.
    family_id = audit.proposal.family_id

    # SIMULATED send (INV-9): the adapter records, never sends.
    channel = str(audit.proposal.payload.get("action", "email"))
    send = crm_adapter.send_message({"channel": channel, "proposal_id": str(proposal_id)})

    # Recompute the §4.7 seam from the adapter mirror (A-7: derive-and-return; the
    # in-memory repo is read-only per A-3, so we do not persist the new status).
    seam_status = None
    if family_id is not None:
        joined = repository.get_family(family_id)
        if joined is not None:
            mirror = crm_adapter.read_mirror(family_id)
            seam_status = derive_seam_status(joined.family, mirror)

    return DecisionResponse(
        proposal_id=proposal_id,
        action=request.action,
        send_simulated=send.simulated,
        seam_status=seam_status,
    )


@router.get("/proposals", response_model=list[AuditResponse])
def list_proposals(log: LogDep) -> list[AuditResponse]:
    """The §10 audit index — every proposal with its evals + decisions (NFR-6)."""
    views = [log.get_audit(record.proposal_id) for record in log.list_proposals()]
    return [
        AuditResponse(proposal=view.proposal, evals=view.evals, decisions=view.decisions)
        for view in views
        if view is not None
    ]


@router.get("/proposals/{proposal_id}", response_model=AuditResponse)
def get_proposal(proposal_id: UUID, log: LogDep) -> AuditResponse:
    """The §10 audit view for one proposal (NFR-6). 404 on unknown id."""
    view = log.get_audit(proposal_id)
    if view is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return AuditResponse(proposal=view.proposal, evals=view.evals, decisions=view.decisions)

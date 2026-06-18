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

from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.ai.client import LLMClient
from app.ai.cost import run_budget_for_today
from app.ai.graphs.close_tips import generate_close_tips
from app.ai.graphs.enrollment_draft import (
    draft_enrollment_message,
    draft_enrollment_message_ungated,
)
from app.ai.schemas.enrollment_draft import DraftAction
from app.api.deps import (
    get_brand_judge,
    get_crm_adapter_dep,
    get_eval_state,
    get_llm_client,
    get_notes_repository,
    get_observability_log,
    get_params,
    get_repository,
    get_settings_dep,
)
from app.api.merge import MERGE_FLOW
from app.api.schemas import (
    AuditResponse,
    BulkNudgeBlocked,
    BulkNudgeCounts,
    BulkNudgeRequest,
    BulkNudgeResponse,
    BulkNudgeSent,
    CloseTipsRequest,
    CloseTipsResponse,
    DecisionRequest,
    DecisionResponse,
    DraftRequest,
    DraftResponse,
    UngatedDraftRequest,
    UngatedDraftResponse,
)
from app.core.eval_gate import BrandJudge, action_enabled
from app.core.notes import Note, NoteAuthor, NoteKind, summarize_followup
from app.core.params import Params
from app.core.seam import derive_seam_status
from app.core.settings import Settings
from app.data.notes_repository import NotesRepository
from app.data.repository import FamilyRepository
from app.evals.suite import EvalSuiteResult
from app.observability.log_store import AuditView, DecisionAction, ObservabilityLog

router = APIRouter(tags=["ai-actions"])

# The §5.2 draft schema version surfaced on each logged proposal (the audit head).
DRAFT_FLOW = "enrollment_draft"
DRAFT_SCHEMA_VERSION = "1"

# D-1 — the UNGATED detail-panel draft (no eval gate; the human is the final gate).
# A distinct audit head so the observability log keeps the ungated proposals apart
# from the eval-gated ``enrollment_draft`` ones (NFR-6).
DRAFT_UNGATED_FLOW = "enrollment_draft_ungated"
DRAFT_UNGATED_SCHEMA_VERSION = "1"

# D-1 channel → shared DraftAction map. The panel speaks email/sms; the shared
# enum is NOT extended (the eval suite iterates it). sms ⇒ NUDGE (the short-message
# form). The requested channel string is echoed back so the UI labels the draft.
_UNGATED_CHANNEL_TO_ACTION: dict[str, DraftAction] = {
    "email": DraftAction.EMAIL,
    "sms": DraftAction.NUDGE,
}
# The audited reviewer identity. v1 has no auth; the operator is a fixed seam (A-3).
DEFAULT_HUMAN = "operator"

# The consolidated suite eval whose RED row kills the enrollment-draft action,
# fail-closed in the LIVE path (FR-4.5; INV-3) — the same eval name the per-draft
# grounding eval logs under, lifted to the suite-level kill.
DRAFT_GATING_EVAL = "message_safety_grounding"
# The signal added to `failed_rules` when the suite-level kill suppresses a draft —
# distinct from the per-message V-1..V-4 rule names so the UI can tell them apart.
EVAL_SUITE_RED_RULE = "eval_suite_red"

# S9 W5 close-tips audit head + the suite eval whose RED row kills the close-tips
# action in the LIVE path, fail-closed (INV-3) — the same name the suite's
# close-tips row reports under (`app.evals.suite.CLOSE_TIPS`).
CLOSE_TIPS_FLOW = "close_tips"
CLOSE_TIPS_SCHEMA_VERSION = "1"
CLOSE_TIPS_GATING_EVAL = "close_tips"
CLOSE_TIPS_EVAL_NAME = "close_tips_grounding"

# --- dependency aliases (Annotated keeps the call in the type, not a default arg) ---
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
BrandJudgeDep = Annotated["BrandJudge | None", Depends(get_brand_judge)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
NotesRepositoryDep = Annotated[NotesRepository, Depends(get_notes_repository)]
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

    # Per-run budget (INV-8), PRE-TRIPPED if today's logged spend hit the cross-run
    # DAILY cap (NFR-5): a tripped budget degrades the edge to the deterministic
    # template with NO live call — the same fail-closed path as the per-run switch.
    budget = run_budget_for_today(
        settings=settings, params=params, log=log, today=datetime.now(UTC).date()
    )
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
    # observability spine is append-only and keyed once per proposal. Stamp the
    # run's USD (budget.usd_spent) so the cross-run daily accumulator can sum it.
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
        usd_spent=budget.usd_spent,
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


@router.post("/ai/enrollment/draft-ungated", response_model=UngatedDraftResponse)
def draft_enrollment_ungated(
    request: UngatedDraftRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
) -> UngatedDraftResponse:
    """Draft an email/SMS for the detail panel WITHOUT the eval gate (D-1; INV-2).

    The redesigned panel wants a real LLM draft the operator edits + sends MANUALLY
    — the human is the hard final gate (DECISIONS.md D-1, a brief override of
    INV-3/INV-4 for this surface only). INV-2 still holds: the result is a logged
    `proposal`, never an auto-send (no CRM send happens here). This is a NEW path
    ALONGSIDE the eval-gated ``/ai/enrollment/draft`` — that route is untouched.

    Loads the grounded family (404 if unknown), builds the per-today INV-8 budget
    (a tripped daily cap degrades the edge to the operator template with no live
    call), runs the ungated pipeline, and LOGS the proposal for the audit (NFR-6)
    under :data:`DRAFT_UNGATED_FLOW`. NO eval is run or logged — this path is
    ungated by design (D-1). ``degraded`` mirrors the edge: True iff the LLM was
    unavailable (no key / kill switch) or the budget tripped (INV-8).
    """
    joined = repository.get_family(request.family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    action = _UNGATED_CHANNEL_TO_ACTION[request.channel]

    # Per-run budget (INV-8), PRE-TRIPPED if today's logged spend hit the cross-run
    # DAILY cap (NFR-5): a tripped budget degrades the edge to the deterministic
    # template with NO live call — the same fail-closed path as the gated route.
    budget = run_budget_for_today(
        settings=settings, params=params, log=log, today=datetime.now(UTC).date()
    )
    proposal = draft_enrollment_message_ungated(
        joined,
        action,
        client=client,
        budget=budget,
        settings=settings,
    )
    # The edge degraded iff the LLM was unavailable (no key / kill switch) or the
    # budget tripped (pre-tripped daily cap OR a mid-run per-run breach, which
    # leaves the shared budget tripped) — the same condition the client used to
    # return the template. Read here at the composition root (core stays clock-free).
    degraded = (not settings.llm_available) or budget.tripped

    # LOG before a human sees anything (ARCH §10; NFR-6) — a new id per attempt.
    # Stamp the run's USD so the cross-run daily accumulator can sum it. No eval is
    # logged: this path is ungated by design (D-1), and no send happens here.
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=DRAFT_UNGATED_FLOW,
        schema_version=DRAFT_UNGATED_SCHEMA_VERSION,
        payload=proposal.model_dump(mode="json"),
        family_id=request.family_id,
        usd_spent=budget.usd_spent,
    )
    return UngatedDraftResponse(
        proposal_id=proposal_id,
        channel=request.channel,
        degraded=degraded,
        body=proposal.body,
        claims=list(proposal.claims),
    )


def _batch_id(prefix: str, family_ids: list[UUID], *, salt: str = "") -> str:
    """A deterministic ``batch_id`` tagging one bulk audit group (NFR-6; A-20).

    Derived (uuid5) from the prefix + the SORTED family ids + an optional salt so
    the same selection yields the same id — a stable handle the UI can correlate
    the partition against, without a second write path. Sorted so selection order
    does not change the id.
    """
    key = f"{prefix}:{salt}:" + ",".join(sorted(str(fid) for fid in family_ids))
    return f"{prefix}-{uuid5(NAMESPACE_URL, key).hex}"


@router.post("/ai/enrollment/bulk-nudge", response_model=BulkNudgeResponse)
def bulk_nudge(
    request: BulkNudgeRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
    brand_judge: BrandJudgeDep,
    eval_state: EvalStateDep,
    crm_adapter: CRMAdapterDep,
    notes: NotesRepositoryDep,
) -> BulkNudgeResponse:
    """Bulk-nudge a selection — a THIN loop over the per-family gated path (A-20).

    NOT a new write path: each family runs the SAME draft + eval gate as
    :func:`draft_enrollment` (INV-3 fail-closed, per-family non-negotiable). The
    operator's single bulk click is the batch human-approval (INV-2). For each
    family within the INV-8 per-run cap:

    * the proposal + its eval are LOGGED regardless of pass/fail (INV-4 audit side);
    * eval-PASS ⇒ a send is recorded via the SIMULATED CRM adapter (INV-9), an
      approve DECISION is logged (the audit head), and the family is ``sent``;
    * eval-FAIL (or suite-red kill) ⇒ the family is ``blocked`` with its
      ``failed_rules`` — NO send, NO approve-decision (fail-closed, INV-3/4).

    Families beyond ``params.bulk.nudge_per_run_cap`` are deferred to ``capped``
    without drafting — the metered edge is never overspent (INV-8). One
    ``batch_id`` tags the whole group (NFR-6). No second write path: this reuses
    the exact draft/gate/send/log composition of the single route.
    """
    batch_id = _batch_id("bulk-nudge", request.family_ids, salt=request.action.value)
    cap = params.bulk.nudge_per_run_cap
    # Read the wall clock ONCE at the composition root (core stays clock-free); each
    # per-family budget below is pre-tripped if today's logged spend hit the daily cap.
    today = datetime.now(UTC).date()

    eval_suite_red = not action_enabled(eval_state, DRAFT_GATING_EVAL)

    sent: list[BulkNudgeSent] = []
    blocked: list[BulkNudgeBlocked] = []
    capped: list[UUID] = []

    for index, family_id in enumerate(request.family_ids):
        # INV-8 per-run cap: beyond the cap, defer (never overspend the edge).
        if index >= cap:
            capped.append(family_id)
            continue

        joined = repository.get_family(family_id)
        if joined is None:
            blocked.append(BulkNudgeBlocked(family_id=family_id, failed_rules=["family_not_found"]))
            continue

        # The SAME per-family draft pipeline as the single route (INV-3). The budget
        # is pre-tripped if today's logged spend hit the cross-run DAILY cap (NFR-5),
        # so the metered edge degrades fail-closed across the whole batch.
        budget = run_budget_for_today(settings=settings, params=params, log=log, today=today)
        outcome = draft_enrollment_message(
            joined,
            request.action,
            client=client,
            budget=budget,
            settings=settings,
            params=params,
            brand_judge=brand_judge,
        )

        # LOG the proposal + its eval before any human sees it (ARCH §10; INV-4).
        # Stamp the run's USD so the cross-run daily accumulator can sum it.
        proposal_id = uuid4()
        payload: dict[str, object] = (
            outcome.proposal.model_dump(mode="json") if outcome.proposal is not None else {}
        )
        log.log_proposal(
            proposal_id=proposal_id,
            flow=DRAFT_FLOW,
            schema_version=DRAFT_SCHEMA_VERSION,
            payload=payload,
            family_id=family_id,
            usd_spent=budget.usd_spent,
        )
        validation = outcome.validation
        log.log_eval(
            proposal_id=proposal_id,
            eval_name="message_safety_grounding",
            passed=validation.passed if validation is not None else False,
            score=validation.brand_score if validation is not None else None,
        )

        failed_rules = list(validation.failed_rules) if validation is not None else ["v1_schema"]
        surfaced = outcome.surfaced and not eval_suite_red
        if eval_suite_red and EVAL_SUITE_RED_RULE not in failed_rules:
            failed_rules.append(EVAL_SUITE_RED_RULE)

        if not surfaced:
            # Fail-closed: the eval blocked it. Logged above (audit), NEVER sent,
            # and NO approve-decision is recorded (INV-3/4).
            blocked.append(BulkNudgeBlocked(family_id=family_id, failed_rules=failed_rules))
            continue

        # PASS ⇒ the bulk click is the batch approval: record the approve decision
        # (the sole decision shape) + a SIMULATED send via the adapter (INV-9), the
        # SAME composition as the single decision route.
        log.log_decision(
            proposal_id=proposal_id,
            human=DEFAULT_HUMAN,
            action=DecisionAction.APPROVE,
        )
        channel = request.action.value
        body_excerpt = str(payload.get("body", ""))
        send = crm_adapter.send_message(
            {
                "channel": channel,
                "proposal_id": str(proposal_id),
                "family_id": str(family_id),
                "body": summarize_followup(channel, body_excerpt),
            }
        )
        notes.add_note(
            Note(
                family_id=family_id,
                author=NoteAuthor.SYSTEM,
                kind=NoteKind.STATE_CHANGE,
                body=summarize_followup(channel, body_excerpt),
                created_at=datetime.now(UTC),
            )
        )
        sent.append(BulkNudgeSent(family_id=family_id, note_id=send.recorded_id))

    return BulkNudgeResponse(
        batch_id=batch_id,
        counts=BulkNudgeCounts(sent=len(sent), blocked=len(blocked), capped=len(capped)),
        sent=sent,
        blocked=blocked,
        capped=capped,
    )


@router.post("/ai/enrollment/close-tips", response_model=CloseTipsResponse)
def close_tips(
    request: CloseTipsRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    settings: SettingsDep,
    log: LogDep,
    client: LLMClientDep,
    brand_judge: BrandJudgeDep,
    eval_state: EvalStateDep,
) -> CloseTipsResponse:
    """Generate eval-gated "how to close this family" tips, log, surface on pass.

    Mirrors the §5.2 draft flow (INV-2/3/4): build the grounded context pack from
    ``app_form.extracted_fields``, run the close-tips pipeline, LOG the proposal +
    its eval REGARDLESS of pass/fail (INV-4 audit side), and surface the tips body
    ONLY when the eval passed AND the consolidated ``close_tips`` suite row is not
    RED. A red suite row disables the action in the LIVE path (FR-4.5; INV-3),
    not merely the UI — the proposal is still produced + logged, but not surfaced
    (``surfaced=False``, no body) with ``eval_suite_red`` added to ``failed_rules``.

    Close-tips are advisory (read-only): there is no outbound send, so the proposal
    is logged for the audit (NFR-6) but not routed through the send/decision path.
    """
    joined = repository.get_family(request.family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    # Suite-level kill (FR-4.5; INV-3): a RED consolidated close-tips row disables
    # surfacing in the live path. Computed BEFORE the pipeline so it can suppress an
    # otherwise-passing proposal; the proposal is still produced + logged below.
    eval_suite_red = not action_enabled(eval_state, CLOSE_TIPS_GATING_EVAL)

    # Per-run budget pre-tripped if today's logged spend hit the cross-run DAILY cap
    # (NFR-5) ⇒ the edge degrades fail-closed with no live call.
    budget = run_budget_for_today(
        settings=settings, params=params, log=log, today=datetime.now(UTC).date()
    )
    outcome = generate_close_tips(
        joined,
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
    )

    # LOG before a human sees anything (ARCH §10) — a new id per attempt. Stamp the
    # run's USD so the cross-run daily accumulator can sum it.
    proposal_id = uuid4()
    payload: dict[str, object] = (
        outcome.proposal.model_dump(mode="json") if outcome.proposal is not None else {}
    )
    log.log_proposal(
        proposal_id=proposal_id,
        flow=CLOSE_TIPS_FLOW,
        schema_version=CLOSE_TIPS_SCHEMA_VERSION,
        payload=payload,
        family_id=request.family_id,
        usd_spent=budget.usd_spent,
    )
    # Log the eval REGARDLESS of pass/fail — a blocked proposal is still logged with
    # its failing eval (INV-4 audit side). A parse failure / degraded edge yields no
    # validation (INV-2), recorded as a failed eval to keep the audit chain complete.
    validation = outcome.validation
    log.log_eval(
        proposal_id=proposal_id,
        eval_name=CLOSE_TIPS_EVAL_NAME,
        passed=validation.passed if validation is not None else False,
        score=validation.brand_score if validation is not None else None,
    )

    failed_rules = list(validation.failed_rules) if validation is not None else ["v1_schema"]
    surfaced = outcome.surfaced and not eval_suite_red
    if eval_suite_red and EVAL_SUITE_RED_RULE not in failed_rules:
        failed_rules.append(EVAL_SUITE_RED_RULE)
    return CloseTipsResponse(
        proposal_id=proposal_id,
        surfaced=surfaced,
        degraded=outcome.degraded,
        failed_rules=failed_rules,
        proposal=outcome.proposal if surfaced else None,
        validation=validation,
    )


def _apply_merge(
    proposal_id: UUID,
    audit: AuditView,
    notes: NotesRepository,
) -> DecisionResponse:
    """Apply an approved identity-merge fold — DETERMINISTIC + SIMULATED (INV-2/INV-9).

    The deterministic core owns this write (INV-2): only after the logged human
    ``approve`` (recorded by the caller) does the duplicate fold into the primary.
    In v1 the fold is SIMULATED (INV-9) — NO live HubSpot/Supabase mutation and NO
    ``service_role`` cross-family delete: it records a system-authored fold note on
    the SURVIVING primary family (the audit-visible "what happened") and returns
    the recorded decision. The merge surface is not an outbound, so the CRM nudge
    send/seam path is deliberately NOT exercised here.

    The proposal payload carries the fold targets (the pure
    :class:`app.core.identity.MergeProposal` the merge-queue logged); reads default
    safely so a malformed payload never raises.
    """
    payload = audit.proposal.payload
    primary_raw = payload.get("primary_family_id")
    duplicate_raw = payload.get("duplicate_family_id")
    primary_id = audit.proposal.family_id
    if primary_id is None and isinstance(primary_raw, str):
        primary_id = UUID(primary_raw)

    # Record the SIMULATED fold as a deterministic system note on the survivor so
    # the audit shows the merge resolution (INV-2 — a core-authored state_change,
    # not an LLM call). No live cross-family write happens in v1 (INV-9).
    if primary_id is not None:
        duplicate_label = str(duplicate_raw) if duplicate_raw is not None else "duplicate"
        notes.add_note(
            Note(
                family_id=primary_id,
                author=NoteAuthor.SYSTEM,
                kind=NoteKind.STATE_CHANGE,
                body=(
                    f"Merge approved: folded duplicate household {duplicate_label} into "
                    f"primary {primary_id} (simulated — no live CRM/DB write in v1)."
                ),
                created_at=datetime.now(UTC),
            )
        )

    # No send, no seam recompute: a fold is not an outbound. ``send_simulated``
    # stays True (this WAS a simulated apply) so the response reads as applied.
    return DecisionResponse(
        proposal_id=proposal_id,
        action=DecisionAction.APPROVE,
        send_simulated=True,
    )


@router.post("/proposals/{proposal_id}/decision", response_model=DecisionResponse)
def decide_proposal(
    proposal_id: UUID,
    request: DecisionRequest,
    repository: RepositoryDep,
    log: LogDep,
    crm_adapter: CRMAdapterDep,
    notes: NotesRepositoryDep,
) -> DecisionResponse:
    """Apply a human verdict — the SOLE state-applying path (ARCH §6; NFR-6).

    404 if the proposal was never logged (§10 causality). Logs the decision, then
    branches on the proposal KIND via its ``flow`` discriminator (the audit head):

    - an ``identity_merge`` proposal (:data:`app.api.merge.MERGE_FLOW`) ⇒ approve
      applies the dedup FOLD deterministically + SIMULATED (INV-2/INV-9): a
      system-authored fold note on the surviving primary family + the proposal is
      marked applied, with NO CRM nudge send (the merge surface is not an outbound)
      and no live cross-family Supabase mutation in v1;
    - any other flow (a ``enrollment_draft`` nudge) ⇒ approve keeps its UNCHANGED
      behavior: simulate a send (INV-9), append a DETERMINISTIC follow-up auto-note
      (A-8; INV-2 — not an LLM call), and recompute the §4.7 seam.

    On edit/discard: log only, no send, no fold, no note (fail-closed, INV-4 — a
    discard NEVER merges and keeps the households separate). The family's
    ``last_contact_at`` derives from the logged approve decision (A-14).
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
        # Edit / discard: decision recorded, nothing sent/folded, no state derived.
        # For a merge proposal this is the fail-closed "keep them separate" path.
        return DecisionResponse(proposal_id=proposal_id, action=request.action)

    # APPROVE: the only branch that applies a proposal to (simulated) state.
    audit = log.get_audit(proposal_id)
    assert audit is not None  # re-checked above; narrows for the type checker.

    # Merge proposals fold rather than send — branch on the flow discriminator.
    if audit.proposal.flow == MERGE_FLOW:
        return _apply_merge(proposal_id, audit, notes)

    family_id = audit.proposal.family_id

    # Send through the CRM adapter — mode-agnostic (INV-9): the simulated recorder
    # records (never sends); the LIVE adapter writes a HubSpot Note, resolving the
    # contact/deal by gt_synthetic_id from `family_id` (S10 W3). The message
    # carries `family_id` (for live id resolution) and the draft `body` (so the
    # live Note body matches the deterministic auto-note). This live note fires
    # ONLY here, post-approval, from the deterministic decision route (INV-2).
    channel = str(audit.proposal.payload.get("action", "email"))
    body_excerpt = str(audit.proposal.payload.get("body", ""))
    send = crm_adapter.send_message(
        {
            "channel": channel,
            "proposal_id": str(proposal_id),
            "family_id": str(family_id) if family_id is not None else None,
            "body": summarize_followup(channel, body_excerpt),
        }
    )

    # Append a DETERMINISTIC follow-up auto-note (A-8; INV-2): a system-authored
    # state_change record of the simulated send, built by the pure core builder
    # from the logged draft body — NOT an LLM call. The note's `created_at` is the
    # only wall-clock read, set here at the composition root (core stays clock-free,
    # same pattern as `api/notes.py`); the recency status itself derives from the
    # logged approve DECISION (A-14), so no new field is written.
    if family_id is not None:
        notes.add_note(
            Note(
                family_id=family_id,
                author=NoteAuthor.SYSTEM,
                kind=NoteKind.STATE_CHANGE,
                body=summarize_followup(channel, body_excerpt),
                created_at=datetime.now(UTC),
            )
        )

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
        # The adapter's recorded send id — under CRM_MODE=live the live HubSpot
        # Note id, so the cockpit can deep-link the captured note (S10 W3).
        note_id=send.recorded_id,
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

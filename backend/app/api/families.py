"""Read-only Family / pipeline endpoints (ARCHITECTURE.md §6; FR-2.1/2.2).

All GET, all deterministic, no AI (INV-2 for the S0 landing slice). Every route
reads through the :class:`FamilyRepository` seam (`deps.get_repository`), so the
store is swappable to Supabase with zero changes here (NFR-8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_observability_log, get_params, get_repository
from app.api.schemas import (
    CalendarEntry,
    CalendarResponse,
    FamilyDetailResponse,
    HouseholdGroup,
    PipelineResponse,
    StudentBoardResponse,
    StudentRow,
    WorkQueueItem,
)
from app.core.contact_log import last_contact_at
from app.core.contact_status import ContactStatus, derive_contact_status
from app.core.family_record import assemble_deal_view
from app.core.params import Params
from app.core.recovery_state import (
    RecoveredOutcome,
    RecoveryState,
    derive_recovery_state,
    derive_student_recovery_state,
    is_active,
    recovered_outcome,
)
from app.core.work_queue import (
    WorkQueueFamily,
    WorkQueueStudent,
    freshness,
    recoverability,
    recoverable_now,
    recoverable_now_student,
    responsiveness_from_engagement,
    score_family,
    score_student,
    student_value,
    value,
)
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage, StallReason
from app.data.repository import FamilyRepository, JoinedFamily, JoinedStudent
from app.observability.log_store import DismissRecord, ObservabilityLog

router = APIRouter(tags=["families"])

# The store seam, injected via Annotated so the call sits in the type, not a
# default-arg call (avoids ruff B008; the idiomatic FastAPI dependency style).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
# The typed §8 params seam, injected the same way (INV-11 — every tunable here).
ParamsDep = Annotated[Params, Depends(get_params)]
# The NFR-6 audit spine — the recency source (A-14): last_contact_at is derived
# from the logged approve decisions, never a stored column.
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]


def _work_queue_family(joined: JoinedFamily, params: Params) -> WorkQueueFamily:
    """Project a joined family down to the scorer's pure input (FR-2.5; §5.1).

    Responsiveness is derived from the aggregate ``community_profile``
    engagement signals (A-5) — the spine carries no normalized responsiveness —
    via the pure :func:`responsiveness_from_engagement` helper. Everything else
    reads straight off the spine row.
    """
    signals = joined.community_profile.engagement_signals if joined.community_profile else {}
    # Child count drives the value term (A-23); it lives on the lead (the Interest
    # form's "How many children?"). A lead-less marketing row defaults to 1.
    num_children = joined.lead.num_children if joined.lead else 1
    return WorkQueueFamily(
        family_id=joined.family.family_id,
        current_stage=joined.family.current_stage,
        stalled_since=joined.family.stalled_since,
        created_at=joined.family.created_at,
        responsiveness=responsiveness_from_engagement(signals, params),
        num_children=num_children,
        funding_type=joined.family.funding_type,
    )


def _work_queue_student(joined: JoinedStudent, params: Params) -> WorkQueueStudent:
    """Project a joined student down to the per-child scorer's pure input (A-24).

    Responsiveness is the household's aggregate engagement (A-5) — shared across a
    family's children — exactly as for :func:`_work_queue_family`. Everything else
    reads off the child's OWN funnel (its ``current_stage``/``stalled_since``).
    """
    signals = joined.community_profile.engagement_signals if joined.community_profile else {}
    student = joined.student
    return WorkQueueStudent(
        student_id=student.student_id,
        family_id=student.family_id,
        current_stage=student.current_stage,
        stalled_since=student.stalled_since,
        created_at=student.created_at,
        responsiveness=responsiveness_from_engagement(signals, params),
        funding_type=student.funding_type,
    )


def _student_stall_stage(joined: JoinedStudent) -> Stage:
    """The funnel stage a CHILD was stuck at — its recovery "advanced past" baseline.

    Maps the student's own ``stall_reason`` through the §5.1 table (the same one
    families use); a child with no ``stall_reason`` falls back to its current stage
    (so "advanced" reads False).
    """
    student = joined.student
    return (
        _STALL_REASON_STAGE[student.stall_reason]
        if student.stall_reason is not None
        else student.current_stage
    )


def _recency_for(
    joined: JoinedFamily,
    *,
    log: ObservabilityLog,
    now: datetime,
    params: Params,
) -> tuple[ContactStatus, datetime | None]:
    """Compose a family's (contact_status, last_contact_at) at the api layer (A-14).

    The single recency composer shared by the calendar route and the work-queue
    route (DRY): the deriver needs ``now`` + the audit log, neither of which the
    pure core may touch (CLAUDE §3, INV-2), so this lives HERE — the composition
    root — NOT in ``core/``. Reads ``last_contact_at`` (the latest approved
    outbound) from the audit log, then derives the recency color against the
    api-layer ``now``. ``funded`` short-circuits to CLOSED. Callers pass ONE
    ``now`` read once per request so a whole response is internally consistent.

    Args:
        joined: The spine row joined to its source rows (for created_at/funded).
        log: The NFR-6 audit spine — the ``last_contact_at`` source (A-14).
        now: The request's reference time, read once at the api layer.
        params: Loaded params — supplies the ``enrollment.contact`` day windows.

    Returns:
        ``(contact_status, last_contact_at)`` for the family.
    """
    family = joined.family
    contacted_at = last_contact_at(log, family.family_id)
    status = derive_contact_status(
        created_at=family.created_at or now,
        last_contact_at=contacted_at,
        now=now,
        funded=family.funding_state is FundingState.FUNDED,
        params=params,
    )
    return status, contacted_at


def _apply_date(joined: JoinedFamily) -> datetime | None:
    """The family's application instant — ``app_form.submitted_at`` else spine ``created_at``.

    Mirrors the FR-2.2 deal-view rule (``family_record.assemble_deal_view``) so
    the calendar and the deal view never drift on what "apply_date" means.
    """
    apply_date = joined.app_form.submitted_at if joined.app_form is not None else None
    if apply_date is None:
        apply_date = joined.family.created_at
    return apply_date


def _stall_date(
    joined: JoinedFamily,
    *,
    log: ObservabilityLog,
    now: datetime,
    params: Params,
) -> datetime:
    """The family's stall-anchor instant — the calendar grouping key (A-16; S11 W1).

    Re-anchors the enrollment calendar on *when a family went quiet* rather than
    when it applied (``_apply_date`` clusters in late 2025, opening the surface
    empty on the current month). Resolves the first available of this precedence
    chain (document order is the contract):

    1. ``family.stalled_since`` — the spine's explicit stall instant.
    2. ``last_contact_at(log, family_id)`` — the latest approved-outbound (A-14),
       i.e. the last time we actually touched the family.
    3. ``created_at + enrollment.contact.overdue_days`` (params — INV-11) — the
       day an uncontacted family crossed into overdue.
    4. ``created_at`` — the brand-new fallback.

    Lives at the API layer (NOT ``core/``) because tiers 2–4 read ``now`` and the
    audit ``log``, neither of which the pure core may touch (CLAUDE §3, INV-2) —
    the same composition-root rationale as :func:`_recency_for`. Mirrors that
    helper's style: ``now``/``log`` are passed in, read once per request.

    Args:
        joined: The spine row joined to its source rows.
        log: The NFR-6 audit spine — the tier-2 ``last_contact_at`` source.
        now: The request's reference time, read once at the api layer (the
            ``created_at`` fallback when the spine carries no ``created_at``).
        params: Loaded params (§8) — supplies ``enrollment.contact.overdue_days``.

    Returns:
        The resolved stall-anchor instant (never None — tier 4 always resolves).
    """
    family = joined.family
    if family.stalled_since is not None:
        return family.stalled_since
    contacted_at = last_contact_at(log, family.family_id)
    if contacted_at is not None:
        return contacted_at
    created_at = family.created_at or now
    return created_at + timedelta(days=params.enrollment.contact.overdue_days)


# §5.1 stall_reason → the funnel stage the family was stuck at, the baseline for
# the recovery "advanced past the stall stage" check (A-19). A stall reason maps
# to exactly one stage (the deterministic §5.1 rule table); a family with no
# stall_reason falls back to its current stage (so "advanced" reads False — it has
# not moved off where it is). Kept here (the composition root), not in pure core.
_STALL_REASON_STAGE: dict[StallReason, Stage] = {
    StallReason.INFO_SESSION_NO_SHOW: Stage.INTEREST,
    StallReason.NO_RESPONSE: Stage.INTEREST,
    StallReason.APP_INCOMPLETE: Stage.APPLY,
    StallReason.FORMS_PARTIAL: Stage.ENROLL,
    StallReason.FUNDING_PENDING: Stage.ENROLL,
}


def _stall_stage(joined: JoinedFamily) -> Stage:
    """The funnel stage a family was stuck at — the recovery "advanced past" baseline.

    Maps the spine's ``stall_reason`` through the §5.1 :data:`_STALL_REASON_STAGE`
    table; a family with no ``stall_reason`` falls back to its current stage (so
    "advanced" reads False — it has not moved off where it is). Pure projection off
    the spine; shared by :func:`_recovery_state_for` and the history-scope
    :func:`recovered_outcome` so the two never disagree on the baseline.
    """
    family = joined.family
    return (
        _STALL_REASON_STAGE[family.stall_reason]
        if family.stall_reason is not None
        else family.current_stage
    )


def _recovery_state_for(
    joined: JoinedFamily,
    *,
    log: ObservabilityLog,
    now: datetime,
    params: Params,
) -> RecoveryState:
    """Compose a family's derived :class:`RecoveryState` at the API layer (A-19).

    Resolves the log-derived facts the pure :func:`derive_recovery_state` must NOT
    read itself (CLAUDE §3, INV-2) — ``last_contact_at`` (A-14) and ``dismissed``
    (the S12 dismiss event, netted against a superseding re-stall via the family's
    derived ``stall_date``) — plus the ``stall_stage`` baseline mapped from the
    spine's ``stall_reason``, then delegates to the pure deriver. The same
    composition-root rationale as :func:`_recency_for` / :func:`_stall_date`.

    Args:
        joined: The spine row joined to its source rows.
        log: The NFR-6 audit spine — the dismiss + last-contact source.
        now: The request's reference time, read once at the api layer.
        params: Loaded params (§8).

    Returns:
        The family's :class:`RecoveryState`.
    """
    family = joined.family
    contacted_at = last_contact_at(log, family.family_id)
    stall_date = _stall_date(joined, log=log, now=now, params=params)
    dismissed = log.is_dismissed(family.family_id, restalled_after=stall_date)
    return derive_recovery_state(
        joined=joined,
        last_contact_at=contacted_at,
        dismissed=dismissed,
        stall_stage=_stall_stage(joined),
        params=params,
    )


def _holding_dismiss(
    joined: JoinedFamily,
    *,
    log: ObservabilityLog,
    now: datetime,
    params: Params,
) -> DismissRecord | None:
    """The :class:`DismissRecord` that currently HOLDS for a family, or None (A-19).

    The latest dismiss event for the family that is not superseded by a later
    re-stall — i.e. the one ``is_dismissed`` is reporting True for. Surfaces the
    logged record (reason / human / created_at) so the history surface can show
    *who set this family aside, why, and when*. Mirrors the netting in
    :meth:`ObservabilityLog.is_dismissed` against the family's derived
    ``stall_date``. Returns None when no dismiss holds.
    """
    family = joined.family
    stall_date = _stall_date(joined, log=log, now=now, params=params)
    if not log.is_dismissed(family.family_id, restalled_after=stall_date):
        return None
    latest: DismissRecord | None = None
    for record in log.list_dismissals():
        if record.family_id != family.family_id:
            continue
        if latest is None or record.created_at > latest.created_at:
            latest = record
    return latest


def _resolved_at(
    joined: JoinedFamily,
    *,
    log: ObservabilityLog,
    now: datetime,
    params: Params,
) -> datetime:
    """Approximate when a RECOVERED family left the active board (A-19; history scope).

    There is no stored "recovered at" instant, so this composes the most relevant
    available signal instant the same way :func:`_stall_date` is composed — by the
    recovery predicate that fired (``recovered_outcome``):

    - ``stage_advanced``  → ``app_form.submitted_at`` (the apply-step instant we have).
    - ``forms_cleared``   → ``enrollment_forms.created_at`` (the forms-row instant).
    - ``deposit_received``→ ``family.crm_synced_at`` (the funding-mirror instant).

    If that signal instant is not recoverable, fall back to ``last_contact_at``
    (A-14) else the spine's ``updated_at`` else ``created_at`` else ``now`` — so a
    row always carries an approximate instant (never None). Lives at the API layer
    (NOT pure core) because the fallbacks read the audit log and ``now`` (INV-2),
    the same composition-root rationale as :func:`_stall_date`.
    """
    family = joined.family
    outcome = recovered_outcome(joined, stall_stage=_stall_stage(joined))
    signal: datetime | None = None
    if outcome == "stage_advanced" and joined.app_form is not None:
        signal = joined.app_form.submitted_at
    elif outcome == "forms_cleared" and joined.enrollment_forms is not None:
        signal = joined.enrollment_forms.created_at
    elif outcome == "deposit_received":
        signal = family.crm_synced_at
    if signal is not None:
        return signal
    return last_contact_at(log, family.family_id) or family.updated_at or family.created_at or now


@router.get("/pipeline", response_model=PipelineResponse)
def get_pipeline(repository: RepositoryDep) -> PipelineResponse:
    """Per-stage pipeline counts + CRM-seam summary (FR-2.1, FR-2.6)."""
    counts = repository.pipeline_counts()
    # Seam summary derived through the same store seam (every status zero-filled).
    seam = {status: len(repository.list_families(seam_status=status)) for status in SeamStatus}
    return PipelineResponse(counts=counts, total=sum(counts.values()), seam=seam)


@router.get("/families", response_model=list[FamilyRecord])
def list_families(
    repository: RepositoryDep,
    params: ParamsDep,
    stage: Stage | None = None,
    funding_state: FundingState | None = None,
    seam_status: SeamStatus | None = None,
    min_score: float | None = None,
) -> list[FamilyRecord]:
    """List Family Records, filtered by stage / funding_state / seam_status / score (FR-2.1).

    ``min_score`` (§6) keeps only families whose deterministic work-queue score
    (FR-2.5) is ≥ the threshold — the queue's score gate surfaced as a list
    filter. The spine carries no responsiveness, so scoring needs the join; when
    ``min_score`` is set the cohort is read via ``list_joined`` and scored
    through the same pure scorer as ``/work-queue`` (one source of truth), then
    the column-level filters are re-applied.
    """
    if min_score is None:
        return repository.list_families(
            stage=stage,
            funding_state=funding_state,
            seam_status=seam_status,
        )
    scored_ids = {
        joined.family.family_id
        for joined in repository.list_joined()
        if score_family(_work_queue_family(joined, params), params) >= min_score
    }
    return [
        family
        for family in repository.list_families(
            stage=stage,
            funding_state=funding_state,
            seam_status=seam_status,
        )
        if family.family_id in scored_ids
    ]


@router.get("/families/{family_id}", response_model=FamilyDetailResponse)
def get_family(
    family_id: UUID,
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
) -> FamilyDetailResponse:
    """Full joined Family Record — spine + four source rows + FR-2.2 deal view (§6).

    Stays the "full joined Family Record" (§6): the spine and its four source
    rows are returned as before (the S0 contract), enriched with ``deal_view`` —
    the flat operator projection from :func:`assemble_deal_view` over the same
    joined rows. The drop-off fields are pure (sourced in the projection); the
    contact-recency fields are composed HERE, the composition root (CLAUDE §3,
    INV-2): the deriver needs ``now`` + the audit log, neither of which the pure
    core may touch, so this handler derives ``last_contact_at`` (A-14) and
    ``contact_status`` and stamps them onto the projection via ``model_copy``.
    No AI (INV-2).
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    # Recency composed at the api layer (NOT pure core): derive last_contact_at
    # from the audit log (A-14) and the color status against an api-layer `now`,
    # through the shared composer (also used by /enrollment/calendar + /work-queue).
    now = datetime.now(UTC)
    contact_status, contacted_at = _recency_for(joined, log=log, now=now, params=params)
    recovery_state = _recovery_state_for(joined, log=log, now=now, params=params)
    deal_view = assemble_deal_view(joined).model_copy(
        update={
            "contact_status": contact_status,
            "last_contact_at": contacted_at,
            "recovery_state": recovery_state,
        }
    )
    return FamilyDetailResponse(
        family=joined.family,
        lead=joined.lead,
        app_form=joined.app_form,
        enrollment_forms=joined.enrollment_forms,
        community_profile=joined.community_profile,
        deal_view=deal_view,
    )


# The work-queue scope (the Show-all surface's first axis). ``active`` is the
# DEFAULT — the live recovery queue (recovery_state ∈ {stalled, working}); the
# 5,006-strong recovered/dismissed cohort does NOT belong in it (FR-2.5). The
# other scopes are bounded by ``limit`` so the route never streams the long tail.
WorkQueueScope = Literal["active", "history", "all"]

# Bounds for the history/all ``limit`` cap — never stream the recovered long tail.
_DEFAULT_QUEUE_LIMIT = 200
_MAX_QUEUE_LIMIT = 500

# recovery_states that constitute the ACTIVE recovery queue and the HISTORY tail.
_ACTIVE_STATES = (RecoveryState.STALLED, RecoveryState.WORKING)
_HISTORY_STATES = (RecoveryState.RECOVERED, RecoveryState.DISMISSED)


@router.get("/work-queue", response_model=list[WorkQueueItem])
def get_work_queue(
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
    scope: Annotated[
        WorkQueueScope,
        Query(
            description=(
                "Which slice of the recovery queue to return. **active** (default) — "
                "the live recovery queue, recovery_state ∈ {stalled, working}; "
                "pre-filtered to the cheap `stalled_since is not None` candidates "
                "BEFORE the per-family derive so the default response stays small/fast. "
                "**history** — only {recovered, dismissed}, `limit`-capped (never the "
                "5,006-strong tail). **all** — the back-compat full cohort, `limit`-capped."
            ),
        ),
    ] = "active",
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=_MAX_QUEUE_LIMIT,
            description=(
                "Row cap for the history/all scopes (default 200, max 500). Ignored "
                "for the active scope (already small after the candidate pre-filter)."
            ),
        ),
    ] = _DEFAULT_QUEUE_LIMIT,
) -> list[WorkQueueItem]:
    """Ranked work queue, highest recoverable_now first, scoped (FR-2.5; S12 W1; §6).

    Three scopes off the derived ``recovery_state`` (A-19):

    - **active** (default): the live recovery queue — ``{stalled, working}``. To
      keep this fast on a recovered-heavy cohort (5,146 families, ~5,006 of them
      recovered) it PRE-FILTERS to the cheap ``family.stalled_since is not None``
      candidates BEFORE the expensive per-family derive/score: a family that was
      never stalled cannot be active recovery work. Only the ~140 candidates are
      derived, then kept iff ``{stalled, working}``.
    - **history**: ``{recovered, dismissed}`` only, ordered by ``recoverable_now``
      desc and capped to ``limit`` — never streams the long tail.
    - **all**: the back-compat full cohort, also ``limit``-capped.

    Within every scope rows order by the S12 ``recoverable_now`` key descending,
    ties broken by ascending ``family_id`` (total, stable). Each row carries that
    key plus ``freshness`` / ``score`` / ``recoverability`` / ``value``, the
    derived ``stall_date`` (the calendar's grouping key — :func:`_stall_date`),
    the api-composed recency pair (``contact_status`` + ``last_contact_at``,
    S9 W3), and the derived ``recovery_state`` (A-19). Recency, stall_date, and
    recovery facts are composed HERE against a single ``now`` read once per
    request — never in the pure scorer (INV-2). No AI (INV-2).
    """
    now = datetime.now(UTC)

    # The candidate cohort. For the ACTIVE default, pre-filter to families that
    # were ever stalled (the only ones that can be {stalled, working}) BEFORE the
    # per-family derive — so the default response is computed over ~140 rows, not
    # 5,146. history/all keep the full cohort (then cap by `limit` after ranking).
    candidates = repository.list_joined()
    if scope == "active":
        candidates = [j for j in candidates if j.family.stalled_since is not None]

    joined_by_id: dict[UUID, JoinedFamily] = {
        joined.family.family_id: joined for joined in candidates
    }
    queue_families = [_work_queue_family(joined, params) for joined in joined_by_id.values()]
    # Order by recoverable_now desc; ascending family_id breaks ties stably (S12).
    ranked = sorted(
        queue_families,
        key=lambda f: (-recoverable_now(f, params, now=now), f.family_id),
    )

    rows: list[WorkQueueItem] = []
    for family in ranked:
        joined = joined_by_id[family.family_id]
        state = _recovery_state_for(joined, log=log, now=now, params=params)
        # Scope gate: drop rows whose derived state is outside the requested slice.
        if scope == "active" and state not in _ACTIVE_STATES:
            continue
        if scope == "history" and state not in _HISTORY_STATES:
            continue
        contact_status, contacted_at = _recency_for(joined, log=log, now=now, params=params)
        # History-scope OUTCOME story (A-19) — computed ONLY here, so the active
        # path stays byte-identical and adds no cost (all five fields stay null on
        # active/all rows). For a recovered row: which predicate fired + when it
        # left the board; for a dismissed row: the holding DismissRecord's fields.
        recovered_label: RecoveredOutcome | None = None
        resolved_at: datetime | None = None
        dismiss_reason: str | None = None
        dismissed_by: str | None = None
        dismissed_at: datetime | None = None
        if scope == "history":
            if state is RecoveryState.RECOVERED:
                recovered_label = recovered_outcome(joined, stall_stage=_stall_stage(joined))
                resolved_at = _resolved_at(joined, log=log, now=now, params=params)
            elif state is RecoveryState.DISMISSED:
                dismiss = _holding_dismiss(joined, log=log, now=now, params=params)
                if dismiss is not None:
                    dismiss_reason = dismiss.reason
                    dismissed_by = dismiss.human
                    dismissed_at = dismiss.created_at
        rows.append(
            WorkQueueItem(
                family_id=family.family_id,
                display_name=joined.family.display_name,
                current_stage=family.current_stage,
                score=score_family(family, params, now=now),
                recoverability=recoverability(family, params, now=now),
                value=value(family, params),
                num_children=family.num_children,
                funding_type=family.funding_type,
                stall_date=_stall_date(joined, log=log, now=now, params=params),
                recoverable_now=recoverable_now(family, params, now=now),
                freshness=freshness(family, params, now=now),
                contact_status=contact_status,
                last_contact_at=contacted_at,
                recovery_state=state,
                recovered_outcome=recovered_label,
                resolved_at=resolved_at,
                dismiss_reason=dismiss_reason,
                dismissed_by=dismissed_by,
                dismissed_at=dismissed_at,
            )
        )
        # history/all are bounded — never stream the recovered/dismissed tail.
        if scope != "active" and len(rows) >= limit:
            break
    return rows


@router.get("/students", response_model=StudentBoardResponse)
def get_students(
    repository: RepositoryDep,
    params: ParamsDep,
) -> StudentBoardResponse:
    """The per-child board (A-24; `GET /students`) — students grouped by household.

    Each child runs its own funnel (one application per child), so the board ranks
    STUDENTS by ``recoverable_now_student`` (recoverability-driven; every child is
    one tuition of value). Per-child recovery state is derived from the student's
    OWN funnel signals (:func:`derive_student_recovery_state`). Rows are grouped
    under their household; a household's ``value_at_risk`` sums one per-child
    tuition over its students that are still active ({stalled, working}) — the
    per-child replacement for the old all-or-nothing family value. Households are
    ordered by their most-recoverable child, students within a household likewise.
    All numbers come from the pure scorer (INV-2); ``now`` is read once.
    """
    now = datetime.now(UTC)
    joined_students = repository.list_students()
    joined_by_id = {js.student.student_id: js for js in joined_students}

    units = [_work_queue_student(js, params) for js in joined_students]
    ranked = sorted(
        units,
        key=lambda s: (-recoverable_now_student(s, params, now=now), s.student_id),
    )

    # Build ranked rows, grouped by household in first-seen (rank) order so the
    # most-recoverable child surfaces its household first.
    groups: dict[UUID, HouseholdGroup] = {}
    for unit in ranked:
        js = joined_by_id[unit.student_id]
        student = js.student
        state = derive_student_recovery_state(
            current_stage=student.current_stage,
            funding_state=student.funding_state,
            enrollment_forms=js.enrollment_forms,
            stall_stage=_student_stall_stage(js),
        )
        row = StudentRow(
            student_id=student.student_id,
            family_id=student.family_id,
            household_name=js.family.display_name,
            display_label=student.display_label,
            synthetic_first_name=student.synthetic_first_name,
            grade=student.grade,
            current_stage=student.current_stage,
            funding_type=student.funding_type,
            funding_state=student.funding_state,
            stall_reason=student.stall_reason,
            score=score_student(unit, params, now=now),
            recoverability=recoverability(unit, params, now=now),
            value=student_value(params),
            recoverable_now=recoverable_now_student(unit, params, now=now),
            freshness=freshness(unit, params, now=now),
            recovery_state=state,
        )
        group = groups.get(student.family_id)
        if group is None:
            group = HouseholdGroup(
                family_id=student.family_id,
                household_name=js.family.display_name,
                value_at_risk=0.0,
                students=[],
            )
            groups[student.family_id] = group
        group.students.append(row)
        # Household $-at-risk: sum one per-child tuition over STILL-ACTIVE students
        # (a recovered/dismissed child is no longer "at risk" — A-24 fixes the old
        # all-or-nothing family value).
        if is_active(state):
            group.value_at_risk += row.value

    households = list(groups.values())
    return StudentBoardResponse(
        households=households,
        total_students=len(ranked),
        total_value_at_risk=sum(g.value_at_risk for g in households),
    )


# YYYY-MM with month 01..12 — anchors the calendar query param's 422 validation.
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get("/enrollment/calendar", response_model=CalendarResponse)
def get_enrollment_calendar(
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
    month: Annotated[
        str | None,
        Query(
            pattern=_MONTH_PATTERN,
            description=(
                "Target month in YYYY-MM form (01-12); 422 on a bad format. Optional — "
                "when omitted, resolves to the month of the most-recent stall_date so the "
                "surface opens non-empty."
            ),
        ),
    ] = None,
    day: Annotated[
        int | None,
        Query(
            ge=1,
            le=31,
            description=(
                "Optional day-of-month (1-31) drill (S12 W1): when set, returns only "
                "the resolved month's entries whose stall_date falls on that day — the "
                "heat-calendar drill list. Omitted ⇒ the whole month (unchanged)."
            ),
        ),
    ] = None,
) -> CalendarResponse:
    """Families whose ``stall_date`` falls in ``month``, for the Wave 4 month view (§6).

    Re-anchored on the derived ``stall_date`` (S11 W1; ASSUMPTIONS A-16) — the
    first available of ``family.stalled_since`` → ``last_contact_at`` →
    ``created_at + overdue_days`` → ``created_at`` (via :func:`_stall_date`) — so
    the board clusters on when a family went quiet, not when it applied. The
    ``month`` query param is **optional**: when omitted it resolves to the YYYY-MM
    of the most-recent ``stall_date`` across all families (so the surface opens
    non-empty), falling back to the month of ``now`` if there are zero families.
    Only the in-month families are returned, sorted ascending by ``stall_date``; a
    month with no stalls yields ``entries: []`` (never an error). The optional
    ``day`` param (S12 W1) narrows to a single day-of-month — the heat-calendar
    drill list — within the resolved month (omitted ⇒ the whole month). Each entry
    keeps ``apply_date`` for reference and carries the api-composed
    ``contact_status`` (now + audit log + params — the same recency composition as
    the deal view and the work queue, INV-2 core purity), the derived
    ``recovery_state`` (A-19), and ``value``/``score``/``recoverable_now``/
    ``freshness`` from the pure work-queue scorer. ``CalendarResponse.month``
    echoes the **resolved** month. No AI (INV-2).
    """
    now = datetime.now(UTC)

    # Compute every family's stall_date once (the grouping/anchor key) so the
    # month resolution and the in-month filter read one consistent derivation.
    stalled: list[tuple[datetime, JoinedFamily]] = [
        (_stall_date(joined, log=log, now=now, params=params), joined)
        for joined in repository.list_joined()
    ]

    if month is None:
        # Open on the most-recent stall_date's month so the surface is non-empty;
        # fall back to the month of `now` when there are zero families.
        anchor = max((sd for sd, _ in stalled), default=now)
        resolved_month = f"{anchor.year:04d}-{anchor.month:02d}"
    else:
        resolved_month = month
    year_s, month_s = resolved_month.split("-")
    year, mon = int(year_s), int(month_s)

    in_month = [
        (stall_date, joined)
        for stall_date, joined in stalled
        if stall_date.year == year
        and stall_date.month == mon
        # S12 W1 drill: when `day` is set, keep only that day-of-month.
        and (day is None or stall_date.day == day)
    ]
    in_month.sort(key=lambda pair: pair[0])

    entries = [
        CalendarEntry(
            family_id=joined.family.family_id,
            display_name=joined.family.display_name,
            stall_date=stall_date,
            apply_date=_apply_date(joined) or stall_date,
            current_stage=joined.family.current_stage,
            contact_status=_recency_for(joined, log=log, now=now, params=params)[0],
            value=value(wqf, params),
            num_children=wqf.num_children,
            funding_type=wqf.funding_type,
            score=score_family(wqf, params, now=now),
            recoverable_now=recoverable_now(wqf, params, now=now),
            freshness=freshness(wqf, params, now=now),
            recovery_state=_recovery_state_for(joined, log=log, now=now, params=params),
        )
        for stall_date, joined in in_month
        for wqf in (_work_queue_family(joined, params),)
    ]
    return CalendarResponse(month=resolved_month, entries=entries)

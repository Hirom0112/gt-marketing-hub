"""Read-only Family / pipeline endpoints (ARCHITECTURE.md §6; FR-2.1/2.2).

All GET, all deterministic, no AI (INV-2 for the S0 landing slice). Every route
reads through the :class:`FamilyRepository` seam (`deps.get_repository`), so the
store is swappable to Supabase with zero changes here (NFR-8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_observability_log, get_params, get_repository
from app.api.schemas import (
    CalendarEntry,
    CalendarResponse,
    FamilyDetailResponse,
    PipelineResponse,
    WorkQueueItem,
)
from app.core.contact_log import last_contact_at
from app.core.contact_status import ContactStatus, derive_contact_status
from app.core.family_record import assemble_deal_view
from app.core.params import Params
from app.core.work_queue import (
    WorkQueueFamily,
    rank_families,
    recoverability,
    responsiveness_from_engagement,
    score_family,
    value,
)
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import FamilyRepository, JoinedFamily
from app.observability.log_store import ObservabilityLog

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
    return WorkQueueFamily(
        family_id=joined.family.family_id,
        current_stage=joined.family.current_stage,
        stalled_since=joined.family.stalled_since,
        responsiveness=responsiveness_from_engagement(signals, params),
        funding_type=joined.family.funding_type,
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
    contact_status, contacted_at = _recency_for(
        joined, log=log, now=datetime.now(UTC), params=params
    )
    deal_view = assemble_deal_view(joined).model_copy(
        update={"contact_status": contact_status, "last_contact_at": contacted_at}
    )
    return FamilyDetailResponse(
        family=joined.family,
        lead=joined.lead,
        app_form=joined.app_form,
        enrollment_forms=joined.enrollment_forms,
        community_profile=joined.community_profile,
        deal_view=deal_view,
    )


@router.get("/work-queue", response_model=list[WorkQueueItem])
def get_work_queue(
    repository: RepositoryDep, params: ParamsDep, log: LogDep
) -> list[WorkQueueItem]:
    """Ranked work queue, highest deterministic score first (FR-2.5; §6).

    Reads the cohort joined (for the aggregate responsiveness signal — A-5),
    projects each family to the scorer's pure input, and delegates ordering to
    :func:`app.core.work_queue.rank_families` — the router never re-implements
    ranking. Each row carries the score plus its ``recoverability`` / ``value``
    components so the UI can show why a family ranks where it does, plus the
    api-composed recency pair (``contact_status`` + ``last_contact_at``) so the
    board can color a family without N extra calls (S9 W3). The recency is
    composed HERE via :func:`_recency_for` against a single ``now`` read once
    per request — never in the pure scorer (INV-2). No AI (INV-2).
    """
    now = datetime.now(UTC)
    joined_by_id: dict[UUID, JoinedFamily] = {
        joined.family.family_id: joined for joined in repository.list_joined()
    }
    queue_families = [_work_queue_family(joined, params) for joined in joined_by_id.values()]
    ranked = rank_families(queue_families, params)
    rows: list[WorkQueueItem] = []
    for family in ranked:
        joined = joined_by_id[family.family_id]
        contact_status, contacted_at = _recency_for(joined, log=log, now=now, params=params)
        rows.append(
            WorkQueueItem(
                family_id=family.family_id,
                display_name=joined.family.display_name,
                current_stage=family.current_stage,
                score=score_family(family, params),
                recoverability=recoverability(family, params),
                value=value(family, params),
                contact_status=contact_status,
                last_contact_at=contacted_at,
            )
        )
    return rows


# YYYY-MM with month 01..12 — anchors the calendar query param's 422 validation.
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get("/enrollment/calendar", response_model=CalendarResponse)
def get_enrollment_calendar(
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
    month: Annotated[
        str,
        Query(
            pattern=_MONTH_PATTERN,
            description="Target month in YYYY-MM form (01-12); 422 on a bad format.",
        ),
    ],
) -> CalendarResponse:
    """Families whose apply_date falls in ``month``, for the Wave 4 month view (§6).

    A family's ``apply_date`` is ``app_form.submitted_at`` else the spine
    ``created_at`` (the FR-2.2 deal-view rule, via :func:`_apply_date`). Only the
    in-month families are returned, sorted ascending by ``apply_date``; a month
    with no applications yields ``entries: []`` (never an error). Each entry
    carries the api-composed ``contact_status`` (now + audit log + params), the
    same recency composition as the deal view and the work queue (INV-2 core
    purity — the deriver's ``now``/log reads stay at the api layer). No AI (INV-2).
    """
    year_s, month_s = month.split("-")
    year, mon = int(year_s), int(month_s)
    now = datetime.now(UTC)

    dated: list[tuple[datetime, JoinedFamily]] = []
    for joined in repository.list_joined():
        apply_date = _apply_date(joined)
        if apply_date is None or apply_date.year != year or apply_date.month != mon:
            continue
        dated.append((apply_date, joined))
    dated.sort(key=lambda pair: pair[0])

    entries = [
        CalendarEntry(
            family_id=joined.family.family_id,
            display_name=joined.family.display_name,
            apply_date=apply_date,
            current_stage=joined.family.current_stage,
            contact_status=_recency_for(joined, log=log, now=now, params=params)[0],
        )
        for apply_date, joined in dated
    ]
    return CalendarResponse(month=month, entries=entries)

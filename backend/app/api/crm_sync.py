"""CRM-as-truth incremental-poll endpoints (A2; PLAN_v2 §A2; RESEARCH_v2 §II.1).

The composition root that wires the A2 incremental poll behind REST. It COMPOSES
three already-built, separately-tested pieces and owns none of their logic:

- the PURE planner (``app.core.crm_sync``) — the watermark advance + the time-window
  chunking past HubSpot's 10k-result cap (now-injected, no clock, no I/O);
- the CRM adapter pull (``CRMAdapter.search_modified_since`` with the A2 ``until_ms``
  upper bound) — simulated v1, live under ``CRM_MODE=live`` (INV-9);
- the §4.7 seam reconcile (``app.core.seam`` — ``propose_reconcile`` /
  ``apply_reconcile``), which is CRM-authoritative for stage/owner and keeps
  ``funding_state`` DB-authoritative (INV-10 — the A2 seam flip).

  ``POST /crm/sync/poll``
    Read the persisted per-program watermark; pull every ``deal`` modified strictly
    after it (window-chunked); reconcile each pulled record into the program store
    (CRM wins stage/owner, last-write-wins); advance the watermark to the max
    ``hs_lastmodifieddate`` seen; LOG each proposal + decision (NFR-6). A pulled
    record with no local family is counted ``unmatched`` (never fabricated). Returns
    a summary: pulled / applied / conflicts / unmatched / watermark.

  ``GET  /crm/sync/status``
    Read-only: the per-object watermark plus the configured ``search_qps`` /
    ``chunk_days`` tunables (INV-11).

This module is the composition root (CLAUDE.md §3): it MAY read the clock and make
UUIDs (the pure core may not), so ``now`` and ``proposal_id`` are computed HERE,
exactly as ``app.api.seam`` does. It may import ``app.core`` / ``app.observability``;
``app/core/`` stays pure. No live external send is made here (INV-9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import (
    get_active_program,
    get_observability_log,
    get_params,
    get_repository,
    get_seam_crm_adapter_dep,
    get_watermark_store,
)
from app.core.crm_sync import PulledRecord, advance_watermark, plan_sync_windows
from app.core.params import Params
from app.core.program import Program
from app.core.seam import (
    MirrorState,
    ReconcileDirection,
    apply_reconcile,
    propose_reconcile,
)
from app.data.models import FamilyRecord
from app.data.repository import FamilyRepository
from app.data.watermark_store import WatermarkStore
from app.observability.log_store import DecisionAction, ObservabilityLog

router = APIRouter(tags=["crm-sync"])

# Dependency aliases (Annotated keeps the Depends call in the type — ruff B008, the
# idiomatic FastAPI style matching app/api/seam.py).
ProgramDep = Annotated[Program, Depends(get_active_program)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
WatermarkStoreDep = Annotated[WatermarkStore, Depends(get_watermark_store)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_seam_crm_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
ParamsDep = Annotated[Params, Depends(get_params)]

# The HubSpot object type the v1 poll syncs (the migration's example object_type).
# A1 widens this to a multi-object loop; v1 keys CRM-as-truth on the deal.
_DEAL_OBJECT = "deal"

# The cold-backfill sentinel: when a (program, object_type) has never been synced
# (a NULL watermark), the FIRST poll pulls from the Unix epoch forward — a full
# backfill — and then advances the watermark to the max modified-at seen. A fixed,
# documented instant (not a magic tunable — it is THE epoch, the lower bound of any
# `hs_lastmodifieddate`), so the window planner has a concrete `start`.
_EPOCH_START = datetime(1970, 1, 1, tzinfo=UTC)

# The audited §10 flow tag + schema version for one CRM-sync reconcile (the audit
# head). Each pulled-record reconcile logs a proposal under this flow then a
# decision — the poll is automated, so the decision actor is the poller, not a
# human operator (the reconcile is CRM-authoritative, INV-2: the deterministic seam
# core owns the write, the LLM is nowhere near this path).
SYNC_FLOW = "crm_sync_reconcile"
SYNC_SCHEMA_VERSION = "1"
SYNC_ACTOR = "crm-poller"


def to_epoch_ms(value: datetime) -> int:
    """A datetime → the epoch-millisecond stamp HubSpot's ``hs_lastmodifieddate`` uses.

    The adapter's CRM-Search filters compare against epoch-ms (RESEARCH_v2 §II.1), so
    each window bound is converted here at the composition boundary.
    """
    return int(value.timestamp() * 1000)


class SyncPollSummary(BaseModel):
    """The outcome of one ``POST /crm/sync/poll`` (A2).

    Attributes:
        pulled: How many records the windowed CRM Search returned this poll.
        applied: How many reconciles were APPLIED (an ACCEPT_MIRROR / PUSH_LOCAL
            that wrote through the store seam).
        conflicts: How many reconciles flagged a CONFLICT (fail-closed — not
            silently resolved; INV-4).
        unmatched: How many pulled records had no local family (logged/counted,
            never fabricated).
        watermark: The new watermark (ISO-8601), or ``None`` if nothing was ever
            synced and nothing was pulled.
        search_qps: The configured CRM-Search request-rate budget (queries/sec) the
            poller would throttle to — surfaced for observability (INV-11).
    """

    pulled: int
    applied: int
    conflicts: int
    unmatched: int
    watermark: str | None
    search_qps: int


class SyncStatusObject(BaseModel):
    """One object type's watermark in the read-only status view (A2)."""

    object_type: str
    watermark: str | None


class SyncStatus(BaseModel):
    """The read-only ``GET /crm/sync/status`` view (A2).

    The per-object watermark plus the configured poll tunables (``search_qps`` /
    ``chunk_days``, INV-11). Read-only — nothing is logged or written.
    """

    objects: list[SyncStatusObject]
    search_qps: int
    chunk_days: int


@router.post("/crm/sync/poll", response_model=SyncPollSummary)
def poll_crm_sync(
    program: ProgramDep,
    repository: RepositoryDep,
    store: WatermarkStoreDep,
    crm_adapter: CRMAdapterDep,
    log: LogDep,
    params: ParamsDep,
) -> SyncPollSummary:
    """Pull-since-watermark, reconcile CRM-as-truth, advance, LOG (A2; NFR-6).

    The composition reads the clock HERE (``now``) — the pure planner may not. For
    the ``deal`` object: read the per-program watermark (``None`` ⇒ cold full
    backfill from the epoch sentinel), chunk ``[start, now]`` into ``chunk_days``
    sub-windows so each CRM Search stays under the 10k cap, and pull each window with
    its ``[start, end]`` bound (the chunking is load-bearing). Each pulled record is
    reconciled through the §4.7 seam: a matched family runs propose→apply→persist→log
    (CRM wins stage/owner by last-write-wins; ``funding_state`` stays DB-authoritative,
    INV-10); an unmatched pull is counted, never fabricated. The watermark advances to
    the max ``hs_lastmodifieddate`` seen and is persisted only when it moved forward.
    """
    now = datetime.now(UTC)
    object_type = _DEAL_OBJECT

    watermark = store.get_watermark(program, object_type)
    start = watermark or _EPOCH_START

    # Window-chunk [start, now] under the 10k cap (the chunking is what makes each
    # bounded query safe). Each window is pulled with BOTH bounds so no single CRM
    # Search query risks the cap (A2; RESEARCH_v2 §II.1).
    windows = plan_sync_windows(start, now, params.crm_sync.chunk_days)
    pulled_pairs: list[tuple[UUID, MirrorState]] = []
    for window in windows:
        pulled_pairs.extend(
            crm_adapter.search_modified_since(
                object_type,
                to_epoch_ms(window.start),
                until_ms=to_epoch_ms(window.end),
            )
        )

    applied = 0
    conflicts = 0
    unmatched = 0
    for family_id, mirror in pulled_pairs:
        joined = repository.get_family(family_id)
        if joined is None:
            # A pulled record with no local family — count it, do NOT fabricate one.
            unmatched += 1
            continue
        outcome = _reconcile_pulled(repository, crm_adapter, log, joined.family, mirror)
        if outcome is _Outcome.APPLIED:
            applied += 1
        elif outcome is _Outcome.CONFLICT:
            conflicts += 1

    # Advance the watermark to the max hs_lastmodifieddate pulled (the pure planner;
    # it never moves backward and an empty batch is a no-op). Persist only when it
    # strictly advanced (the store does not guard direction — that is the caller's
    # contract).
    new_watermark = advance_watermark(
        watermark,
        [
            PulledRecord(hs_lastmodifieddate=mirror.mirror_updated_at)
            for _, mirror in pulled_pairs
            if mirror.mirror_updated_at is not None
        ],
    )
    if new_watermark is not None and new_watermark != watermark:
        store.set_watermark(program, object_type, new_watermark)

    return SyncPollSummary(
        pulled=len(pulled_pairs),
        applied=applied,
        conflicts=conflicts,
        unmatched=unmatched,
        watermark=new_watermark.isoformat() if new_watermark is not None else None,
        # search_qps is surfaced as the CONFIGURED rate only; sleep-based pacing
        # enforcement is A5 (not implemented here) — this just reports the budget.
        search_qps=params.crm_sync.search_qps,
    )


@router.get("/crm/sync/status", response_model=SyncStatus)
def crm_sync_status(
    program: ProgramDep,
    store: WatermarkStoreDep,
    params: ParamsDep,
) -> SyncStatus:
    """The read-only per-object watermark + configured poll tunables (A2; INV-11)."""
    objects = [
        SyncStatusObject(
            object_type=object_type,
            watermark=_iso_or_none(store.get_watermark(program, object_type)),
        )
        for object_type in (_DEAL_OBJECT,)
    ]
    return SyncStatus(
        objects=objects,
        search_qps=params.crm_sync.search_qps,
        chunk_days=params.crm_sync.chunk_days,
    )


# ---------------------------------------------------------------------------
# Reconcile helper — the seam.py reconcile→persist→log dance, one pulled record.
# ---------------------------------------------------------------------------


class _Outcome(Enum):
    """The result of reconciling one pulled record (for the poll's tallies)."""

    APPLIED = "applied"
    CONFLICT = "conflict"
    NOOP = "noop"


def _reconcile_pulled(
    repository: FamilyRepository,
    crm_adapter: CRMAdapter,
    log: ObservabilityLog,
    record: FamilyRecord,
    mirror: MirrorState,
) -> _Outcome:
    """Reconcile ONE pulled record into the store — the seam.py dance (A2; NFR-6).

    Mirrors ``app.api.seam.reconcile_seam``: ``propose_reconcile`` → ``apply_reconcile``
    → persist the adopted field(s) + advance ``crm_synced_at`` through the store seam
    → re-push through the CRM adapter (simulated v1, INV-9) → LOG the proposal + the
    decision (NFR-6). A ``None`` proposal (already synced) is a clean no-op. A flagged
    CONFLICT fails closed (``applied=False``) and persists nothing (INV-4); it is still
    logged so the audit records the flag.

    The A2 difference from the human-clicked seam: the poll ADOPTS a CRM-newer change
    even onto an otherwise-synced local record (that is the whole point of the
    CRM-as-truth pull), so persistence is gated on ``result.applied`` alone — not on
    the seam's local-advanced push-idempotency fence. INV-10 holds automatically:
    ``apply_reconcile`` keeps ``funding_state`` the unchanged DB value (never the
    mirror's), so writing ``result.record.funding_state`` writes the DB value, not the
    CRM's.
    """
    proposal = propose_reconcile(record, mirror)
    if proposal is None:
        return _Outcome.NOOP  # already synced — nothing to reconcile.

    result = apply_reconcile(record, proposal)

    if result.applied:
        if proposal.direction is ReconcileDirection.ACCEPT_MIRROR:
            # Adopt the CRM-authoritative tracked fields onto the stored record.
            # `result.record.funding_state` is the UNCHANGED DB value (INV-10 — the
            # mirror's funding_state is never accepted), so this is the DB value.
            repository.apply_field(record.family_id, "current_stage", result.record.current_stage)
            repository.apply_field(record.family_id, "funding_state", result.record.funding_state)
        synced_at = result.record.crm_synced_at
        if synced_at is not None:
            repository.mark_synced(record.family_id, synced_at)
        # Re-push the reconciled record through the CRM adapter (simulated v1 — INV-9).
        crm_adapter.push_family(result.record)

    # LOG the reconcile (NFR-6): the proposal, then the decision. A flagged conflict
    # is logged too — the audit records the flag even though apply_reconcile failed
    # closed (applied=False); the deterministic core declined to silently resolve it.
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=SYNC_FLOW,
        schema_version=SYNC_SCHEMA_VERSION,
        payload=proposal.model_dump(mode="json"),
        family_id=record.family_id,
    )
    log.log_decision(
        proposal_id=proposal_id,
        human=SYNC_ACTOR,
        action=DecisionAction.APPROVE,
    )

    return _Outcome.APPLIED if result.applied else _Outcome.CONFLICT


def _iso_or_none(value: datetime | None) -> str | None:
    """ISO-8601 the watermark, or ``None`` when never synced."""
    return value.isoformat() if value is not None else None

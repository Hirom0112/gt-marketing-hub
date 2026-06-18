"""Deterministic enrollment write-action endpoints (S10 W3; ARCH §7.1; INV-2/9).

The "Seed to HubSpot" action — the one place a synthetic family is PUSHED across
the ``CRMAdapter`` seam from a deterministic, human-triggered route (a button
click), never from ``app/ai``. It is mode-agnostic: under ``CRM_MODE=simulate``
the recorder records the push (INV-9); under ``CRM_MODE=live`` the production
adapter writes a Contact + Deal into the real HubSpot portal behind the four
guards (ANALYSIS/hubspot-complement-plan.md §3). INV-2 holds: this route is the
deterministic core's composition layer — it imports the adapter seam, the AI
edge never does (the §8.4 import-walk test guards that for the live adapter).

  ``POST /enrollment/families/{family_id}/seed``
    1. load the family (404 if unknown);
    2. ``adapter.push_family(record)`` — the write-shaped seam op (§7.1);
    3. advance ``crm_synced_at`` and re-derive the §4.7 seam so it flips
       ``unsynced → synced`` (derive-and-return per A-7 — the read-only A-3 store
       is not mutated);
    4. return ``{family_id, simulated, deal_id, stage, seam_status}``; ``deal_id``
       is the adapter's ``recorded_id`` — under ``CRM_MODE=live`` the live HubSpot
       deal id, the cockpit's proof-of-capture.

This module is the composition layer (it imports ``app.adapters``); ``app/core/``
stays pure. No LLM call is ever made here — seeding is fully deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import (
    get_crm_adapter_dep,
    get_observability_log,
    get_params,
    get_repository,
)
from app.api.schemas import (
    AutoAssignCounts,
    AutoAssignRequest,
    AutoAssignResponse,
    AutoAssignResult,
    BulkAssignCounts,
    BulkAssignRequest,
    BulkAssignResponse,
    BulkDismissCounts,
    BulkDismissRequest,
    BulkDismissResponse,
    BulkSeedCaptured,
    BulkSeedCounts,
    BulkSeedRequest,
    BulkSeedResponse,
    SeedResponse,
    SlaSweepCounts,
    SlaSweepRequest,
    SlaSweepResponse,
    SlaSweepResult,
)
from app.core import sales_agents
from app.core.contact_log import last_contact_at
from app.core.lead_routing import LeadSignals, is_sla_breached, route_lead
from app.core.params import Params
from app.core.seam import MirrorState, derive_seam_status
from app.data.repository import UNASSIGNED, FamilyRepository, JoinedFamily
from app.observability.log_store import DecisionAction, ObservabilityLog

# The deterministic-assignment audit flow tag (NFR-6) — distinguishes these
# operator decisions from AI proposal flows on the shared spine. A constant, not
# a magic string (INV-11 spirit).
_ASSIGN_FLOW = "assignment"
_ASSIGN_SCHEMA_VERSION = "1"

router = APIRouter(tags=["enrollment"])

# Dependency aliases (Annotated keeps the call in the type, not a default arg —
# ruff B008; the idiomatic FastAPI style matching the other routers).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
ParamsDep = Annotated[Params, Depends(get_params)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
# v1 has no auth; the operator is a fixed audit seam (A-3), mirroring ai_actions.
DEFAULT_HUMAN = "operator"


def _batch_id(prefix: str, family_ids: list[UUID]) -> str:
    """A deterministic ``batch_id`` tagging one bulk audit group (NFR-6; A-20).

    Derived (uuid5) from the prefix + the SORTED family ids so the same selection
    yields the same id — a stable correlation handle, not a second write path.
    """
    key = f"{prefix}:" + ",".join(sorted(str(fid) for fid in family_ids))
    return f"{prefix}-{uuid5(NAMESPACE_URL, key).hex}"


@router.post("/enrollment/families/{family_id}/seed", response_model=SeedResponse)
def seed_family_to_crm(
    family_id: UUID,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
) -> SeedResponse:
    """Push a synthetic family across the CRM seam and re-derive the seam (S10 W3).

    404 if the family is unknown. Otherwise ``push_family`` writes (live) or
    records (simulated) the Contact + Deal; the §4.7 seam is then recomputed
    against the post-push state — ``crm_synced_at`` advanced to ``updated_at`` and
    the mirror reflecting the pushed stage — so it derives ``synced``
    (derive-and-return per A-7; the read-only store is not mutated). The returned
    ``deal_id`` is the adapter's ``recorded_id`` (the live HubSpot deal id under
    ``CRM_MODE=live``) — the cockpit's proof the family was captured.
    """
    joined = repository.get_family(family_id)
    if joined is None:
        raise HTTPException(status_code=404, detail="family not found")

    record = joined.family

    # The write-shaped seam op (§7.1): live pushes a Contact+Deal, simulate
    # records the push. The SOLE caller on this deterministic route (INV-2).
    sync = crm_adapter.push_family(record)

    # Derive-and-return the post-push §4.7 seam (A-7): the push synced local state
    # into the CRM, so crm_synced_at advances to updated_at and the mirror now
    # holds the pushed stage. derive_seam_status then yields `synced`. The
    # read-only A-3 store is not mutated — the seam is derived for the response.
    synced_record = record.model_copy(update={"crm_synced_at": record.updated_at})
    mirror = MirrorState(stage=sync.stage, mirror_updated_at=record.updated_at)
    seam_status = derive_seam_status(synced_record, mirror)

    return SeedResponse(
        family_id=family_id,
        simulated=sync.simulated,
        deal_id=sync.recorded_id,
        contact_id=sync.contact_id,
        stage=sync.stage,
        seam_status=seam_status,
    )


@router.post("/enrollment/families/bulk-seed", response_model=BulkSeedResponse)
def bulk_seed_families(
    request: BulkSeedRequest,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
) -> BulkSeedResponse:
    """Bulk-seed a selection — a THIN loop over the per-family seed path (A-20).

    NOT a new write path: each known family runs the SAME ``push_family`` through
    the SIMULATED CRM adapter (CRM_MODE=simulate — no live writes this run, A-17;
    INV-9). The seam is DERIVED from the post-push adapter mirror (A-7), never
    asserted ``synced``. Unknown family ids are skipped (a bulk selection is
    resilient — no 404 aborts the whole batch). One ``batch_id`` tags the audit
    group (NFR-6).
    """
    batch_id = _batch_id("bulk-seed", request.family_ids)
    captured: list[BulkSeedCaptured] = []

    for family_id in request.family_ids:
        joined = repository.get_family(family_id)
        if joined is None:
            continue  # resilient: skip unknown ids rather than abort the batch.

        record = joined.family
        sync = crm_adapter.push_family(record)
        # Derive-and-return the post-push §4.7 seam (A-7) from the adapter mirror —
        # not asserted, mode-agnostic (the simulated mirror reflects what we pushed).
        synced_record = record.model_copy(update={"crm_synced_at": record.updated_at})
        mirror = crm_adapter.read_mirror(family_id)
        seam_status = derive_seam_status(synced_record, mirror)
        captured.append(
            BulkSeedCaptured(
                family_id=family_id,
                deal_id=sync.recorded_id,
                seam_status=seam_status,
            )
        )

    return BulkSeedResponse(
        batch_id=batch_id,
        counts=BulkSeedCounts(captured=len(captured)),
        captured=captured,
    )


@router.post("/enrollment/families/bulk-dismiss", response_model=BulkDismissResponse)
def bulk_dismiss_families(
    request: BulkDismissRequest,
    log: LogDep,
) -> BulkDismissResponse:
    """Bulk-dismiss a selection — a THIN loop over the per-family dismiss write (A-20).

    Loops ``log_dismiss`` (the ONE new audit write; A-19) for each family with the
    shared, REQUIRED ``reason`` — a blank reason is rejected 422 by the request
    schema before any dismiss is logged. Each dismissed family then derives
    ``recovery_state=dismissed`` (until a later re-stall supersedes it). One
    ``batch_id`` tags the audit group (NFR-6). No second write path: this is the
    same family-keyed dismiss event the single path appends.
    """
    batch_id = _batch_id("bulk-dismiss", request.family_ids)
    dismissed: list[UUID] = []
    for family_id in request.family_ids:
        log.log_dismiss(family_id=family_id, human=DEFAULT_HUMAN, reason=request.reason)
        dismissed.append(family_id)

    return BulkDismissResponse(
        batch_id=batch_id,
        counts=BulkDismissCounts(dismissed=len(dismissed)),
        dismissed=dismissed,
    )


@router.post("/enrollment/families/bulk-assign", response_model=BulkAssignResponse)
def bulk_assign_families(
    request: BulkAssignRequest,
    repository: RepositoryDep,
    log: LogDep,
) -> BulkAssignResponse:
    """Assign a selection of families to one sales agent — the M4 write (A-30).

    A DETERMINISTIC core write, NOT an LLM call (INV-2): the deterministic core
    (``repository.assign_families``) stamps ``assigned_rep_id`` + ``assigned_at``
    on each KNOWN family (the owner-authority flip — the DB now owns deal
    ownership, A-30; ``app/core/seam.py``). A 1-element ``family_ids`` list is the
    single-assign case. ``agent_id`` is validated against the static
    ``sales_agents`` registry FIRST — an unknown agent is rejected 400 before any
    write (fail-closed). Unknown family ids are skipped (resilient bulk, like
    ``bulk-seed``).

    Each assignment is logged to the §10 audit spine (NFR-6 who/what/when): one
    ``proposal`` per family carrying the chosen ``agent_id`` (WHAT) plus a
    ``decision`` (WHO=operator, WHEN=now, action=approve). No eval is attached —
    this is a deterministic operator write, not an AI proposal-eval path. One
    ``batch_id`` tags the audit group.
    """
    if sales_agents.lookup(request.agent_id) is None:
        # Fail-closed: only a registered agent may own a deal (no write, no log).
        raise HTTPException(status_code=400, detail="unknown agent_id")

    assigned_at = datetime.now(UTC)
    assigned = repository.assign_families(request.family_ids, request.agent_id, assigned_at)

    # Log each assignment to the audit spine (NFR-6). Deterministic ⇒ no eval.
    for family_id in assigned:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=_ASSIGN_FLOW,
            schema_version=_ASSIGN_SCHEMA_VERSION,
            payload={
                "agent_id": str(request.agent_id),
                "rule": "manual-override",
                "reason": f"manual-override: operator assigned {request.agent_id}",
            },
            family_id=family_id,
            created_at=assigned_at,
        )
        log.log_decision(
            proposal_id=proposal_id,
            human=DEFAULT_HUMAN,
            action=DecisionAction.APPROVE,
            created_at=assigned_at,
        )

    return BulkAssignResponse(
        batch_id=_batch_id("bulk-assign", request.family_ids),
        agent_id=request.agent_id,
        counts=BulkAssignCounts(assigned=len(assigned)),
        assigned=assigned,
    )


def _lead_signals(joined: JoinedFamily) -> LeadSignals:
    """Project a joined family onto the router's :class:`LeadSignals` (§2–§6).

    Reads the spine's territory/income/stage/funding + ownership fields and the
    lead's child count. The value/at-risk/deadline refinements of ``is_hot`` are
    left at defaults in v1 (readiness derives from stage + funding); wiring the
    work-queue/voucher signals in is a documented refinement (LEAD_ASSIGNMENT §5).
    """
    f = joined.family
    return LeadSignals(
        family_id=f.family_id,
        state=f.state,
        income_tier=f.income_tier,
        current_stage=f.current_stage,
        funding_state=f.funding_state,
        assigned_rep_id=f.assigned_rep_id,
        reported_rep_id=f.reported_rep_id,
        num_children=joined.lead.num_children if joined.lead else 1,
    )


@router.post("/enrollment/leads/auto-assign", response_model=AutoAssignResponse)
def auto_assign_leads(
    request: AutoAssignRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
) -> AutoAssignResponse:
    """Deterministically route inbound leads to sales agents (LEAD_ASSIGNMENT.md §2).

    The on-camera "route the new leads" action. ``family_ids`` omitted ⇒ the whole
    UNASSIGNED intake pool; else just those families. For each lead the PURE router
    (``app.core.lead_routing.route_lead``) decides ``(agent, role, reason)`` by the
    first-match precedence (owner-match → territory → readiness → income → weighted
    RR, cap-beats-weight); the DETERMINISTIC core then writes (INV-2, never an LLM):
    it stamps ``assigned_rep_id`` (promoting a resolved self-reported owner),
    appends an immutable ``lead_assignment`` history row, persists the pool cursor,
    and logs the reason to the §10 audit spine (NFR-6). A HELD lead (ambiguous
    identity / parked / all-capped) is a fail-closed non-assignment — surfaced in
    the result, never guessed onto an agent (INV-4).
    """
    if request.family_ids:
        joined = [j for fid in request.family_ids if (j := repository.get_family(fid)) is not None]
    else:
        joined = repository.list_joined(owner=UNASSIGNED)

    # Per-agent current load (open book size) for the cap-beats-weight rule (§7).
    loads: dict[UUID, int] = {}
    for fam in repository.list_families():
        if fam.assigned_rep_id is not None:
            loads[fam.assigned_rep_id] = loads.get(fam.assigned_rep_id, 0) + 1

    cursors = repository.read_cursors()
    batch_id = _batch_id("auto-assign", [j.family.family_id for j in joined])
    assigned_at = datetime.now(UTC)
    results: list[AutoAssignResult] = []

    for jf in joined:
        signals = _lead_signals(jf)
        decision = route_lead(
            signals, sales_agents.SALES_AGENTS, params, cursors=cursors, loads=loads
        )
        held = decision.agent_id is None
        results.append(
            AutoAssignResult(
                family_id=decision.family_id,
                agent_id=decision.agent_id,
                routed_role=decision.routed_role,
                rule=decision.rule,
                reason=decision.reason,
                owner_match=decision.owner_match,
                held=held,
            )
        )
        if held:
            continue  # fail-closed: nothing written, nothing owned (INV-4)

        agent_id = decision.agent_id
        assert agent_id is not None
        prior = jf.family.assigned_rep_id
        if prior == agent_id:
            # Owner-match NO-OP: the family is already owned by this agent (the
            # "never silently reassign" / duplicate-lead guard). Nothing changes —
            # no write, no history row, no audit decision.
            continue
        # Persist the owner (promotes a resolved self-reported owner → assigned_rep_id,
        # the server-side write the client never makes — INV-5 IDOR guard).
        repository.assign_families([decision.family_id], agent_id, assigned_at)
        # Append the immutable ownership-history fact (§10).
        repository.append_assignment_event(
            family_id=decision.family_id,
            from_rep_id=prior,
            to_rep_id=agent_id,
            routed_role=decision.routed_role,
            assigned_by="router",
            reason=decision.reason,
            batch_id=batch_id,
        )
        # Advance + persist the pool's round-robin cursor (§7).
        if decision.cursor_advanced_to is not None and decision.pool_key:
            cursors[decision.pool_key] = decision.cursor_advanced_to
            repository.write_cursor(decision.pool_key, decision.cursor_advanced_to)
        # Reflect the new load so the next lead in this batch sees it (cap math).
        loads[agent_id] = loads.get(agent_id, 0) + 1
        # Log the decision + its reason to the §10 audit spine (NFR-6; deterministic
        # ⇒ no eval). The reason is the WHY, not just the WHO.
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=_ASSIGN_FLOW,
            schema_version=_ASSIGN_SCHEMA_VERSION,
            payload={
                "agent_id": str(agent_id),
                "routed_role": decision.routed_role,
                "rule": decision.rule,
                "reason": decision.reason,
                "owner_match": decision.owner_match,
            },
            family_id=decision.family_id,
            created_at=assigned_at,
        )
        log.log_decision(
            proposal_id=proposal_id,
            human="router",
            action=DecisionAction.APPROVE,
            created_at=assigned_at,
        )

    n_assigned = sum(1 for r in results if not r.held)
    return AutoAssignResponse(
        batch_id=batch_id,
        counts=AutoAssignCounts(assigned=n_assigned, held=len(results) - n_assigned),
        results=results,
    )


@router.post("/enrollment/leads/sla-sweep", response_model=SlaSweepResponse)
def sla_sweep(
    request: SlaSweepRequest,
    repository: RepositoryDep,
    params: ParamsDep,
    log: LogDep,
) -> SlaSweepResponse:
    """Reassign leads left unworked past the SLA timer (LEAD_ASSIGNMENT.md §9).

    For each ASSIGNED family, a lead is breached when it was assigned past
    ``params.assignment.sla.unworked_reassign_days`` ago AND has no logged outbound
    contact since (the assignment decision itself is excluded — it is not contact).
    The ``owned_breach`` policy governs the action: ``alert`` flags it for an admin
    without moving it (the "one source of truth" default); ``auto_reassign`` reroutes
    it AWAY from the breached rep (``exclude=``) to a different agent, appends a
    from→to history row, re-stamps the timer, and logs the reason (NFR-6).
    Anti-ping-pong: after ``max_reassignments`` SLA hops the lead is ESCALATED to
    the intake pool rather than rotated forever. ``as_of`` overrides "now" (a
    deterministic clock); deterministic core owns every write (INV-2).
    """
    now = request.as_of or datetime.now(UTC)
    sla = params.assignment.sla
    families = [f for f in repository.list_families() if f.assigned_rep_id is not None]
    batch_id = _batch_id("sla-sweep", [f.family_id for f in families])

    loads: dict[UUID, int] = {}
    for f in families:
        assert f.assigned_rep_id is not None
        loads[f.assigned_rep_id] = loads.get(f.assigned_rep_id, 0) + 1
    cursors = repository.read_cursors()
    results: list[SlaSweepResult] = []

    for fam in families:
        contacted = last_contact_at(log, fam.family_id, exclude_flow=_ASSIGN_FLOW)
        if not is_sla_breached(fam.assigned_at, contacted, now, params):
            continue
        old_owner = fam.assigned_rep_id
        assert old_owner is not None

        # owned_breach = alert (default): flag it, do NOT silently move it (§9).
        if sla.owned_breach == "alert":
            reason = (
                f"sla-alert: lead unworked past {sla.unworked_reassign_days}d since "
                f"{fam.assigned_at} — owner {old_owner} alerted, not reassigned"
            )
            log.log_proposal(
                proposal_id=uuid4(),
                flow=_ASSIGN_FLOW,
                schema_version=_ASSIGN_SCHEMA_VERSION,
                payload={"rule": "sla-alert", "reason": reason, "agent_id": str(old_owner)},
                family_id=fam.family_id,
                created_at=now,
            )
            results.append(
                SlaSweepResult(
                    family_id=fam.family_id,
                    action="alerted",
                    from_rep_id=old_owner,
                    to_rep_id=old_owner,
                    reason=reason,
                )
            )
            continue

        # owned_breach = auto_reassign. Anti-ping-pong: after max_reassignments SLA
        # hops, escalate to the intake pool rather than rotate forever.
        prior_hops = sum(
            1 for h in repository.list_assignments(fam.family_id) if h.reason.startswith("sla-")
        )
        if prior_hops >= sla.max_reassignments:
            repository.unassign_families([fam.family_id], now)
            reason = (
                f"sla-reassign cap reached ({prior_hops} hops ≥ {sla.max_reassignments}) "
                f"→ escalated to intake pool"
            )
            repository.append_assignment_event(
                family_id=fam.family_id,
                from_rep_id=old_owner,
                to_rep_id=None,
                routed_role=None,
                assigned_by="sla-sweep",
                reason=reason,
                batch_id=batch_id,
            )
            loads[old_owner] = max(0, loads.get(old_owner, 0) - 1)
            results.append(
                SlaSweepResult(
                    family_id=fam.family_id,
                    action="escalated",
                    from_rep_id=old_owner,
                    to_rep_id=None,
                    reason=reason,
                )
            )
            continue

        # Reroute AWAY from the breached rep: route as a NEW lead (drop the sticky
        # owner) with the breached agent excluded, so it lands on a different agent.
        joined = repository.get_family(fam.family_id)
        if joined is None:
            continue
        signals = _lead_signals(joined).model_copy(
            update={"assigned_rep_id": None, "reported_rep_id": None}
        )
        decision = route_lead(
            signals,
            sales_agents.SALES_AGENTS,
            params,
            cursors=cursors,
            loads=loads,
            exclude=frozenset({old_owner}),
        )
        if decision.agent_id is None:
            # No other agent could take it → escalate to intake rather than hold.
            repository.unassign_families([fam.family_id], now)
            reason = f"sla-reassign: no alternate agent ({decision.reason}) → escalated to intake"
            repository.append_assignment_event(
                family_id=fam.family_id,
                from_rep_id=old_owner,
                to_rep_id=None,
                routed_role=None,
                assigned_by="sla-sweep",
                reason=reason,
                batch_id=batch_id,
            )
            results.append(
                SlaSweepResult(
                    family_id=fam.family_id,
                    action="escalated",
                    from_rep_id=old_owner,
                    to_rep_id=None,
                    reason=reason,
                )
            )
            continue

        new_owner = decision.agent_id
        reason = (
            f"sla-reassign: unworked past {sla.unworked_reassign_days}d since "
            f"{fam.assigned_at}; rerouted from {old_owner} → {decision.reason}"
        )
        repository.assign_families([fam.family_id], new_owner, now)
        repository.append_assignment_event(
            family_id=fam.family_id,
            from_rep_id=old_owner,
            to_rep_id=new_owner,
            routed_role=decision.routed_role,
            assigned_by="sla-sweep",
            reason=reason,
            batch_id=batch_id,
        )
        if decision.cursor_advanced_to is not None and decision.pool_key:
            cursors[decision.pool_key] = decision.cursor_advanced_to
            repository.write_cursor(decision.pool_key, decision.cursor_advanced_to)
        loads[old_owner] = max(0, loads.get(old_owner, 0) - 1)
        loads[new_owner] = loads.get(new_owner, 0) + 1
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow=_ASSIGN_FLOW,
            schema_version=_ASSIGN_SCHEMA_VERSION,
            payload={"rule": "sla-reassign", "reason": reason, "agent_id": str(new_owner)},
            family_id=fam.family_id,
            created_at=now,
        )
        log.log_decision(
            proposal_id=proposal_id,
            human="sla-sweep",
            action=DecisionAction.APPROVE,
            created_at=now,
        )
        results.append(
            SlaSweepResult(
                family_id=fam.family_id,
                action="reassigned",
                from_rep_id=old_owner,
                to_rep_id=new_owner,
                reason=reason,
            )
        )

    return SlaSweepResponse(
        batch_id=batch_id,
        counts=SlaSweepCounts(
            alerted=sum(1 for r in results if r.action == "alerted"),
            reassigned=sum(1 for r in results if r.action == "reassigned"),
            escalated=sum(1 for r in results if r.action == "escalated"),
        ),
        results=results,
    )

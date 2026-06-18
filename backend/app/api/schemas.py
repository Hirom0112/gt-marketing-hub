"""Pydantic response schemas for the read API (ARCHITECTURE.md §6).

All read-only (INV-2 for S0). These shape the GET responses for the landing
dashboard: per-stage pipeline counts (FR-2.1) and the basic joined Family Record
deal view (FR-2.2 — the full deal view lands in S1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.schemas.brand import LibraryAsset
from app.ai.schemas.close_tips import CloseTipsProposal
from app.ai.schemas.content import AudienceTag, Channel, Decision
from app.ai.schemas.enrollment_draft import Claim, DraftAction, EnrollmentDraftProposal
from app.core.contact_status import ContactStatus
from app.core.eval_gate import ValidationResult
from app.core.family_record import DealView
from app.core.recovery_state import RecoveredOutcome, RecoveryState
from app.core.sis_reconcile import SisBucket
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    FundingType,
    LeadsNew,
    SeamStatus,
    Stage,
    StallReason,
)
from app.observability.log_store import (
    DecisionAction,
    DecisionRecord,
    EvalRecord,
    ProposalRecord,
)


class PipelineResponse(BaseModel):
    """Per-stage pipeline tally + CRM-seam summary (FR-2.1, FR-2.6).

    ``counts`` is keyed by the §4.8 funnel stage (interest/apply/enroll/tuition),
    every stage present (zero-filled). ``total`` is the family total, == the sum
    of ``counts`` — handy for the dashboard's "of N families" copy. ``seam`` is
    the §4.7 Supabase↔HubSpot seam summary (synced/unsynced/conflict), surfaced
    read-only on the landing dashboard.

    ``student_counts`` is the per-CHILD grain (A-24): each child placed in its OWN
    derived stage, so a multi-child household spans every stage its children are in
    (Rivera → one in ``enroll``, one in ``tuition``). ``total_students`` is its sum.
    Both zero-filled. The board can show households AND children per column.
    """

    counts: dict[Stage, int]
    total: int
    seam: dict[SeamStatus, int]
    student_counts: dict[Stage, int]
    total_students: int


class FamilyDetailResponse(BaseModel):
    """A spine row joined to its four source rows, plus the FR-2.2 deal view (§6).

    `GET /families/{id}` stays the "full joined Family Record" (§6): the spine
    and its four source rows are kept (the S0 contract). S1 adds ``deal_view`` —
    the flat operator-facing FR-2.2 projection from
    :func:`app.core.family_record.assemble_deal_view` over the joined rows
    (stall reason, funding tier, MAP score, attribution, derived seam status).
    """

    family: FamilyRecord
    lead: LeadsNew | None
    app_form: AppForm | None
    enrollment_forms: EnrollmentForms | None
    community_profile: CommunityProfile | None
    deal_view: DealView


class DropOffResponse(BaseModel):
    """One family's last apply-flow position before exit (A-24; `GET …/drop-off`).

    Step → form → field granularity from ``apply_events``: ``step`` ∈
    {interest, apply, enroll, tuition}, ``form_key`` the sub-form id (e.g.
    ``data_collection_consent``; ``None`` for step-level events), ``field_key``
    the field within it. Metadata only — ``form_key`` is a STRUCTURAL form id,
    never a typed value/content and never a child key (INV-1/INV-6/COPPA). The
    route returns a 204 (no content) when the family emitted no events (or the
    active store does not carry telemetry), so this body never has to be nullable.
    """

    family_id: UUID
    step: str
    form_key: str | None = None
    field_key: str | None = None
    event_type: str
    occurred_at: str | None = None


class DropOffBucketResponse(BaseModel):
    """One cohort drop-off heatmap cell (A-24) — an exit count at a step/form/field.

    Aggregate only: ``count`` families froze at this (``step``, ``form_key``,
    ``field_key``) cell. No family/child identity — *where* the cohort freezes,
    not *who*. ``form_key`` is a structural sub-form id (INV-1/INV-6).
    """

    step: str
    form_key: str | None = None
    field_key: str | None = None
    count: int


class DropOffHeatmapResponse(BaseModel):
    """The cohort drop-off heatmap (A-24; `GET /drop-off/heatmap`).

    A list of exit-count cells, ordered count-desc then step/form/field. Empty
    (``buckets: []``) — never a 500 — when the active store carries no telemetry
    (the in-memory v1 fallback).
    """

    buckets: list[DropOffBucketResponse]


class WorkQueueItem(BaseModel):
    """One ranked work-queue row (FR-2.5; §6 `GET /work-queue`).

    The deterministic deal card the queue UI renders: family identity plus the
    score and its two interpretable components (``recoverability``, ``value``)
    so an operator sees *why* a family ranks where it does. Computed entirely by
    the pure :mod:`app.core.work_queue` scorer — never by an LLM (INV-2).
    """

    family_id: UUID
    display_name: str
    current_stage: Stage
    score: float
    recoverability: float
    value: float
    # A-23 — the value drivers, surfaced so the row can show the HONEST secondary
    # ("3 kids · $31,200") and the funding label ("Texas voucher" / "Self-pay")
    # instead of a synthetic per-family $. ``num_children`` scales value; every
    # targeted family is full-pay so ``funding_type`` is voucher/self-pay.
    num_children: int
    funding_type: FundingType | None = None
    # Assignment contract (LEAD_ASSIGNMENT.md §10a) — the deal owner + WHEN they
    # were assigned, surfaced on the triage/work-queue row so the rep calendar can
    # key families by assignment date. Owner-scoped server-side (the resolve_owner_scope
    # IDOR clamp — an agent only ever sees its own book). NULL ⇒ the unassigned
    # intake pool. STABLE field names — a parallel calendar workstream reads these.
    assigned_rep_id: UUID | None = None
    assigned_at: datetime | None = None
    # The family's stall-anchor instant (the same derivation the calendar groups
    # on — :func:`app.api.families._stall_date`), composed at the API layer (it
    # needs ``now`` + the audit log, INV-2). Lets the Show-all list group rows
    # under day headers and always show a stall-date column. Never None — the
    # ``_stall_date`` precedence chain always resolves (tier 4 = ``created_at``).
    stall_date: datetime
    # S12 W1 — the recoverable-now ranking key (``value × variance × score ×
    # freshness``) the queue is now ordered by, plus its ``freshness`` factor so
    # the UI can show the time-decay component. Both from the pure work-queue
    # scorer (INV-2); ``recoverable_now`` is a dollars-weighted magnitude, not [0,1].
    recoverable_now: float
    freshness: float
    # Contact-recency (S9 W3; A-14) — composed in the API layer (now + audit log
    # + params), NOT the pure scorer, so the board/queue can color a family
    # without N extra calls. ``contact_status`` is the recency color;
    # ``last_contact_at`` is the latest approved-outbound instant (None if never).
    contact_status: ContactStatus
    last_contact_at: datetime | None = None
    # S12 W1 — the derived recovery state (A-19), composed in the API layer (it
    # needs ``now`` + the audit log for the dismiss/contact facts), NOT the pure
    # scorer. {stalled, working, recovered, dismissed}.
    recovery_state: RecoveryState
    # History-scope OUTCOME story (A-19) — populated ONLY on ``scope=history`` rows
    # so the active/triage contract stays byte-identical (these all default null,
    # so an active row simply omits them). For a RECOVERED row: which predicate in
    # ``derive_recovery_state`` fired (``recovered_outcome``) plus the approximate
    # instant the family left the active board (``resolved_at``). For a DISMISSED
    # row: the logged ``DismissRecord`` reason / operator / instant. Both groups are
    # mutually exclusive — a recovered row has null dismiss fields and vice-versa.
    recovered_outcome: RecoveredOutcome | None = None
    resolved_at: datetime | None = None
    dismiss_reason: str | None = None
    dismissed_by: str | None = None
    dismissed_at: datetime | None = None


class StudentRow(BaseModel):
    """One ranked per-child row on the board (A-24; `GET /students`).

    Each child runs its own funnel (one application per child), so the board
    ranks STUDENTS. The row carries the child's own funnel state + score and its
    parent household identity (``family_id`` + ``household_name``) so the UI can
    group rows by household. ``value`` is one per-child tuition; the household's
    $-at-risk is the sum of its still-recoverable students. Computed by the pure
    :mod:`app.core.work_queue` scorer + the recovery deriver — never an LLM (INV-2).
    """

    student_id: UUID
    family_id: UUID  # the household, for grouping rows on the board.
    household_name: str  # the parent FamilyRecord.display_name.
    display_label: str  # the distinct per-student label ("Rivera household — Alex · Grade 3").
    synthetic_first_name: str
    grade: str

    current_stage: Stage
    funding_type: FundingType | None = None
    funding_state: FundingState
    stall_reason: StallReason | None = None

    # Pure scorer outputs (INV-2). ``value`` is one per-child tuition (A-24 — no
    # num_children multiplier); ``recoverable_now`` is the dollars-weighted key the
    # board orders students by; both terms also surfaced so the row shows WHY.
    score: float
    recoverability: float
    value: float
    recoverable_now: float
    freshness: float

    # Per-child derived recovery state (A-24). {stalled, working, recovered,
    # dismissed}: recovered (its own funnel moved), working (a per-child approved
    # outbound exists), dismissed (a per-child dismiss holds — POST /students/{id}/
    # dismiss), else stalled. Resolved per (family_id, student_id) so it never
    # reflects a sibling's or a family-level event.
    recovery_state: RecoveryState


class HouseholdGroup(BaseModel):
    """A household's students grouped together for the board (A-24; `GET /students`).

    Groups a family's :class:`StudentRow`s under the household, with the
    household's aggregate ``value_at_risk`` = the SUM of its students' ``value``
    over the ones still active (``recovery_state ∈ {stalled, working}``) — the
    per-child replacement for the old all-or-nothing family value (A-24).
    """

    family_id: UUID
    household_name: str
    value_at_risk: float
    students: list[StudentRow]


class StudentBoardResponse(BaseModel):
    """The per-child board (A-24; `GET /students`) — households + roll-up totals.

    ``households`` are ordered by their top student's rank (the most-recoverable
    child surfaces its household first); ``students`` within a household are
    ranked too. ``total_value_at_risk`` sums every household's ``value_at_risk``;
    ``total_students`` is the row count — the situation bar reads these.
    """

    households: list[HouseholdGroup]
    total_students: int
    total_value_at_risk: float


class StudentDismissRequest(BaseModel):
    """`POST /students/{id}/dismiss` body (A-24) — the per-child dismiss reason.

    A per-child dismiss is the only MANUAL recovery removal of one child (A-19:
    recovered is DETECTED, never a button). ``reason`` is REQUIRED — a blank
    reason is rejected 422 before any event is logged, so the audit always records
    *why* a child was set aside (INV-2).
    """

    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        """Reject a whitespace-only reason 422 (the audit needs a real why; A-19)."""
        if not value.strip():
            raise ValueError("dismiss reason must not be blank")
        return value


class StudentDismissResponse(BaseModel):
    """`POST /students/{id}/dismiss` result (A-24) — the child's new recovery state.

    Echoes the dismissed child's ids and its recomputed ``recovery_state`` (now
    ``dismissed``, the highest-precedence state) so the UI can drop the row from
    the active board without a re-fetch.
    """

    student_id: UUID
    family_id: UUID
    recovery_state: RecoveryState
    reason: str


class CalendarEntry(BaseModel):
    """One family on the enrollment calendar (S11 W1; ARCH §6 `GET /enrollment/calendar`).

    The month-view + color-coded board (Wave 4) renders one of these per family
    whose ``stall_date`` falls in the resolved month. ``stall_date`` (S11 W1;
    ASSUMPTIONS A-16) is the grouping/anchor key — the first available of:
    ``family.stalled_since`` → ``last_contact_at`` → ``created_at + overdue_days``
    → ``created_at`` — so the surface clusters on when a family went quiet, not
    when it applied. ``apply_date`` (``app_form.submitted_at`` else spine
    ``created_at``) is retained for reference. ``contact_status`` is composed in
    the API layer (now + audit log + params), same as the deal view (INV-2 core
    purity). ``value`` (recovery dollars) and ``score`` (0..1) reuse the pure
    work-queue scorer so the board can size/sort entries by recovery worth.
    """

    family_id: UUID
    display_name: str
    stall_date: datetime
    apply_date: datetime
    current_stage: Stage
    contact_status: ContactStatus
    value: float
    # A-23 — value drivers for the calendar entry (child count + funding label).
    num_children: int
    funding_type: FundingType | None = None
    score: float
    # S12 W1 — the recoverable-now ranking key + its freshness factor (pure
    # scorer), and the derived recovery_state (A-19, API-composed) so the heat
    # calendar and its drill list can size/scope a chip without N extra calls.
    recoverable_now: float
    freshness: float
    recovery_state: RecoveryState


class CalendarResponse(BaseModel):
    """The `GET /enrollment/calendar?month=YYYY-MM` payload (S11 W1; ARCH §6).

    ``month`` echoes the **resolved** ``YYYY-MM`` (the actual month returned —
    when the caller omits ``month`` it resolves to the YYYY-MM of the most-recent
    ``stall_date``, so the client can read back what it got); ``entries`` are the
    in-month families sorted ascending by ``stall_date`` (empty list for a month
    with no stalls — never an error).
    """

    month: str
    entries: list[CalendarEntry] = Field(default_factory=list)


class LeadsCalendarAgentCount(BaseModel):
    """One agent's NEW-lead count on one calendar day (DECISIONS.md D-3).

    The per-day chip the Leads-tab calendar renders: the owning agent's identity
    (read off the static :data:`app.core.sales_agents.SALES_AGENTS` registry —
    synthetic name only, INV-1) plus how many of that day's intake leads are
    assigned to them. Read-only aggregation (INV-2).
    """

    agent_id: UUID
    synthetic_name: str
    count: int


class LeadsCalendarDay(BaseModel):
    """One populated day on the Leads calendar — per-agent chips + the unowned pool.

    ``agents`` is one :class:`LeadsCalendarAgentCount` per agent with ≥1 lead that
    day (sorted by synthetic_name); ``unowned_count`` is the day's intake leads
    with a NULL ``assigned_rep_id`` (the unassigned pool); ``total`` is the day's
    whole intake count (the agents' counts + ``unowned_count``).
    """

    day: int
    agents: list[LeadsCalendarAgentCount] = Field(default_factory=list)
    unowned_count: int
    total: int


class LeadsCalendarResponse(BaseModel):
    """The `GET /enrollment/leads-calendar?month=YYYY-MM` payload (D-3; INV-2).

    ``month`` echoes the **resolved** YYYY-MM (when the caller omits ``month`` it
    resolves to the month of the most-recent intake date so the surface opens
    non-empty); ``days`` are the populated days (zero-lead days omitted) ascending
    by day-of-month. Read-only.
    """

    month: str
    days: list[LeadsCalendarDay] = Field(default_factory=list)


class AgentRollup(BaseModel):
    """One agent's roster row on the admin lens (M3 R1; MULTI_AGENT_COCKPIT §5).

    A PURE AGGREGATION over the SAME derivations ``/work-queue`` already computes,
    grouped by ``assigned_rep_id`` — no new scoring math (PLAN M3 R1):

    - ``queue_size`` — count of this agent's families in the ACTIVE work-queue set
      (recovery_state ∈ {stalled, working}, the same active pre-filter + deriver the
      work-queue route uses).
    - ``stall_rate`` — fraction of that active set whose derived recovery_state is
      ``stalled`` (gone quiet, not yet worked) — the EXISTING work-queue stall signal.
    - ``close_rate`` — fraction of the agent's active-candidate book that RECOVERED,
      via the EXISTING ``recovered_outcome`` (the same close signal the work-queue's
      recovery summary uses); never a new close metric.
    - ``load`` — ``queue_size`` relative to ``params.assignment.per_tier_load_cap``
      (a ratio; the cap is the single params home, INV-11).

    ``agent_id`` / ``synthetic_name`` / ``tier`` are identity, read straight off the
    static sales-agent registry — not a recomputation. An ``unowned`` bucket (the
    intake pool) carries the same metrics with a null identity.
    """

    agent_id: UUID | None = None
    synthetic_name: str | None = None
    tier: str | None = None
    queue_size: int
    stall_rate: float
    close_rate: float
    load: float


class AgentsResponse(BaseModel):
    """The admin-lens per-agent roster (M3 R1; `GET /enrollment/agents`).

    ``agents`` is one :class:`AgentRollup` per registered demo agent (rank order);
    ``unowned`` is the intake pool roll-up (``assigned_rep_id IS NULL``). Read-only
    aggregation over the existing work-queue outputs (INV-2 — no writes).
    """

    agents: list[AgentRollup]
    unowned: AgentRollup


# The agent-dashboard time-window selector (D-14/D-15). ``all`` is unbounded; the
# others narrow to a trailing window whose day-count lives in ``params.kpi.windows``
# (INV-11 — never a hardcoded literal). A named Literal so FastAPI validates the
# query param (a bad value is a 422, never silently treated as ``all``).
KpiWindow = Literal["day", "week", "month", "all"]


class AgentKpisResponse(BaseModel):
    """One agent's personal KPIs over a window (D-14; `GET /enrollment/agent-kpis`).

    The sales-agent KPI Dashboard (Tab 5) surface. Each field is a PURE aggregation
    over already-logged facts — the family's ``assigned_at`` (Leads Assigned), the
    contact-outcome log (Contacts Made / Follow-Ups Completed / Appointments Booked),
    ``app_form`` state (Applications Started / Completed), and ``funding_state``
    (Conversion Rate = funded ÷ assigned). No new applicant data (INV-1); owner-scoped
    (INV-5); read-only (INV-2). ``conversion_rate`` is a 4-dp float in [0, 1].
    """

    window: KpiWindow
    leads_assigned: int
    contacts_made: int
    follow_ups_completed: int
    appointments_booked: int
    applications_started: int
    applications_completed: int
    conversion_rate: float


# --------------------------------------------------------------------------- #
# S2 AI action surface (FR-2.4; ARCH §5.2/§6; INV-2/INV-3/INV-4).
# --------------------------------------------------------------------------- #
class DraftRequest(BaseModel):
    """`POST /ai/enrollment/draft` body — which family + which channel (§6)."""

    family_id: UUID
    action: DraftAction


class DraftResponse(BaseModel):
    """The §5.2 draft outcome surfaced to the client (INV-3/INV-4 at the boundary).

    The proposal body is surfaced **only** when ``surfaced`` is True (the eval
    passed). On a block/degrade ``proposal`` is ``None`` (no usable body to act
    on — the UI offers the deterministic template fallback) but ``proposal_id``
    is always present so the client can call the decision endpoint, and
    ``failed_rules`` carries the gate's reasons for the audit-aware UI.
    """

    proposal_id: UUID
    surfaced: bool
    degraded: bool
    failed_rules: list[str] = Field(default_factory=list)
    proposal: EnrollmentDraftProposal | None = None
    validation: ValidationResult | None = None


class UngatedDraftRequest(BaseModel):
    """`POST /ai/enrollment/draft-ungated` body — which family + which channel (D-1).

    ``channel`` is the panel's own vocabulary (``email`` / ``sms``); the endpoint
    maps it to the shared :class:`DraftAction` internally (email ⇒ EMAIL, sms ⇒
    NUDGE — a nudge is the short-message form) and echoes ``channel`` back so the
    UI labels the draft correctly. The DraftAction enum is NOT extended (the eval
    suite iterates it) — the channel lives only on this request/response pair.
    """

    family_id: UUID
    channel: Literal["email", "sms"]


class UngatedDraftResponse(BaseModel):
    """The ungated detail-panel draft (DECISIONS.md D-1; INV-2 — a proposal, not a send).

    No eval gate runs on this surface (D-1): the human edits and sends manually,
    so the body is ALWAYS surfaced (there is no fail-closed suppression here). The
    proposal is still LOGGED for the audit (NFR-6). ``degraded`` is True when the
    metered edge was unavailable / capped (INV-8) and the operator template stands
    in; ``channel`` echoes the requested channel; ``claims`` carries the proposal's
    grounding claims (empty for a wrapped raw-text or template draft).
    """

    proposal_id: UUID
    channel: str
    degraded: bool
    body: str
    claims: list[Claim] = Field(default_factory=list)


class CloseTipsRequest(BaseModel):
    """`POST /ai/enrollment/close-tips` body — which family (S9 W5; §6)."""

    family_id: UUID


class CloseTipsResponse(BaseModel):
    """The §5.2 close-tips outcome surfaced to the client (INV-3/INV-4 boundary).

    The tips body is surfaced **only** when ``surfaced`` is True (the eval passed
    AND the close-tips grounding layer resolved). On a block/degrade ``proposal``
    is ``None`` (no usable tips to act on) but ``proposal_id`` is always present so
    the client can call the decision endpoint, and ``failed_rules`` carries the
    gate's reasons (incl. ``close_tips_grounding`` for a fabricated citation) for
    the audit-aware UI.
    """

    proposal_id: UUID
    surfaced: bool
    degraded: bool
    failed_rules: list[str] = Field(default_factory=list)
    proposal: CloseTipsProposal | None = None
    validation: ValidationResult | None = None


class DecisionRequest(BaseModel):
    """`POST /proposals/{id}/decision` body — the human verdict (§6; §4.9).

    ``edited_payload`` carries the human's edits when ``action`` is ``edit``; it
    is ignored for approve/discard.
    """

    action: DecisionAction
    edited_payload: dict[str, object] | None = None


class DecisionResponse(BaseModel):
    """The decision result — the ONLY state-applying path (INV-2; NFR-6).

    On ``approve`` a send is recorded through the CRM adapter (``send_simulated``
    True for the simulated recorder, False for a live HubSpot write) and
    ``seam_status`` carries the recomputed §4.7 seam; on edit/discard there is no
    send and ``seam_status`` is ``None``. ``note_id`` is the adapter's recorded
    send id — under ``CRM_MODE=live`` the live HubSpot Note id, so the cockpit
    can deep-link the captured note (S10 W3).
    """

    proposal_id: UUID
    action: DecisionAction
    send_simulated: bool = False
    seam_status: SeamStatus | None = None
    note_id: str | None = None


class SeedResponse(BaseModel):
    """``POST /enrollment/families/{id}/seed`` result — the captured live push (S10 W3).

    The deterministic seed route pushes a synthetic family through the
    ``CRMAdapter`` seam (mode-agnostic): the simulated recorder records, the live
    adapter writes a Contact + Deal into HubSpot. Returns the recorded/live deal
    id, the pushed funnel stage, and the §4.7 seam recomputed after the push
    (``unsynced → synced``). ``simulated`` is False for a live HubSpot write.
    """

    family_id: UUID
    simulated: bool
    deal_id: str
    contact_id: str | None = None
    stage: Stage
    seam_status: SeamStatus


# --------------------------------------------------------------------------- #
# S12 W2 bulk action surface (A-20; INV-2/INV-3/INV-8/INV-9).
# Bulk is a THIN batch UX over the existing per-family gated spine — never a new
# write path. Each route loops the single-family composition internally; the
# operator's one bulk click is the batch human-approval (INV-2), the per-family
# eval gate stays non-negotiable (INV-3 fail-closed). One ``batch_id`` tags the
# audit group (NFR-6). All sends/pushes go through the SIMULATED adapter (INV-9).
# --------------------------------------------------------------------------- #
class BulkNudgeRequest(BaseModel):
    """`POST /ai/enrollment/bulk-nudge` body — the selected families + channel.

    ``action`` defaults to ``nudge`` so a bare family selection still drafts a
    concrete channel. Each family runs the SAME draft + eval gate as the single
    route; the bulk click is the batch human-approval (INV-2).
    """

    family_ids: list[UUID]
    action: DraftAction = DraftAction.NUDGE


class BulkNudgeSent(BaseModel):
    """One eval-passing family in a bulk-nudge run — recorded send + audit head."""

    family_id: UUID
    note_id: str


class BulkNudgeBlocked(BaseModel):
    """One eval-FAILING family in a bulk-nudge run — blocked, logged, NO send (INV-3/4)."""

    family_id: UUID
    failed_rules: list[str] = Field(default_factory=list)


class BulkNudgeCounts(BaseModel):
    """The pre/post partition counts of a bulk-nudge run (the visible gate signal)."""

    sent: int
    blocked: int
    capped: int


class BulkNudgeResponse(BaseModel):
    """The `POST /ai/enrollment/bulk-nudge` partition (A-20; INV-3 fail-closed).

    ``sent`` are eval-passing families whose send was recorded via the SIMULATED
    adapter and whose proposal/eval/approve-decision were logged. ``blocked`` are
    eval-failing families — logged with their failing eval, NEVER sent (INV-4).
    ``capped`` are families deferred past the INV-8 per-run cap — never overspent.
    """

    batch_id: str
    counts: BulkNudgeCounts
    sent: list[BulkNudgeSent] = Field(default_factory=list)
    blocked: list[BulkNudgeBlocked] = Field(default_factory=list)
    capped: list[UUID] = Field(default_factory=list)


class BulkSeedRequest(BaseModel):
    """`POST /enrollment/families/bulk-seed` body — the families to push (S12 W2)."""

    family_ids: list[UUID]


class BulkSeedCaptured(BaseModel):
    """One captured family in a bulk-seed run — the recorded deal + derived seam."""

    family_id: UUID
    deal_id: str
    seam_status: SeamStatus


class BulkSeedCounts(BaseModel):
    """The bulk-seed tally (every requested known family is captured, simulated)."""

    captured: int


class BulkSeedResponse(BaseModel):
    """The `POST /enrollment/families/bulk-seed` result (A-20; INV-9 simulated).

    Loops ``push_family`` through the SIMULATED CRM adapter (CRM_MODE=simulate —
    no live writes this run, A-17); the seam is DERIVED from the adapter mirror,
    not asserted ``synced``. One ``batch_id`` tags the audit group (NFR-6).
    """

    batch_id: str
    counts: BulkSeedCounts
    captured: list[BulkSeedCaptured] = Field(default_factory=list)


class BulkDismissRequest(BaseModel):
    """`POST /enrollment/families/bulk-dismiss` body — families + the required reason.

    ``reason`` is REQUIRED and non-blank (the one new write must say why; A-19): a
    blank reason is rejected 422 before any dismiss is logged.
    """

    family_ids: list[UUID]
    reason: str = Field(min_length=1)


class BulkDismissCounts(BaseModel):
    """The bulk-dismiss tally."""

    dismissed: int


class BulkDismissResponse(BaseModel):
    """The `POST /enrollment/families/bulk-dismiss` result (A-19; A-20).

    Loops ``log_dismiss`` (the one new audit write) for each family with the
    shared ``reason``; dismissed families then derive ``recovery_state=dismissed``.
    One ``batch_id`` tags the audit group (NFR-6).
    """

    batch_id: str
    counts: BulkDismissCounts
    dismissed: list[UUID] = Field(default_factory=list)


class BulkAssignRequest(BaseModel):
    """`POST /enrollment/families/bulk-assign` body — families + the target agent (M4).

    A 1-element ``family_ids`` list is the single-assign case (no separate route).
    ``agent_id`` is validated against the static ``sales_agents`` registry at the
    route (unknown agent → 4xx, fail-closed). The write sets both
    ``assigned_rep_id`` + ``assigned_at`` (the owner-authority flip, A-30).
    """

    family_ids: list[UUID] = Field(min_length=1)
    agent_id: UUID


class BulkAssignCounts(BaseModel):
    """The bulk-assign tally — how many families were assigned (known ids)."""

    assigned: int


class BulkAssignResponse(BaseModel):
    """The `POST /enrollment/families/bulk-assign` result (M4; INV-2; NFR-6).

    A DETERMINISTIC core write (never an LLM call): each known family gets
    ``assigned_rep_id`` + ``assigned_at`` written and a decision logged to the
    audit spine. Unknown ids are skipped (resilient bulk, like ``bulk-seed``).
    One ``batch_id`` tags the audit group.
    """

    batch_id: str
    agent_id: UUID
    counts: BulkAssignCounts
    assigned: list[UUID] = Field(default_factory=list)


class AutoAssignRequest(BaseModel):
    """`POST /enrollment/leads/auto-assign` body — the deterministic router run.

    ``family_ids`` omitted (or empty) ⇒ route the whole UNASSIGNED intake pool
    (the on-camera "route the new leads" action). A non-empty list routes just
    those families (each still gated by the owner-match rule). The router decides;
    the deterministic core writes (INV-2) — no LLM, no eval (deterministic).
    """

    family_ids: list[UUID] = Field(default_factory=list)


class AutoAssignResult(BaseModel):
    """One lead's routing outcome (LEAD_ASSIGNMENT.md §2). ``agent_id is None`` ⇒
    HELD (ambiguous identity / parked / all-capped) — a fail-closed non-assignment.
    ``reason`` is the human-readable rule trace; ``owner_match`` flags the sticky
    existing/self-reported owner path."""

    family_id: UUID
    agent_id: UUID | None
    routed_role: str | None
    rule: str
    reason: str
    owner_match: bool
    held: bool


class AutoAssignCounts(BaseModel):
    """The auto-assign tally — assigned vs held (fail-closed non-assignments)."""

    assigned: int
    held: int


class AutoAssignResponse(BaseModel):
    """The `POST /enrollment/leads/auto-assign` result (LEAD_ASSIGNMENT.md §2; NFR-6).

    Every result carries its reason; each assignment is persisted
    (``assigned_rep_id`` + an append-only ``lead_assignment`` history row) and
    logged to the audit spine. One ``batch_id`` tags the group.
    """

    batch_id: str
    counts: AutoAssignCounts
    results: list[AutoAssignResult] = Field(default_factory=list)


class SlaSweepRequest(BaseModel):
    """`POST /enrollment/leads/sla-sweep` body (LEAD_ASSIGNMENT.md §9).

    ``as_of`` overrides "now" (the deterministic demo clock / a test clock); when
    omitted the server uses the wall clock. The sweep reads ``params.assignment.sla``
    for the timer + the ``owned_breach`` policy (``alert`` vs ``auto_reassign``).
    """

    as_of: datetime | None = None


class SlaSweepResult(BaseModel):
    """One breached lead's SLA outcome (LEAD_ASSIGNMENT.md §9).

    ``action`` ∈ ``alerted`` (owned_breach=alert — flagged, not moved),
    ``reassigned`` (rerouted away from the breached rep), ``escalated`` (reassign
    cap reached → returned to intake). ``reason`` is the human-readable trace.
    """

    family_id: UUID
    action: str
    from_rep_id: UUID | None
    to_rep_id: UUID | None
    reason: str


class SlaSweepCounts(BaseModel):
    """The SLA-sweep tally — alerted / reassigned / escalated breached leads."""

    alerted: int
    reassigned: int
    escalated: int


class SlaSweepResponse(BaseModel):
    """The `POST /enrollment/leads/sla-sweep` result (LEAD_ASSIGNMENT.md §9; NFR-6).

    Every breached lead is logged with WHY it breached and what happened; a
    reassignment appends a from→to history row and re-stamps the SLA timer.
    """

    batch_id: str
    counts: SlaSweepCounts
    results: list[SlaSweepResult] = Field(default_factory=list)


class AuditResponse(BaseModel):
    """The §10 audit view for one proposal — proposal + its evals + decisions (NFR-6)."""

    proposal: ProposalRecord
    evals: list[EvalRecord] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# S4 content engine surface (FR-3.1/3.4/3.5; ARCH §5.3; INV-2/INV-3/INV-8).
# --------------------------------------------------------------------------- #
class ContentGenerateRequest(BaseModel):
    """`POST /ai/content/generate` body — the operator prompt + target channel.

    ``channel`` defaults to ``instagram`` so a bare prompt still generates a batch
    for a concrete channel (the conditioning is channel-scoped, §8.3.2).
    """

    prompt: str
    channel: Channel = Channel.INSTAGRAM


class CandidateValidationView(BaseModel):
    """The minimal validation verdict the client renders — just the pass bit (FR-4.3).

    The full :class:`ValidationResult` is logged server-side (INV-4 audit); the
    client only needs ``passed`` to choose the keep-enabled vs blocked affordance.
    """

    passed: bool


class ContentCandidateResponse(BaseModel):
    """One candidate in a generated batch — surfaced OR blocked (FR-3.1; INV-3/INV-4).

    A FLAT projection the content workspace renders directly. ``surfaced`` is the
    fail-closed switch: a passing candidate (``surfaced=True``, ``passed=True``)
    renders with keep/discard controls; a BLOCKED candidate (``surfaced=False``)
    renders its ``failed_rules`` with NO keep affordance — the gate is shown,
    never softened (INV-4). Both carry a ``proposal_id`` (both are logged, INV-4
    audit side), but the keep endpoint still 409s on an un-passed eval (INV-3), so
    a blocked candidate is never keepable even if a client tried.
    """

    # §3 names the copy field `copy`; that wire name shadows `BaseModel.copy`, so
    # the attribute is `copy_text` and `copy` is kept as the alias (mirrors
    # ContentCandidate). FastAPI serializes the response by alias ⇒ the JSON key is
    # `copy`; `populate_by_name` lets the handler construct with `copy=`.
    model_config = ConfigDict(populate_by_name=True)

    proposal_id: UUID
    copy_text: str = Field(alias="copy")
    channel: str
    surfaced: bool
    degraded: bool = False
    failed_rules: list[str] = Field(default_factory=list)
    validation: CandidateValidationView


class ContentGenerateResponse(BaseModel):
    """The §5.3 batch outcome — surfaced + blocked candidates + the blocked count.

    ``candidates`` holds BOTH passing (``surfaced=True``) and blocked
    (``surfaced=False``) candidates so the operator can SEE the fail-closed gate
    at work (INV-4 visible); only passing ones are keepable. ``blocked_count`` is
    the number of gated-but-failing candidates (the audit count, == the count of
    ``surfaced=False`` entries). ``degraded`` is True when the kill switch / cost
    cap / no-key path forced the persistent-fallback set with no live call (INV-8).
    """

    batch_id: str = ""
    candidates: list[ContentCandidateResponse] = Field(default_factory=list)
    blocked_count: int = 0
    degraded: bool = False


class CampaignGenerateRequest(BaseModel):
    """`POST /ai/content/campaign` body — the four campaign axes + a count (Slice B).

    A campaign is defined by four axes: ``theme`` (the angle to lead with, e.g.
    ``gifted_identity`` / ``cost_tefa_esa`` / …), ``channel`` (shapes format/length),
    ``audience`` (the :class:`AudienceTag` driving tone + CTA), and an optional
    ``target_geo_prompt`` (an AI-search prompt the copy should be structured to win —
    SEO/GEO). ``count`` is the requested batch size; the endpoint CLAMPS it to a module
    cap so a batch is never silently unbounded (INV-8). ``channel`` / ``audience`` are
    closed enums (an out-of-range value is rejected 422). ``theme`` is a free string so
    the angle set can grow without a schema migration; it is embedded verbatim.
    """

    theme: str = Field(min_length=1)
    channel: Channel
    audience: AudienceTag
    target_geo_prompt: str | None = None
    count: int = Field(default=1, ge=1)


class CampaignEcho(BaseModel):
    """The campaign axes echoed back on the response so the client can show them as chips."""

    theme: str
    channel: Channel
    audience: AudienceTag
    target_geo_prompt: str | None = None


class CampaignGenerateResponse(ContentGenerateResponse):
    """The campaign batch outcome — the SAME flat batch as `/ai/content/generate` + echo.

    Extends :class:`ContentGenerateResponse` (so candidates stay FLAT and the existing
    BatchResult UI renders it unchanged) and adds the ``campaign`` echo of the axes that
    drove the batch.
    """

    campaign: CampaignEcho


class ContentDecisionRequest(BaseModel):
    """`POST /content/{proposal_id}/decision` body — the human keep/discard verdict.

    Only ``keep`` publishes (to the library + brand memory); ``discard``
    strengthens a dont signal and publishes nothing (FR-3.5; INV-2).
    """

    action: Decision


class ContentDecisionResponse(BaseModel):
    """The content-decision result — the SOLE content state-write path (INV-2; NFR-6).

    On ``keep`` a kept :class:`LibraryAsset` was promoted (``library_asset`` set)
    and brand memory affirmed; on ``discard`` there is no asset and ``published``
    is False.
    """

    proposal_id: UUID
    action: Decision
    published: bool = False
    library_asset: LibraryAsset | None = None


class SisFamilyStatus(BaseModel):
    """One reconcile outcome — the PII-firewall fields ONLY (M5; per-child A-24).

    This is the entire surface that crosses from the SIS roster into the cockpit:
    ``family_id``, ``student_id``, ``present``, ``confirmed_at``, ``bucket`` — never a
    child name/DOB/grade or any roster contact. ``student_id`` is an opaque
    owner-scoped uuid (the per-CHILD grain), NOT child PII; ``None`` = a
    household-grain verdict. The firewall is the shape itself (INV-1/INV-6; enforced
    by ``test_buckets_leak_no_roster_pii``).
    """

    family_id: UUID
    student_id: UUID | None = None
    present: bool
    confirmed_at: datetime | None
    bucket: SisBucket


class SisBucketGroup(BaseModel):
    """One reconcile bucket: its label, count, and member families (M5)."""

    bucket: SisBucket
    count: int
    families: list[SisFamilyStatus]


class SisBucketsResponse(BaseModel):
    """The admin SIS reconcile roll-up (`GET /enrollment/sis-buckets`, M5).

    Read-only (INV-2): the daily reconcile job's verdicts grouped by bucket in a
    fixed order. Strictly firewall fields — no roster/child PII (INV-1/INV-6).
    """

    buckets: list[SisBucketGroup]
    total: int

"""Pydantic response schemas for the read API (ARCHITECTURE.md §6).

All read-only (INV-2 for S0). These shape the GET responses for the landing
dashboard: per-stage pipeline counts (FR-2.1) and the basic joined Family Record
deal view (FR-2.2 — the full deal view lands in S1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.schemas.brand import LibraryAsset
from app.ai.schemas.close_tips import CloseTipsProposal
from app.ai.schemas.content import Channel, ContentCandidate, Decision
from app.ai.schemas.enrollment_draft import DraftAction, EnrollmentDraftProposal
from app.core.contact_status import ContactStatus
from app.core.eval_gate import ValidationResult
from app.core.family_record import DealView
from app.core.recovery_state import RecoveryState
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    LeadsNew,
    SeamStatus,
    Stage,
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
    """

    counts: dict[Stage, int]
    total: int
    seam: dict[SeamStatus, int]


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


class SurfacedCandidateResponse(BaseModel):
    """One surfaced (passing) candidate + its proposal_id + passing verdict (FR-3.1).

    The proposal body surfaces ONLY for a candidate whose eval PASSED (INV-3);
    blocked candidates never appear here (they are logged, not surfaced). The
    ``proposal_id`` lets the client call the keep/discard decision endpoint.
    """

    proposal_id: UUID
    candidate: ContentCandidate
    validation: ValidationResult


class ContentGenerateResponse(BaseModel):
    """The §5.3 batch outcome — surfaced candidates + the blocked count + degraded.

    ``candidates`` holds only PASSING candidates (INV-3/INV-4). ``blocked_count``
    is the number of gated-but-failing candidates that were WITHHELD yet logged
    (the audit count). ``degraded`` is True when the kill switch / cost cap forced
    the persistent-fallback path with no live call (INV-8).
    """

    candidates: list[SurfacedCandidateResponse] = Field(default_factory=list)
    blocked_count: int = 0
    degraded: bool = False


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

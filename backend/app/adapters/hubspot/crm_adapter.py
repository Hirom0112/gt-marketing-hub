"""The CRM adapter boundary — interface + simulated impl (ARCHITECTURE.md §7.1).

§7 (authoritative): "Every external boundary is an interface with two
implementations — Simulated and Production — selected at startup by config
(`adapters/registry.py`, NFR-8). v1 wires all to Simulated. Going live =
flipping config + supplying the production impl, with zero changes to `core/` or
`ai/`."

The §7.1 `CRMAdapter` interface has three operations:

- ``push_family(family_record) -> SyncResult`` — write-shaped; the sim **records**
  the push, never sends.
- ``read_mirror(family_id) -> MirrorState`` — feeds the §4.7 seam-status deriver,
  so it returns the *existing* :class:`app.core.seam.MirrorState` (not a second
  type) — the simulated mirror is rebuilt from what was recorded.
- ``send_message(message) -> SendResult`` — email/nudge; simulated in v1.

INV-9: the simulated impl is a pure in-memory recorder — **no network client at
all**. "Records, never sends" is therefore a structural property (an in-memory
log), provable without mocking sockets. This module imports nothing from
``anthropic``/``langgraph`` and keeps ``core/`` pure (it only *reads*
``core.seam.MirrorState`` and ``data.models``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from app.core.seam import MirrorState
from app.data.models import FamilyRecord, Stage, Student
from app.marketing.schemas.publish import (
    MirrorStatus,
    PlatformDispatch,
    PublishMonitor,
    PublishRequest,
)
from app.marketing.schemas.scheduling import DispatchStatus


class SyncResult(BaseModel):
    """Outcome of a ``push_family`` write (§7.1).

    Attributes:
        simulated: ``True`` whenever the simulated impl handled it — the v1 lock
            (INV-9). A production impl would return ``False``.
        recorded_id: The id under which the push was recorded (the simulated
            stand-in for a CRM object id).
        family_id: The pushed family's id (the §4.7 mirror key).
        stage: The funnel stage written into the mirror — what ``read_mirror``
            will later reflect.
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    family_id: UUID
    stage: Stage
    # The associated CRM contact id, when the push created/upserted one — the
    # live HubSpot contact id under CRM_MODE=live, so the cockpit can deep-link
    # the captured Contact alongside the Deal (S10 W3). None for the simulated
    # recorder (which has no contact object), and optional so adding it is
    # non-breaking for existing SyncResult construction.
    contact_id: str | None = None


class StudentSyncResult(BaseModel):
    """Outcome of a ``push_student`` write — one child to its own CRM object (A-24).

    The per-child analog of :class:`SyncResult`: one application per child maps to
    one per-child CRM object. ``simulated`` is the v1 lock (INV-9); ``object_id``
    is the live per-child HubSpot object id under ``CRM_MODE=live`` (None for the
    simulated recorder). ``stage`` is the child's own funnel stage written to CRM.
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    student_id: UUID
    family_id: UUID
    stage: Stage
    object_id: str | None = None


class SendResult(BaseModel):
    """Outcome of a ``send_message`` send (§7.1).

    Attributes:
        simulated: ``True`` when recorded-not-sent (the v1 lock, INV-9).
        recorded_id: The id under which the send was recorded.
        channel: The send channel (e.g. ``"email"``, ``"nudge"``).
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    channel: str


@dataclass(frozen=True, slots=True)
class EngagementSnapshot:
    """Aggregate email-engagement tier counts across a set of contacts (Module 6).

    The source behind the weekly scorecard's "engagement-tier mix (clicked)" KPI.
    Reported through the :class:`CRMAdapter` engagement seam so the same interface
    serves both impls: the simulated adapter synthesizes deterministic tiers from
    the seeded cohort (INV-9 — no I/O), the live HubSpot adapter would read the real
    email-engagement (click) tier. Aggregate only — a count of contacts in the top
    (clicked) tier over the total, never any per-person/behavioral field (INV-6).

    Attributes:
        total: How many contacts were read (the denominator).
        clicked: How many of them are in the top engagement (clicked) tier.
    """

    total: int
    clicked: int

    @property
    def clicked_share(self) -> float:
        """Share of read contacts in the clicked tier (0–1). No contacts ⇒ ``0.0``."""
        return self.clicked / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class EngagementTierMix:
    """Aggregate engagement-tier mix across contacts — clicked/opened/cold (Module 5).

    The fuller sibling of :class:`EngagementSnapshot` behind the Nurture 5a tier-mix
    widget: a count in EACH of the three engagement tiers (top *clicked*, middle
    *opened*, *cold*). Aggregate only — counts by tier, never a per-person/behavioral
    field (INV-6). The simulated adapter synthesizes deterministic tiers from the passed
    cohort (INV-9 — no I/O); the live HubSpot adapter reads the real ``gt_engagement_tier``
    property counts.

    Attributes:
        clicked: Contacts in the top (clicked) tier.
        opened: Contacts in the middle (opened) tier.
        cold: Contacts in the cold tier.
    """

    clicked: int
    opened: int
    cold: int

    @property
    def total(self) -> int:
        """Total contacts read across the three tiers (the denominator)."""
        return self.clicked + self.opened + self.cold


@dataclass(frozen=True, slots=True)
class LeadScoreBand:
    """One histogram band of the lead-score distribution (Module 7; counts only — INV-6).

    A half-open ``[low, high)`` band over the HubSpot ``gt_lead_score`` property with
    the aggregate count of contacts whose score falls in it. ``label`` is the
    human ``"low-high"`` band name. Never a per-person row (INV-6).
    """

    label: str
    low: int
    high: int
    count: int


@dataclass(frozen=True, slots=True)
class LeadScoreDistribution:
    """Aggregate lead-score histogram across contacts (Module 7; counts only — INV-6).

    The read behind the lead-scoring view's LIVE histogram: a count per ``[low, high)``
    band plus the total and a band-midpoint-weighted ``mean`` (an aggregate-only
    approximation — never a per-contact read, INV-6). The simulated adapter synthesizes
    a DETERMINISTIC score per contact from its family id (INV-9 — no I/O); the live
    HubSpot adapter reads the real ``gt_lead_score`` property counts, one CRM-Search
    COUNT per band.

    Attributes:
        bands: One :class:`LeadScoreBand` per ``[low, high)`` band (ascending).
        total: Total contacts read across the bands (the denominator).
        mean: The band-midpoint-weighted mean score (0.0 when no contacts).
    """

    bands: tuple[LeadScoreBand, ...]
    total: int
    mean: float


def lead_score_bands_from_counts(
    band_edges: Sequence[int], counts: Sequence[int]
) -> LeadScoreDistribution:
    """Assemble a :class:`LeadScoreDistribution` from band edges + per-band counts (pure).

    ``band_edges`` are the ascending histogram edges (n edges ⇒ n-1 ``[low, high)``
    bands); ``counts[i]`` is the contact count in band ``[band_edges[i], band_edges[i+1])``.
    The ``mean`` is the band-midpoint-weighted mean (an aggregate-only approximation —
    no per-contact score is ever read; INV-6). A shared pure helper so the simulated and
    live adapters build the identical shape.
    """
    edges = list(band_edges)
    bands: list[LeadScoreBand] = []
    weighted_total = 0.0
    total = 0
    for i in range(len(edges) - 1):
        low, high = edges[i], edges[i + 1]
        count = int(counts[i]) if i < len(counts) else 0
        bands.append(LeadScoreBand(label=f"{low}-{high}", low=low, high=high, count=count))
        weighted_total += ((low + high) / 2.0) * count
        total += count
    mean = weighted_total / total if total else 0.0
    return LeadScoreDistribution(bands=tuple(bands), total=total, mean=mean)


@dataclass(frozen=True, slots=True)
class PipelineStageCount:
    """One deal-stage's aggregate counts (Module 5; counts only — INV-6).

    Attributes:
        stage: The cockpit deal-stage label (interest/apply/enroll/tuition/closed_lost).
        count: Deals currently in this stage.
        stuck: Deals in this stage idle beyond the stuck-in-stage window (aggregate).
    """

    stage: str
    count: int
    stuck: int


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Aggregate pipeline-stage distribution + dated handoff counts (Module 5).

    The read behind the Nurture 5c pipeline view: per-stage counts (with a stuck count)
    plus the marketing→onboarding handoff counts in the weekly/monthly look-back windows.
    Aggregate only — counts by stage/window, never a per-deal row (INV-6). The simulated
    adapter synthesizes deterministic stages from the passed cohort (INV-9 — no I/O); the
    live HubSpot adapter reads the real Deal ``dealstage`` distribution.

    Attributes:
        stages: One :class:`PipelineStageCount` per stage (caller's stage order).
        handoff_week: Deals that entered a handoff stage within the weekly window.
        handoff_month: Deals that entered a handoff stage within the monthly window.
    """

    stages: tuple[PipelineStageCount, ...]
    handoff_week: int
    handoff_month: int


class CRMAdapter(ABC):
    """The CRM external boundary (§7.1).

    Two impls — Simulated (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. Core/AI depend only on this interface.
    """

    @abstractmethod
    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        """Push a family record to the CRM. Write-shaped (§7.1)."""

    @abstractmethod
    def push_student(self, student: Student) -> StudentSyncResult:
        """Push ONE child to its own per-child CRM object (A-24). Write-shaped.

        One application per child ⇒ one per-child object. The sim records the
        push (INV-9); a live impl upserts a per-child object behind the
        synthetic-write guard + INV-8 budget.
        """

    @abstractmethod
    def read_mirror(self, family_id: UUID) -> MirrorState:
        """Read the CRM mirror for one family, for §4.7 seam derivation."""

    @abstractmethod
    def search_modified_since(
        self, object_type: str, watermark_ms: int, until_ms: int | None = None
    ) -> list[tuple[UUID, MirrorState]]:
        """Pull every record modified strictly after ``watermark_ms`` (A2; §4.7).

        The CRM-as-truth incremental read: given an object type and a watermark
        (epoch-ms — the HubSpot ``hs_lastmodifieddate`` version stamp,
        RESEARCH_v2 §II.1), return one ``(family_id, MirrorState)`` per modified
        record, **ascending** by modified-at, so the poller can reconcile each and
        advance its watermark to the max seen. The :class:`MirrorState` carries
        ONLY the PII-free tracked scalars — same inbound firewall as
        :meth:`read_mirror` (guard 2, INV-1): NO contact name/phone/real email.

        ``until_ms`` is an OPTIONAL strict upper bound (epoch-ms): when supplied,
        only records modified strictly BEFORE it are returned. The poller passes
        one window-end per ``plan_sync_windows`` sub-window so each query is bounded
        on both sides and stays under HubSpot's 10k-result cap (A2). ``None`` (the
        default) leaves the read unbounded above — the original v1 contract.

        The live impl pages HubSpot CRM Search (``hs_lastmodifieddate GT`` plus an
        AND-ed ``hs_lastmodifieddate LT`` when bounded, a single ASC sort,
        ``paging.next.after``) through its budgeted call path (guard 3, INV-8); the
        simulated impl reconstructs the answer purely from its in-memory recorder
        (INV-9 — no network client).
        """

    @abstractmethod
    def send_message(self, message: dict[str, Any]) -> SendResult:
        """Send an outbound email/nudge. Simulated in v1 (INV-9)."""

    @abstractmethod
    def read_engagement(self, family_ids: Sequence[UUID]) -> EngagementSnapshot:
        """Read the email-engagement tier mix for the given contacts (Module 6).

        The read behind the weekly scorecard's "engagement-tier mix (clicked)" KPI:
        given the cohort's family ids, return an :class:`EngagementSnapshot` (total
        read + how many sit in the top *clicked* tier). Aggregate only — never a
        per-person/behavioral field (INV-6).

        The simulated impl synthesizes a DETERMINISTIC tier per contact from its
        family id (INV-9 — no network client, fully demoable offline). The live
        HubSpot impl reads the real email-engagement (click) tier via
        :meth:`read_engagement_mix` (S10 / Module 5).
        """

    @abstractmethod
    def read_engagement_mix(self, family_ids: Sequence[UUID]) -> EngagementTierMix:
        """Read the FULL engagement-tier mix (clicked/opened/cold) for a cohort (Module 5).

        The read behind the Nurture 5a tier-mix widget. Aggregate only — a count in each
        tier, never a per-person/behavioral field (INV-6). The simulated impl synthesizes
        a DETERMINISTIC tier per contact from its family id (INV-9 — no network); the live
        HubSpot impl reads the real ``gt_engagement_tier`` property counts (the live read
        IGNORES ``family_ids`` and aggregates portal-wide over synthetic contacts).
        """

    @abstractmethod
    def read_lead_score_distribution(
        self, family_ids: Sequence[UUID], *, band_edges: Sequence[int]
    ) -> LeadScoreDistribution:
        """Read the aggregate lead-score histogram over ``band_edges`` (Module 7; INV-6).

        The READ-ONLY read behind the lead-scoring view's LIVE histogram. ``band_edges``
        are the ascending histogram edges (from ``params.crm_ops.lead_score.bands``); the
        live impl issues one CRM-Search COUNT per ``[low, high)`` band over the HubSpot
        ``gt_lead_score`` property (``GTE low`` AND ``LT high``), reading ONLY the
        aggregate ``total`` — never a per-person row (INV-6) — and ignores ``family_ids``
        (portal-wide aggregate). Lead scoring is DISPLAY-only; nothing is ever written.
        The simulated impl synthesizes a DETERMINISTIC score per contact from its family
        id (INV-9 — no network), so the histogram is real and demoable offline.
        """

    @abstractmethod
    def read_pipeline_snapshot(
        self,
        family_ids: Sequence[UUID],
        *,
        stage_order: Sequence[str],
        handoff_stages: Sequence[str],
        now: datetime,
        stuck_days: int,
        week_days: int,
        month_days: int,
    ) -> PipelineSnapshot:
        """Read the aggregate deal-pipeline distribution + dated handoff counts (Module 5).

        The read behind the Nurture 5c pipeline view: per-stage deal counts (with a stuck
        count for deals idle beyond ``stuck_days``) over ``stage_order``, plus the
        marketing→onboarding handoff counts (deals reaching a ``handoff_stages`` stage)
        within the weekly/monthly look-back windows. Aggregate only — counts by
        stage/window, never a per-deal row (INV-6). The simulated impl synthesizes a
        DETERMINISTIC stage per contact from its family id (INV-9 — no network, ignores
        ``now``/the day windows); the live HubSpot impl reads the real Deal ``dealstage``
        distribution (it IGNORES ``family_ids`` and aggregates portal-wide).
        """

    @abstractmethod
    def read_last_modified(self, object_type: str) -> datetime | None:
        """Read the MAX ``hs_lastmodifieddate`` over one object type (aggregate; INV-6).

        The aggregate "last sync" watermark behind the CRM-Ops overview's per-connector
        last-sync row: the single most-recent modification instant across an object type
        (``contacts`` / ``deals``), or ``None`` when the object type has no records.

        Aggregate only — the live impl issues ONE CRM Search sorted by
        ``hs_lastmodifieddate`` DESCENDING with ``limit=1``, reading ONLY that one
        timestamp scalar (never a per-person row/identity field — the same INV-6 firewall
        as :meth:`read_engagement_mix` / :meth:`read_pipeline_snapshot`). The simulated
        impl reconstructs a DETERMINISTIC watermark from its in-memory recorder (INV-9 —
        no network), so the overview shows a real timestamp offline.
        """

    @abstractmethod
    def mirror_social_post(
        self, dispatch: PlatformDispatch, *, request: PublishRequest
    ) -> str | None:
        """Mirror one dispatched social post into HubSpot as a GT Social Post (W3).

        The cockpit is the primary observability plane; this writes the SECOND
        screen — one GT Social Post custom-object record per DISPATCHED post so
        the team can monitor publishing on the HubSpot screen too. Idempotent on
        the post id (``gt_synthetic_id = str(post_id)``; NEVER any contact
        identity, INV-1).

        Returns the HubSpot object id of the mirrored record, or ``None`` when
        there is nothing to mirror — a ``skipped`` mirror state, a blocked/failed
        dispatch, or a capped one (those carry no live publish). The simulated
        impl returns a deterministic synthetic id (no wall-clock/uuid4).
        """


def is_mirrorable(dispatch: PlatformDispatch) -> bool:
    """Pure predicate: does this dispatch warrant a GT Social Post mirror?

    Only a dispatch that actually published (``simulated_sent``) and is still
    eligible (``mirror_status == pending``) is mirrored. A blocked/failed/capped
    dispatch, or one already ``skipped``/``mirrored``, is NOT — the mirror is the
    second screen for posts that went out, never a record of a non-event.
    """
    return (
        dispatch.mirror_status is MirrorStatus.PENDING
        and dispatch.dispatch_status is DispatchStatus.SIMULATED_SENT
        and not dispatch.capped
    )


def apply_mirror_results(
    monitor: PublishMonitor, mirror_ids: dict[UUID, str | None]
) -> PublishMonitor:
    """Pure: fold per-dispatch mirror ids into an updated, immutable PublishMonitor.

    ``mirror_ids`` maps a dispatch's ``post_id`` to the HubSpot object id returned
    by :meth:`CRMAdapter.mirror_social_post` (or ``None`` when nothing was
    mirrored). For each dispatch with a non-``None`` id, the returned monitor
    flips that dispatch's ``mirror_status`` PENDING→MIRRORED; a ``None`` (or
    absent) entry leaves the dispatch untouched. ``hubspot_object_id`` is set to
    the FIRST mirrored id (the representative record for the request), preserving
    any id already present. Fully deterministic — no I/O, no wall clock.
    """
    updated: list[PlatformDispatch] = []
    first_mirrored: str | None = monitor.hubspot_object_id
    for dispatch in monitor.dispatches:
        obj_id = mirror_ids.get(dispatch.post_id)
        if obj_id is not None and dispatch.mirror_status is MirrorStatus.PENDING:
            updated.append(dispatch.model_copy(update={"mirror_status": MirrorStatus.MIRRORED}))
            if first_mirrored is None:
                first_mirrored = obj_id
        else:
            updated.append(dispatch)
    return monitor.model_copy(
        update={"dispatches": tuple(updated), "hubspot_object_id": first_mirrored}
    )


# Simulated engagement: a contact is placed in the top (clicked) tier when its
# family id hashes into 1-of-N buckets. N is a synthetic-SHAPING constant (INV-11) —
# not a tuned business threshold; it only sets the deterministic, demoable clicked
# share the offline seam reports (≈ 1/N of the cohort). The live tier comes from
# HubSpot, never this divisor.
_SIMULATED_CLICKED_TIER_DIVISOR = 3

# Synthetic lead-score shaping (INV-11 carve-out, like the divisor above — not a tuned
# business threshold). A contact's offline ``gt_lead_score`` is derived deterministically
# from its family id into ``[MIN, MIN + SPAN)`` so the offline histogram mirrors the
# verified portal range (10–99). The live score comes from HubSpot, never these.
_SIMULATED_LEAD_SCORE_MIN = 10
_SIMULATED_LEAD_SCORE_SPAN = 90


class SimulatedCRMAdapter(CRMAdapter):
    """In-memory recorder — records writes/sends, performs **no** I/O (INV-9).

    There is no network client here by construction, so "records, never sends"
    holds structurally: every ``push_family``/``send_message`` appends to an
    in-memory log and the call returns immediately with ``simulated=True``.
    ``read_mirror`` reconstructs a :class:`MirrorState` purely from what
    ``push_family`` recorded, so the same instance feeds the §4.7 deriver in a
    test/demo without any external HubSpot.
    """

    def __init__(self) -> None:
        # Append-only audit logs (the "recorder"). No network client.
        self.pushed_log: list[SyncResult] = []
        self.pushed_student_log: list[StudentSyncResult] = []  # A-24 per-child pushes.
        self.sent_log: list[SendResult] = []
        # GT Social Post mirrors recorded: (synthetic object id, post id).
        self.mirrored_log: list[tuple[str, UUID]] = []
        # The simulated HubSpot mirror, keyed by family — rebuilt from pushes. R1:
        # the stored value is the full multi-field :class:`MirrorState` (stage +
        # funding_state + owner + timestamp), so ``read_mirror`` feeds the §4.7
        # multi-field deriver the same shape the live portal would.
        self._mirror: dict[UUID, MirrorState] = {}

    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        """Record a family push and update the simulated mirror (never sends).

        R1: the mirror adopts every tracked field the §4.7 deriver compares —
        stage, ``funding_state``, and the owner id — so a freshly-pushed family
        reads ``synced`` across all fields, never a spurious divergence on the
        un-mirrored ones. M4 (A-30): the deal owner is now the assigned rep
        (``assigned_rep_id``), the DB-authoritative ownership the seam compares,
        not the RLS root ``user_id``.
        """
        result = SyncResult(
            simulated=True,
            recorded_id=uuid4().hex,
            family_id=family_record.family_id,
            stage=family_record.current_stage,
        )
        self.pushed_log.append(result)
        owner = (
            None if family_record.assigned_rep_id is None else str(family_record.assigned_rep_id)
        )
        self._mirror[family_record.family_id] = MirrorState(
            stage=family_record.current_stage,
            mirror_updated_at=family_record.updated_at,
            funding_state=family_record.funding_state,
            owner=owner,
        )
        return result

    def seed_mirror(self, family_id: UUID, mirror: MirrorState) -> None:
        """Seed the simulated mirror for one family directly (demo/test divergence).

        The push path always mirrors local exactly (⇒ ``synced``); this seam lets
        the demo and tests stage a DELIBERATE divergence — a mirror whose stage /
        ``funding_state`` / ``owner`` differs from the DB record — so the seam
        endpoint exercises ``push_local`` (a DB-newer drift) and ``flag_conflict``
        (a CRM-authoritative ``owner`` divergence, or no clear recency winner). No
        I/O — it simply writes the in-memory mirror entry (INV-9).
        """
        self._mirror[family_id] = mirror

    def push_student(self, student: Student) -> StudentSyncResult:
        """Record a per-child push (never sends) — the v1 simulated path (A-24)."""
        result = StudentSyncResult(
            simulated=True,
            recorded_id=uuid4().hex,
            student_id=student.student_id,
            family_id=student.family_id,
            stage=student.current_stage,
        )
        self.pushed_student_log.append(result)
        return result

    def read_mirror(self, family_id: UUID) -> MirrorState:
        """Return the simulated mirror for ``family_id`` as a core MirrorState.

        R1: the stored mirror is the full multi-field :class:`MirrorState`
        (stage + ``funding_state`` + ``owner`` + timestamp), returned as-is so the
        §4.7 deriver compares every tracked field. Nothing pushed/seeded yet ⇒ an
        empty mirror (all ``None``), which the deriver reads as "not pushed" (an
        ``unsynced`` concern, not divergence).
        """
        entry = self._mirror.get(family_id)
        if entry is None:
            return MirrorState(stage=None, mirror_updated_at=None)
        return entry

    def search_modified_since(
        self, object_type: str, watermark_ms: int, until_ms: int | None = None
    ) -> list[tuple[UUID, MirrorState]]:
        """Return recorded mirrors modified strictly after the watermark, ascending.

        Pure in-memory reconstruction from ``self._mirror`` (INV-9 — no network):
        the watermark (epoch-ms) is compared against each mirror's
        ``mirror_updated_at``; only entries strictly after it are returned, sorted
        ascending by that instant so the result matches the live impl's contract. A
        mirror with no ``mirror_updated_at`` (never pushed) is skipped. ``object_type``
        is accepted for interface parity; the recorder keys on family, not object type.

        ``until_ms`` (epoch-ms) is the OPTIONAL strict upper bound (A2): when given,
        only mirrors modified strictly BEFORE it are returned (strictly-after the
        watermark AND strictly-before until) — the in-memory twin of the live impl's
        AND-ed ``hs_lastmodifieddate LT`` filter. ``None`` leaves it unbounded above.
        """
        watermark = datetime.fromtimestamp(watermark_ms / 1000, tz=UTC)
        until = None if until_ms is None else datetime.fromtimestamp(until_ms / 1000, tz=UTC)
        matches: list[tuple[datetime, UUID, MirrorState]] = []
        for family_id, mirror in self._mirror.items():
            modified_at = mirror.mirror_updated_at
            if modified_at is None or modified_at <= watermark:
                continue
            if until is not None and modified_at >= until:
                continue
            matches.append((modified_at, family_id, mirror))
        matches.sort(key=lambda item: item[0])
        return [(family_id, mirror) for _, family_id, mirror in matches]

    def send_message(self, message: dict[str, Any]) -> SendResult:
        """Record an outbound send (email/nudge) and return it. No live send."""
        channel = str(message.get("channel", "email"))
        result = SendResult(simulated=True, recorded_id=uuid4().hex, channel=channel)
        self.sent_log.append(result)
        return result

    def read_engagement(self, family_ids: Sequence[UUID]) -> EngagementSnapshot:
        """Return a DETERMINISTIC synthetic engagement snapshot (INV-9 — no I/O).

        A contact is placed in the top *clicked* tier when its family id hashes into
        1-of-:data:`_SIMULATED_CLICKED_TIER_DIVISOR` buckets — a stable, repeatable
        synthesis over the passed cohort (the same ids always yield the same share),
        so the scorecard's engagement KPI is real and demoable offline. Aggregate
        only (a count, never a per-contact field; INV-6). No network client.
        """
        ids = list(family_ids)
        clicked = sum(1 for fid in ids if fid.int % _SIMULATED_CLICKED_TIER_DIVISOR == 0)
        return EngagementSnapshot(total=len(ids), clicked=clicked)

    def read_engagement_mix(self, family_ids: Sequence[UUID]) -> EngagementTierMix:
        """A DETERMINISTIC synthetic clicked/opened/cold mix over the cohort (INV-9).

        Each contact is bucketed into ONE of the three tiers by ``fid.int % 3`` (0 ⇒
        clicked, 1 ⇒ opened, 2 ⇒ cold) — a stable, repeatable synthesis over the passed
        cohort so the Nurture tier-mix widget is real and demoable offline. Aggregate
        only (counts, never a per-contact field; INV-6). No network client.
        """
        clicked = opened = cold = 0
        for fid in family_ids:
            bucket = fid.int % 3
            if bucket == 0:
                clicked += 1
            elif bucket == 1:
                opened += 1
            else:
                cold += 1
        return EngagementTierMix(clicked=clicked, opened=opened, cold=cold)

    def read_pipeline_snapshot(
        self,
        family_ids: Sequence[UUID],
        *,
        stage_order: Sequence[str],
        handoff_stages: Sequence[str],
        now: datetime,
        stuck_days: int,
        week_days: int,
        month_days: int,
    ) -> PipelineSnapshot:
        """A DETERMINISTIC synthetic pipeline distribution over the cohort (INV-9).

        Each contact is assigned ONE stage by ``fid.int % len(stage_order)``; it is STUCK
        when ``fid.int % 4 == 0``; a deal in a handoff stage counts toward the monthly
        handoff when ``fid.int % 3 == 0`` and toward the weekly handoff when
        ``fid.int % 6 == 0`` (a strict subset, so weekly <= monthly). Fully deterministic
        over the passed cohort — the injected ``now``/day windows are unused by the offline
        synthesis (they drive the live read). Aggregate only (counts; INV-6).
        """
        order = list(stage_order)
        handoff = set(handoff_stages)
        counts = {s: 0 for s in order}
        stuck = {s: 0 for s in order}
        handoff_week = 0
        handoff_month = 0
        if order:
            for fid in family_ids:
                stage = order[fid.int % len(order)]
                counts[stage] += 1
                if fid.int % 4 == 0:
                    stuck[stage] += 1
                if stage in handoff and fid.int % 3 == 0:
                    handoff_month += 1
                    if fid.int % 6 == 0:
                        handoff_week += 1
        stages = tuple(PipelineStageCount(stage=s, count=counts[s], stuck=stuck[s]) for s in order)
        return PipelineSnapshot(
            stages=stages, handoff_week=handoff_week, handoff_month=handoff_month
        )

    def read_lead_score_distribution(
        self, family_ids: Sequence[UUID], *, band_edges: Sequence[int]
    ) -> LeadScoreDistribution:
        """A DETERMINISTIC synthetic lead-score histogram over the cohort (INV-9 — no I/O).

        Each contact's synthetic ``gt_lead_score`` is derived from its family id into
        the verified portal range (:data:`_SIMULATED_LEAD_SCORE_MIN` …) and bucketed into
        the ``[low, high)`` band it falls in. A score at/above the top edge folds into the
        last band so nothing is dropped. Aggregate only (counts; INV-6); the same cohort
        always yields the same histogram (real + demoable offline).
        """
        edges = list(band_edges)
        counts = [0] * max(len(edges) - 1, 0)
        for fid in family_ids:
            score = _SIMULATED_LEAD_SCORE_MIN + (fid.int % _SIMULATED_LEAD_SCORE_SPAN)
            for i in range(len(edges) - 1):
                # Last band is inclusive at the top edge so a top-of-range score is kept.
                top_inclusive = i == len(edges) - 2
                if edges[i] <= score < edges[i + 1] or (top_inclusive and score >= edges[i + 1]):
                    counts[i] += 1
                    break
        return lead_score_bands_from_counts(edges, counts)

    def read_last_modified(self, object_type: str) -> datetime | None:
        """The MAX recorded ``mirror_updated_at`` across the in-memory mirror (INV-9).

        A DETERMINISTIC offline watermark: the most-recent ``mirror_updated_at`` over
        every recorded/seeded mirror entry, or ``None`` when nothing has been
        pushed/seeded yet. The recorder keys by family (not by object type), so
        ``object_type`` is accepted for interface parity and the same watermark answers
        for ``contacts`` and ``deals``. No network client (INV-9); aggregate only (one
        timestamp, never a per-person row; INV-6).
        """
        stamps = [
            mirror.mirror_updated_at
            for mirror in self._mirror.values()
            if mirror.mirror_updated_at is not None
        ]
        return max(stamps) if stamps else None

    def mirror_social_post(
        self, dispatch: PlatformDispatch, *, request: PublishRequest
    ) -> str | None:
        """Record a GT Social Post mirror and return a DETERMINISTIC synthetic id.

        Records-never-sends (INV-9): appends to ``mirrored_log`` and returns an id
        derived purely from the post id — no wall clock, no ``uuid4`` — so the same
        dispatch always yields the same id (re-running the fan-out is idempotent).
        A non-mirrorable dispatch (skipped/blocked/failed/capped) returns ``None``
        and records nothing, matching the live impl's contract.
        """
        if not is_mirrorable(dispatch):
            return None
        object_id = f"sim-gtsp-{dispatch.post_id}"
        self.mirrored_log.append((object_id, dispatch.post_id))
        return object_id

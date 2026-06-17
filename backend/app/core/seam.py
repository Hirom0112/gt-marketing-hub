"""S0 seam-status deriver (ARCHITECTURE.md §4.7; CLAUDE.md §3, §4.1).

The Supabase↔HubSpot seam is **not** a second store — it is the derived column
`family_record.crm_seam_status`. This module owns its derivation (§4.7):

- ``synced``   — ``crm_synced_at >= updated_at`` (CRM reflects latest local state).
- ``unsynced`` — ``crm_synced_at`` is null or ``< updated_at`` (local changes unpushed).
- ``conflict`` — the simulated HubSpot mirror holds a *tracked-field* value that
  diverges from local, with **neither side clearly newer**.

The mirror is the simulated HubSpot side (§7.1, OUT-3): in v1 its writes are
recorded, never sent, so its current state is passed in as ``MirrorState`` rather
than fetched. This keeps the deriver pure (CLAUDE.md §3): no I/O, no LLM, no
adapter imports — it consumes only the model + a plain value object and returns a
``SeamStatus``. The reconcile *flow* (FR-2.6, S3) and the API live elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage


@dataclass(frozen=True, slots=True)
class MirrorState:
    """The simulated HubSpot mirror's view of a family's tracked fields (§4.7).

    Only fields actually mirrored into HubSpot live here. v1 tracked just
    ``stage`` (the §4.7 worked example); R1 generalizes the mirror to additional
    tracked fields with per-field authority (see :data:`_TRACKED_FIELDS`):

    - ``funding_state`` — DB-authoritative (derived by the funding gate, §5.4):
      a divergence is a pending push, never a conflict (DB always wins).
    - ``owner`` — CRM-authoritative (the HubSpot deal owner, human-edited): a
      divergence is a genuine conflict the gate flags, never silently overwrites.

    The new fields are optional with ``None`` defaults so every existing
    constructor stays backward-compatible: a ``None`` mirror field means "not
    tracked / not pushed" and is skipped by divergence detection.
    ``mirror_updated_at`` is when the mirror last changed, used to judge
    "neither side clearly newer" for DB-authoritative conflict detection.

    Attributes:
        stage: The funnel stage HubSpot currently holds, or ``None`` if the
            mirror has no record yet (nothing pushed).
        mirror_updated_at: When the mirror last changed, or ``None`` if never.
        funding_state: The funding-gate state HubSpot holds, or ``None`` if not
            tracked/pushed (DB-authoritative).
        owner: The HubSpot deal owner identifier (human-edited), or ``None`` if
            not tracked/pushed (CRM-authoritative).
    """

    stage: Stage | None
    mirror_updated_at: datetime | None
    funding_state: FundingState | None = None
    owner: str | None = None


class _FieldAuthority(StrEnum):
    """Which side owns a tracked field when it diverges (R1; M4; §4.7).

    - ``db`` — the local DB is the source of truth (derived fields like ``stage``
      / ``funding_state``): a divergence is a pending push (``push_local``), and
      is only a *conflict* when neither side is clearly newer (the §4.7 rule).
    - ``crm`` — HubSpot is the source of truth (human-edited fields): ANY
      divergence is a genuine conflict, regardless of timestamps — never silently
      overwritten (INV-4-style fail-closed). No field uses this after the M4 flip;
      retained as the authority vocabulary for a future human-edited field.
    - ``db_guarded`` — DB-authoritative WITH a post-``assigned_at`` guard (the M4
      ``owner`` flip, A-30): the DB owns assignment, so a diverging mirror owner
      is normally a plain pending push (DB wins) — UNLESS the mirror's owner
      changed AFTER our last ``assigned_at`` (a post-assignment HubSpot edit), in
      which case the seam flags a CONFLICT rather than stomping the human edit.
    """

    DB = "db"
    CRM = "crm"
    DB_GUARDED = "db_guarded"


class _TrackedField(NamedTuple):
    """One reconciled field: its local accessor, mirror accessor, and authority."""

    name: str
    authority: _FieldAuthority
    local_value: Callable[[FamilyRecord], object]
    mirror_value: Callable[[MirrorState], object]


# The set of tracked fields and their per-field authority (R1; M4; §4.7). This is
# a structural policy — like the SeamStatus enum it defines *what* the seam
# reconciles — so it lives in code: INV-11 governs numeric tunables, not
# structural field definitions. The M4 owner-authority flip (A-30, USER-RATIFIED
# 2026-06-17) makes ``owner`` DB-authoritative driven by ``assigned_rep_id`` (the
# DB owns assignment now), with a post-``assigned_at`` guard (``db_guarded``):
# local ``owner`` is the assigned rep id, compared as a string against the
# mirror's HubSpot owner id.
_TRACKED_FIELDS: tuple[_TrackedField, ...] = (
    _TrackedField(
        name="stage",
        authority=_FieldAuthority.DB,
        local_value=lambda r: r.current_stage,
        mirror_value=lambda m: m.stage,
    ),
    _TrackedField(
        name="funding_state",
        authority=_FieldAuthority.DB,
        local_value=lambda r: r.funding_state,
        mirror_value=lambda m: m.funding_state,
    ),
    _TrackedField(
        name="owner",
        authority=_FieldAuthority.DB_GUARDED,
        local_value=lambda r: None if r.assigned_rep_id is None else str(r.assigned_rep_id),
        mirror_value=lambda m: m.owner,
    ),
)


def _diverging_fields(record: FamilyRecord, mirror: MirrorState) -> list[_TrackedField]:
    """The tracked fields whose mirror value diverges from local.

    Only compares when the mirror actually holds a value: a ``None`` mirror
    field means "not pushed/tracked", which is an ``unsynced`` concern, not
    divergence.
    """
    diverging: list[_TrackedField] = []
    for field in _TRACKED_FIELDS:
        mirror_val = field.mirror_value(mirror)
        if mirror_val is not None and mirror_val != field.local_value(record):
            diverging.append(field)
    return diverging


def _is_conflict(record: FamilyRecord, mirror: MirrorState) -> bool:
    """True iff any diverging tracked field is a genuine conflict (§4.7, R1, M4).

    Per-field authority decides:

    - a CRM-authoritative field that diverges is ALWAYS a conflict — HubSpot owns
      it, so we never silently overwrite it (no field uses this after the M4 flip);
    - a ``db_guarded`` field (``owner``, M4) that diverges is a conflict only when
      the mirror changed AFTER our last ``assigned_at`` (a post-assignment HubSpot
      edit — don't stomp it); otherwise the DB wins and it is a plain pending push;
    - a DB-authoritative field (``stage`` / ``funding_state``) that diverges is a
      conflict only when neither side is clearly newer (the original §4.7 rule);
      otherwise it is a plain pending push.
    """
    diverging = _diverging_fields(record, mirror)
    if not diverging:
        return False
    if any(field.authority is _FieldAuthority.CRM for field in diverging):
        return True
    if any(
        field.authority is _FieldAuthority.DB_GUARDED
        and _mirror_changed_after_assignment(record, mirror)
        for field in diverging
    ):
        return True
    # Any remaining divergence is DB-authoritative (plain DB or a db_guarded field
    # whose guard did NOT fire): a conflict only when recency is unclear (§4.7).
    # A db_guarded divergence that passed the guard is DB-wins → never a conflict
    # here, so restrict the recency rule to genuinely DB-authoritative fields.
    db_authoritative = [f for f in diverging if f.authority is _FieldAuthority.DB]
    if not db_authoritative:
        return False
    return _neither_side_clearly_newer(record, mirror)


def _mirror_changed_after_assignment(record: FamilyRecord, mirror: MirrorState) -> bool:
    """True iff the mirror changed strictly AFTER our last assignment (M4 guard).

    The M4 ``owner`` guard (A-30): the DB owns assignment (``assigned_rep_id`` /
    ``assigned_at``), so a diverging mirror owner is normally a plain pending push
    (DB wins). It becomes a CONFLICT only when the mirror's last change
    (``mirror_updated_at``) is strictly later than our ``assigned_at`` — i.e.
    someone edited the HubSpot owner AFTER we assigned, which we must not stomp.

    Fail-closed on missing anchors: if we never recorded an ``assigned_at`` (the
    DB has no assignment instant to defend) any mirror change is treated as
    post-assignment ⇒ guard fires. A mirror with no ``mirror_updated_at`` cannot
    be shown to post-date the assignment ⇒ guard does not fire (DB wins, push).
    """
    mirror_at = mirror.mirror_updated_at
    if mirror_at is None:
        return False
    assigned_at = record.assigned_at
    if assigned_at is None:
        return True
    return mirror_at > assigned_at


def _mirror_diverges(record: FamilyRecord, mirror: MirrorState) -> bool:
    """True iff the mirror holds any tracked-field value differing from local."""
    return bool(_diverging_fields(record, mirror))


def _neither_side_clearly_newer(record: FamilyRecord, mirror: MirrorState) -> bool:
    """True when timestamps don't establish which side's value is the latest.

    A clear winner exists only when both sides carry an instant and one strictly
    precedes the other. Equal instants, or any missing instant, leave recency
    ambiguous — so a divergence there is a genuine ``conflict`` rather than a
    plain push (§4.7).
    """
    local_at = record.updated_at
    mirror_at = mirror.mirror_updated_at
    if local_at is None or mirror_at is None:
        return True
    return local_at == mirror_at


def derive_seam_status(record: FamilyRecord, mirror: MirrorState) -> SeamStatus:
    """Derive ``crm_seam_status`` for one family record (§4.7).

    Row-level status aggregates the per-field results (R1): any field in
    conflict ⇒ ``conflict``; else any unsynced signal ⇒ ``unsynced``; else
    ``synced``. Rules, in order:

    1. ``conflict`` — some tracked field is a genuine conflict per its authority
       (:func:`_is_conflict`): a CRM-authoritative field (``owner``) diverges, or
       a DB-authoritative field (``stage`` / ``funding_state``) diverges with
       neither side clearly newer. DB divergence with a clear winner is just a
       pending push, not a conflict.
    2. ``synced`` — ``crm_synced_at >= updated_at``: the CRM reflects the latest
       local state (inclusive boundary: an equal instant is synced).
    3. ``unsynced`` — otherwise: ``crm_synced_at`` is null or strictly precedes
       ``updated_at``, so local changes have not been pushed.

    Args:
        record: The family record whose ``updated_at`` / ``crm_synced_at`` /
            tracked fields are compared against the mirror.
        mirror: The simulated HubSpot mirror's view of this family.

    Returns:
        The derived :class:`SeamStatus`.
    """
    if _is_conflict(record, mirror):
        return SeamStatus.CONFLICT

    synced_at = record.crm_synced_at
    if synced_at is not None and record.updated_at is not None and synced_at >= record.updated_at:
        return SeamStatus.SYNCED

    return SeamStatus.UNSYNCED


# ---------------------------------------------------------------------------
# Reconcile flow (FR-2.6; ARCHITECTURE.md §4.7) — the S3 flow the deriver
# docstring defers to. Deterministic, human-gated, simulated v1. Like the
# deriver this is PURE: no I/O, no LLM, no adapters. ``propose_reconcile``
# computes a resolution *proposal* for a non-synced family; ``apply_reconcile``
# computes the post-reconcile state for an APPROVED proposal. Per A-7 (and
# A-3's read-only store) ``apply_reconcile`` does NOT persist — it returns the
# new record + mirror so the caller can re-derive the seam and surface it,
# exactly as the deal view already derives-on-read.
# ---------------------------------------------------------------------------


class ReconcileDirection(StrEnum):
    """How a non-synced family's seam should be reconciled (FR-2.6).

    - ``push_local`` — local is the source of truth (local newer / unpushed);
      mirror the local tracked field and mark the seam synced.
    - ``accept_mirror`` — the mirror is the source of truth; adopt its value.
      Reserved for the human-chosen resolution of a flagged conflict; v1 does
      not auto-pick it.
    - ``flag_conflict`` — a true conflict (mirror diverges, neither side clearly
      newer): needs a human choice. The gate must NOT silently resolve it
      (INV-4-style fail-closed).
    """

    PUSH_LOCAL = "push_local"
    ACCEPT_MIRROR = "accept_mirror"
    FLAG_CONFLICT = "flag_conflict"


class ReconcileProposal(BaseModel):
    """A proposed seam resolution for one non-synced family (FR-2.6).

    Frozen: a proposal is an immutable artifact handed to the human-approval
    path (S3 wave 2), never mutated in place. It carries the tracked-field
    values involved so the caller can render the diff and so ``apply_reconcile``
    needs no second mirror fetch.

    R1 adds the extra tracked fields (``funding_state`` / ``owner``) as optional
    with ``None`` defaults so every existing constructor and consumer stays
    backward-compatible. ``apply_reconcile`` uses them to faithfully reconstruct
    the conflicting mirror (so a multi-field conflict re-derives unchanged) and
    to mirror all DB-authoritative local values on a ``push_local``.

    Attributes:
        family_id: The family this proposal resolves.
        direction: The proposed resolution (see :class:`ReconcileDirection`).
        local_stage: The local tracked-field value (``current_stage``).
        mirror_stage: The mirror's tracked-field value, or ``None`` if unpushed.
        local_funding_state: The local ``funding_state`` (DB-authoritative).
        mirror_funding_state: The mirror's ``funding_state``, or ``None``.
        local_owner: The local owner id — the assigned rep (``assigned_rep_id``,
            DB-authoritative after the M4 flip, A-30), or ``None``.
        mirror_owner: The mirror's HubSpot owner, or ``None``.
        summary: A human-readable one-line description of the proposal.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    family_id: UUID
    direction: ReconcileDirection
    local_stage: Stage
    mirror_stage: Stage | None
    local_funding_state: FundingState | None = None
    mirror_funding_state: FundingState | None = None
    local_owner: str | None = None
    mirror_owner: str | None = None
    summary: str


class ReconcileResult(BaseModel):
    """The computed post-reconcile state of an APPROVED proposal (FR-2.6).

    Pure/derived per A-7: this is the state the caller would persist *if* the
    store were write-capable; in v1 it is returned, not written. ``record`` and
    ``mirror`` re-derive (via :func:`derive_seam_status`) to ``seam_status``.

    Attributes:
        applied: Whether the reconcile changed the seam. ``False`` for a flagged
            conflict (fail-closed) or a no-op.
        seam_status: The seam status after this reconcile.
        record: The post-reconcile family record (``crm_synced_at`` advanced on
            a ``push_local``); unchanged when not applied.
        mirror: The post-reconcile mirror (local value mirrored on a
            ``push_local``); unchanged when not applied.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    applied: bool
    seam_status: SeamStatus
    record: FamilyRecord
    mirror: MirrorState


def propose_reconcile(record: FamilyRecord, mirror: MirrorState) -> ReconcileProposal | None:
    """Compute a reconcile proposal for one family, or ``None`` if synced (FR-2.6).

    Maps the derived :class:`SeamStatus` to a proposed resolution:

    - ``synced``   — nothing to do; returns ``None`` (no-op).
    - ``unsynced`` — local changes are unpushed; propose ``push_local``.
    - ``conflict`` — mirror diverges with no clear winner; propose
      ``flag_conflict``. A true conflict is **never** auto-resolved here: the
      gate flags it for a human choice rather than silently picking a side
      (INV-4-style fail-closed).

    Args:
        record: The family record being reconciled.
        mirror: The simulated HubSpot mirror's view of this family.

    Returns:
        A :class:`ReconcileProposal`, or ``None`` when already ``synced``.
    """
    status = derive_seam_status(record, mirror)
    if status is SeamStatus.SYNCED:
        return None

    if status is SeamStatus.CONFLICT:
        direction = ReconcileDirection.FLAG_CONFLICT
        diverging = ", ".join(field.name for field in _diverging_fields(record, mirror))
        summary = (
            f"Conflict on {diverging}: local and CRM disagree and the change is "
            "not a clear local-newer push — needs a human choice."
        )
    else:  # SeamStatus.UNSYNCED
        direction = ReconcileDirection.PUSH_LOCAL
        summary = f"Unsynced: push local stage '{record.current_stage}' to the CRM."

    local_owner = None if record.assigned_rep_id is None else str(record.assigned_rep_id)
    return ReconcileProposal(
        family_id=record.family_id,
        direction=direction,
        local_stage=record.current_stage,
        mirror_stage=mirror.stage,
        local_funding_state=record.funding_state,
        mirror_funding_state=mirror.funding_state,
        local_owner=local_owner,
        mirror_owner=mirror.owner,
        summary=summary,
    )


def apply_reconcile(record: FamilyRecord, proposal: ReconcileProposal) -> ReconcileResult:
    """Compute the post-reconcile state for an APPROVED proposal (FR-2.6).

    Deterministic and pure (A-7): returns the new record + mirror, never
    persists. Only ``push_local`` (or a human-chosen ``accept_mirror``) is
    applied; a ``flag_conflict`` proposal is **not** resolved here — it fails
    closed, leaving the seam in ``conflict`` until a human supplies a chosen
    direction (INV-4-style: flag, don't soften).

    For ``push_local``: the mirror adopts every DB-authoritative local tracked
    field (``stage`` + ``funding_state``) and carries the local owner, and
    ``crm_synced_at`` advances to ``updated_at``, so :func:`derive_seam_status`
    on the returned pair yields ``synced``. (A ``push_local`` is only ever
    proposed when there is no CRM-authoritative conflict, so mirroring the local
    owner cannot clobber a human edit.)

    Args:
        record: The family record the proposal was computed against.
        proposal: The human-approved reconcile proposal.

    Returns:
        A :class:`ReconcileResult` carrying the post-reconcile state and the
        re-derived :class:`SeamStatus`.
    """
    local_owner = None if record.assigned_rep_id is None else str(record.assigned_rep_id)

    if proposal.direction is ReconcileDirection.PUSH_LOCAL:
        new_record = record.model_copy(update={"crm_synced_at": record.updated_at})
        new_mirror = MirrorState(
            stage=record.current_stage,
            funding_state=record.funding_state,
            owner=local_owner,
            mirror_updated_at=record.updated_at,
        )
        return ReconcileResult(
            applied=True,
            seam_status=derive_seam_status(new_record, new_mirror),
            record=new_record,
            mirror=new_mirror,
        )

    if proposal.direction is ReconcileDirection.ACCEPT_MIRROR:
        # Human-chosen: adopt the mirror's tracked fields as the new local truth,
        # then mark synced. (Reserved path; v1 propose never auto-selects it.)
        adopted_stage = (
            proposal.mirror_stage if proposal.mirror_stage is not None else (record.current_stage)
        )
        adopted_funding = (
            proposal.mirror_funding_state
            if proposal.mirror_funding_state is not None
            else record.funding_state
        )
        new_record = record.model_copy(
            update={
                "current_stage": adopted_stage,
                "funding_state": adopted_funding,
                "crm_synced_at": record.updated_at,
            }
        )
        new_mirror = MirrorState(
            stage=adopted_stage,
            funding_state=adopted_funding,
            owner=proposal.mirror_owner if proposal.mirror_owner is not None else local_owner,
            mirror_updated_at=record.updated_at,
        )
        return ReconcileResult(
            applied=True,
            seam_status=derive_seam_status(new_record, new_mirror),
            record=new_record,
            mirror=new_mirror,
        )

    # FLAG_CONFLICT — fail closed: do not resolve. Reconstruct the conflicting
    # mirror from the proposal (all tracked fields) so the caller re-derives the
    # unchanged conflict status — for whichever field(s) actually diverged.
    flagged_mirror = MirrorState(
        stage=proposal.mirror_stage,
        funding_state=proposal.mirror_funding_state,
        owner=proposal.mirror_owner,
        mirror_updated_at=record.updated_at,
    )
    return ReconcileResult(
        applied=False,
        seam_status=derive_seam_status(record, flagged_mirror),
        record=record,
        mirror=flagged_mirror,
    )

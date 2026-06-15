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

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.data.models import FamilyRecord, SeamStatus, Stage


@dataclass(frozen=True, slots=True)
class MirrorState:
    """The simulated HubSpot mirror's view of a family's tracked fields (§4.7).

    Only fields actually mirrored into HubSpot live here. v1 tracks ``stage``
    (the §4.7 worked example); ``mirror_updated_at`` is when the mirror last
    changed, used to judge "neither side clearly newer" for conflict detection.

    Attributes:
        stage: The funnel stage HubSpot currently holds, or ``None`` if the
            mirror has no record yet (nothing pushed).
        mirror_updated_at: When the mirror last changed, or ``None`` if never.
    """

    stage: Stage | None
    mirror_updated_at: datetime | None


def _mirror_diverges(record: FamilyRecord, mirror: MirrorState) -> bool:
    """True iff the mirror holds a tracked-field value differing from local.

    Only compares when the mirror actually holds a value: a ``None`` mirror
    field means "not pushed", which is an ``unsynced`` concern, not divergence.
    """
    return mirror.stage is not None and mirror.stage != record.current_stage


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

    Rules, in order:

    1. ``conflict`` — the mirror holds a tracked-field value that diverges from
       local and neither side is clearly newer. Divergence with a clear winner
       is just a pending push, not a conflict.
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
    if _mirror_diverges(record, mirror) and _neither_side_clearly_newer(record, mirror):
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

    Attributes:
        family_id: The family this proposal resolves.
        direction: The proposed resolution (see :class:`ReconcileDirection`).
        local_stage: The local tracked-field value (``current_stage``).
        mirror_stage: The mirror's tracked-field value, or ``None`` if unpushed.
        summary: A human-readable one-line description of the proposal.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    family_id: UUID
    direction: ReconcileDirection
    local_stage: Stage
    mirror_stage: Stage | None
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
        summary = (
            f"Conflict: local stage '{record.current_stage}' vs CRM "
            f"'{mirror.stage}' with neither side clearly newer — needs a human choice."
        )
    else:  # SeamStatus.UNSYNCED
        direction = ReconcileDirection.PUSH_LOCAL
        summary = f"Unsynced: push local stage '{record.current_stage}' to the CRM."

    return ReconcileProposal(
        family_id=record.family_id,
        direction=direction,
        local_stage=record.current_stage,
        mirror_stage=mirror.stage,
        summary=summary,
    )


def apply_reconcile(record: FamilyRecord, proposal: ReconcileProposal) -> ReconcileResult:
    """Compute the post-reconcile state for an APPROVED proposal (FR-2.6).

    Deterministic and pure (A-7): returns the new record + mirror, never
    persists. Only ``push_local`` (or a human-chosen ``accept_mirror``) is
    applied; a ``flag_conflict`` proposal is **not** resolved here — it fails
    closed, leaving the seam in ``conflict`` until a human supplies a chosen
    direction (INV-4-style: flag, don't soften).

    For ``push_local``: the mirror adopts the local tracked field and
    ``crm_synced_at`` advances to ``updated_at``, so :func:`derive_seam_status`
    on the returned pair yields ``synced``.

    Args:
        record: The family record the proposal was computed against.
        proposal: The human-approved reconcile proposal.

    Returns:
        A :class:`ReconcileResult` carrying the post-reconcile state and the
        re-derived :class:`SeamStatus`.
    """
    if proposal.direction is ReconcileDirection.PUSH_LOCAL:
        new_record = record.model_copy(update={"crm_synced_at": record.updated_at})
        new_mirror = MirrorState(
            stage=record.current_stage,
            mirror_updated_at=record.updated_at,
        )
        return ReconcileResult(
            applied=True,
            seam_status=derive_seam_status(new_record, new_mirror),
            record=new_record,
            mirror=new_mirror,
        )

    if proposal.direction is ReconcileDirection.ACCEPT_MIRROR:
        # Human-chosen: adopt the mirror's tracked field as the new local truth,
        # then mark synced. (Reserved path; v1 propose never auto-selects it.)
        adopted_stage = (
            proposal.mirror_stage if proposal.mirror_stage is not None else (record.current_stage)
        )
        new_record = record.model_copy(
            update={"current_stage": adopted_stage, "crm_synced_at": record.updated_at}
        )
        new_mirror = MirrorState(stage=adopted_stage, mirror_updated_at=record.updated_at)
        return ReconcileResult(
            applied=True,
            seam_status=derive_seam_status(new_record, new_mirror),
            record=new_record,
            mirror=new_mirror,
        )

    # FLAG_CONFLICT — fail closed: do not resolve. Reconstruct the conflicting
    # mirror from the proposal so the caller re-derives the unchanged status.
    flagged_mirror = MirrorState(stage=proposal.mirror_stage, mirror_updated_at=record.updated_at)
    return ReconcileResult(
        applied=False,
        seam_status=derive_seam_status(record, flagged_mirror),
        record=record,
        mirror=flagged_mirror,
    )

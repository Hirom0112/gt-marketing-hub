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
    ``stage`` (the §4.7 worked example); the mirror now carries additional tracked
    fields with per-field authority (see :data:`_TRACKED_FIELDS`):

    - ``funding_state`` — DB-authoritative (the GT funding signal, §5.4; INV-10):
      a divergence is a pending push, never accepted from the CRM.
    - ``owner`` — CRM-as-source-of-truth (the HubSpot deal owner, human-edited),
      reconciled by last-write-wins (A2): the strictly-newer side wins.

    The new fields are optional with ``None`` defaults so every existing
    constructor stays backward-compatible: a ``None`` mirror field means "not
    tracked / not pushed" and is skipped by divergence detection.
    ``mirror_updated_at`` is the mirror's HubSpot ``hs_lastmodifieddate`` — when
    the mirror last changed — used for the last-write-wins recency comparison
    against the local ``updated_at`` (A2).

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
    """Which side owns a tracked field when it diverges (A2; §4.7; RESEARCH_v2 §II.1).

    - ``db`` — the local DB / GT is the source of truth (``funding_state``, the
      INV-10 funding signal): a divergence is a pending push (``push_local``) and
      is NEVER accepted from the CRM; it is only a *conflict* when neither side is
      clearly newer (the §4.7 ambiguous-recency rule).
    - ``crm`` — HubSpot is the source of truth for the human/pipeline-edited
      fields (``stage``, ``owner``), reconciled by **last-write-wins** (A2): the
      mirror's ``mirror_updated_at`` (the HubSpot ``hs_lastmodifieddate``) is
      compared against the local ``updated_at``. The strictly-newer side wins —
      mirror newer ⇒ accept the CRM value (``accept_mirror``), local newer ⇒
      ``push_local``. Equal/missing instants leave recency ambiguous ⇒
      ``flag_conflict`` (INV-4 fail-closed — never silently picked).
    """

    DB = "db"
    CRM = "crm"


class _TrackedField(NamedTuple):
    """One reconciled field: its local accessor, mirror accessor, and authority."""

    name: str
    authority: _FieldAuthority
    local_value: Callable[[FamilyRecord], object]
    mirror_value: Callable[[MirrorState], object]


# The set of tracked fields and their per-field authority (A2; §4.7). This is a
# structural policy — like the SeamStatus enum it defines *what* the seam
# reconciles — so it lives in code: INV-11 governs numeric tunables, not
# structural field definitions. The A2 flip (TODO_v2 §A2; RESEARCH_v2 §II.1) makes
# the human/pipeline-edited fields ``stage`` + ``owner`` CRM-as-source-of-truth,
# reconciled by last-write-wins (``crm``), while ``funding_state`` stays
# DB-authoritative (``db``) — the GT funding signal is never overwritten by the
# CRM (INV-10). Local ``owner`` is the assigned rep id, compared as a string
# against the mirror's HubSpot owner id.
_TRACKED_FIELDS: tuple[_TrackedField, ...] = (
    _TrackedField(
        name="stage",
        authority=_FieldAuthority.CRM,
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
        authority=_FieldAuthority.CRM,
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


class ReconcileDirection(StrEnum):
    """How a non-synced family's seam should be reconciled (FR-2.6; A2).

    - ``push_local`` — local is the source of truth (local newer / unpushed, or a
      DB-authoritative field where GT wins); mirror the local tracked field and
      mark the seam synced.
    - ``accept_mirror`` — the CRM is the source of truth and is strictly newer
      (the A2 last-write-wins flip for ``stage`` / ``owner``); adopt the mirror's
      value as the new local truth.
    - ``flag_conflict`` — a genuinely ambiguous divergence (no clear recency
      winner): needs a human choice. The gate must NOT silently resolve it
      (INV-4-style fail-closed).
    """

    PUSH_LOCAL = "push_local"
    ACCEPT_MIRROR = "accept_mirror"
    FLAG_CONFLICT = "flag_conflict"


def _field_direction(
    field: _TrackedField, record: FamilyRecord, mirror: MirrorState
) -> ReconcileDirection:
    """The proposed reconcile direction for ONE diverging tracked field (A2; §4.7).

    Per-field authority decides:

    - a DB-authoritative field (``funding_state``) — GT/DB always wins (INV-10):
      a divergence is a pending ``push_local`` unless recency is ambiguous
      (neither side clearly newer), which flags a conflict (§4.7). The CRM value
      is never adopted.
    - a CRM-authoritative field (``stage`` / ``owner``) — last-write-wins (A2):
      the mirror's ``mirror_updated_at`` (HubSpot ``hs_lastmodifieddate``) vs the
      local ``updated_at``. Mirror strictly newer ⇒ ``accept_mirror``; local
      strictly newer ⇒ ``push_local``; equal/missing ⇒ ``flag_conflict``
      (INV-4 fail-closed).
    """
    if field.authority is _FieldAuthority.DB:
        if _neither_side_clearly_newer(record, mirror):
            return ReconcileDirection.FLAG_CONFLICT
        return ReconcileDirection.PUSH_LOCAL

    local_at = record.updated_at
    mirror_at = mirror.mirror_updated_at
    if local_at is None or mirror_at is None or local_at == mirror_at:
        return ReconcileDirection.FLAG_CONFLICT
    if mirror_at > local_at:
        return ReconcileDirection.ACCEPT_MIRROR
    return ReconcileDirection.PUSH_LOCAL


def _resolve_direction(record: FamilyRecord, mirror: MirrorState) -> ReconcileDirection | None:
    """Aggregate the per-field reconcile directions for a row (A2; §4.7).

    ``None`` when no tracked field diverges. Otherwise fail-closed precedence:

    1. any ambiguous field ⇒ ``flag_conflict``;
    2. fields disagree on direction (one wants to pull the CRM, another to push
       local) ⇒ ``flag_conflict`` — a mixed resolution is ambiguous, never
       silently split (INV-4);
    3. any field wants the CRM value ⇒ ``accept_mirror``;
    4. otherwise ⇒ ``push_local`` (a pending push).
    """
    diverging = _diverging_fields(record, mirror)
    if not diverging:
        return None
    directions = {_field_direction(field, record, mirror) for field in diverging}
    if ReconcileDirection.FLAG_CONFLICT in directions:
        return ReconcileDirection.FLAG_CONFLICT
    if {ReconcileDirection.ACCEPT_MIRROR, ReconcileDirection.PUSH_LOCAL} <= directions:
        return ReconcileDirection.FLAG_CONFLICT
    if ReconcileDirection.ACCEPT_MIRROR in directions:
        return ReconcileDirection.ACCEPT_MIRROR
    return ReconcileDirection.PUSH_LOCAL


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

    Row-level status aggregates the per-field reconcile directions (A2,
    :func:`_resolve_direction`). Rules, in order:

    1. ``conflict`` — the row's reconcile resolution is ``flag_conflict`` (an
       ambiguous/mixed divergence) or ``accept_mirror`` (a CRM-authoritative field
       is strictly newer and must be pulled): either way the row diverges and
       needs a reconcile decision, surfaced in the seam view.
    2. ``synced`` — no divergence (or a plain pending push) and
       ``crm_synced_at >= updated_at``: the CRM reflects the latest local state
       (inclusive boundary: an equal instant is synced).
    3. ``unsynced`` — otherwise: ``crm_synced_at`` is null or strictly precedes
       ``updated_at``, so local changes have not been pushed.

    Args:
        record: The family record whose ``updated_at`` / ``crm_synced_at`` /
            tracked fields are compared against the mirror.
        mirror: The simulated HubSpot mirror's view of this family.

    Returns:
        The derived :class:`SeamStatus`.
    """
    direction = _resolve_direction(record, mirror)
    if direction in (ReconcileDirection.FLAG_CONFLICT, ReconcileDirection.ACCEPT_MIRROR):
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

    Resolves the per-field reconcile directions (A2, :func:`_resolve_direction`)
    into one proposal:

    - no divergence and already ``synced`` — nothing to do; returns ``None``.
    - ``push_local`` — local is newer/unpushed (or GT wins a DB field); propose
      pushing local to the CRM.
    - ``accept_mirror`` — a CRM-authoritative field (``stage`` / ``owner``) is
      strictly newer (A2 last-write-wins); propose adopting the CRM value.
    - ``flag_conflict`` — an ambiguous/mixed divergence with no clear winner;
      propose flagging. A true conflict is **never** auto-resolved here: the gate
      flags it for a human choice rather than silently picking a side
      (INV-4-style fail-closed).

    Args:
        record: The family record being reconciled.
        mirror: The simulated HubSpot mirror's view of this family.

    Returns:
        A :class:`ReconcileProposal`, or ``None`` when already ``synced``.
    """
    direction = _resolve_direction(record, mirror)
    if direction is None:
        # No tracked field diverges. Either already synced (no-op → None) or a
        # plain unpushed local change (pending push).
        if derive_seam_status(record, mirror) is SeamStatus.SYNCED:
            return None
        direction = ReconcileDirection.PUSH_LOCAL

    if direction is ReconcileDirection.FLAG_CONFLICT:
        diverging = ", ".join(field.name for field in _diverging_fields(record, mirror))
        summary = (
            f"Conflict on {diverging}: local and CRM disagree and neither side is "
            "clearly newer — needs a human choice."
        )
    elif direction is ReconcileDirection.ACCEPT_MIRROR:
        diverging = ", ".join(field.name for field in _diverging_fields(record, mirror))
        summary = (
            f"CRM newer on {diverging}: HubSpot was edited after the local change "
            "(last-write-wins) — accept the CRM value."
        )
    else:  # ReconcileDirection.PUSH_LOCAL
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

    For ``push_local``: the mirror adopts every local tracked field (``stage`` +
    ``funding_state`` + ``owner``) and ``crm_synced_at`` advances to
    ``updated_at``, so :func:`derive_seam_status` on the returned pair yields
    ``synced``. For ``accept_mirror`` (A2 last-write-wins, CRM strictly newer):
    the record adopts the CRM ``stage`` while ``funding_state`` stays the DB value
    (INV-10 — never accepted), and the pair re-derives ``synced``.

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
        # Human-approved CRM-wins (A2 last-write-wins): adopt the CRM's
        # ``stage`` (the cleanly-modeled CRM-authoritative field) as the new local
        # truth, then mark synced. ``funding_state`` is GT-controlled (INV-10) and
        # is NEVER accepted from the mirror — the DB value is kept and the mirror
        # is set to it (DB wins). ``owner`` adoption into the local UUID
        # assignment is a later A2 unit (the write-back/poller); here the mirror's
        # owner is aligned to the DB assignment so the pair re-derives ``synced``.
        adopted_stage = (
            proposal.mirror_stage if proposal.mirror_stage is not None else record.current_stage
        )
        new_record = record.model_copy(
            update={
                "current_stage": adopted_stage,
                "crm_synced_at": record.updated_at,
            }
        )
        new_mirror = MirrorState(
            stage=adopted_stage,
            funding_state=record.funding_state,  # DB wins (INV-10) — push, not adopt.
            owner=local_owner,
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

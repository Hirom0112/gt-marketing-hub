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

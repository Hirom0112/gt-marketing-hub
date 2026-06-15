"""S0 seam-status deriver tests (ARCHITECTURE.md §4.7; CLAUDE.md §4.1).

The Supabase↔HubSpot seam is modeled as data: `family_record.crm_seam_status`
is a *derived* column computed by a pure function in `app/core/seam.py`. These
tests pin the three §4.7 derivation rules (red → green, §4.1) and the
null-`crm_synced_at` edge:

- `synced`   — `crm_synced_at >= updated_at` (CRM reflects latest local state).
- `unsynced` — `crm_synced_at` is null or `< updated_at` (local changes unpushed).
- `conflict` — the simulated HubSpot mirror holds a tracked-field value that
  diverges from local with neither side clearly newer.

Pure unit: no I/O, no adapters, no LLM — only the model + the deriver.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.seam import MirrorState, derive_seam_status

from app.data.models import FamilyRecord, SeamStatus, Stage

# A fixed clock so every comparison is exact and reproducible (no magic numbers
# floating in the assertions — all instants derive from these anchors).
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # local last-touched baseline.
_BEFORE = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)  # one hour earlier.
_AFTER = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)  # one hour later.


def _family_record(
    *,
    updated_at: datetime,
    crm_synced_at: datetime | None,
    current_stage: Stage = Stage.APPLY,
) -> FamilyRecord:
    """A FamilyRecord seeded with just the §4.1 seam-relevant columns."""
    return FamilyRecord(
        family_id=uuid4(),
        display_name="The Rivera Family",
        primary_contact_synthetic_email="rivera.synthetic@example.invalid",
        current_stage=current_stage,
        attribution_source="referral",
        attribution_utm={"utm_source": "newsletter"},
        updated_at=updated_at,
        crm_synced_at=crm_synced_at,
    )


def test_seam_status_synced_unsynced_conflict() -> None:
    """All three §4.7 branches plus the null-`crm_synced_at` edge."""
    # --- synced: crm_synced_at >= updated_at, mirror agrees on tracked field. ---
    synced = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror_agrees = MirrorState(stage=Stage.APPLY, mirror_updated_at=_AFTER)
    assert derive_seam_status(synced, mirror_agrees) is SeamStatus.SYNCED

    # synced boundary: crm_synced_at == updated_at is still synced (>= is inclusive).
    synced_boundary = _family_record(updated_at=_T0, crm_synced_at=_T0)
    assert (
        derive_seam_status(synced_boundary, MirrorState(stage=Stage.APPLY, mirror_updated_at=_T0))
        is SeamStatus.SYNCED
    )

    # --- unsynced: crm_synced_at < updated_at (local edited after last push). ---
    unsynced = _family_record(updated_at=_T0, crm_synced_at=_BEFORE)
    assert (
        derive_seam_status(unsynced, MirrorState(stage=Stage.APPLY, mirror_updated_at=_BEFORE))
        is SeamStatus.UNSYNCED
    )

    # --- unsynced (null edge): crm_synced_at is null ⇒ never pushed. ---
    never_synced = _family_record(updated_at=_T0, crm_synced_at=None)
    assert (
        derive_seam_status(never_synced, MirrorState(stage=Stage.APPLY, mirror_updated_at=None))
        is SeamStatus.UNSYNCED
    )

    # --- conflict: mirror holds a diverging tracked-field value (stage), and
    #     neither side is clearly newer (mirror_updated_at == updated_at). The
    #     timestamps would otherwise read `synced`, but divergence wins (§4.7). ---
    conflicting = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror_diverges = MirrorState(stage=Stage.ENROLL, mirror_updated_at=_T0)
    assert derive_seam_status(conflicting, mirror_diverges) is SeamStatus.CONFLICT

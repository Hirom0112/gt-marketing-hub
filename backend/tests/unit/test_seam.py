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
from uuid import UUID, uuid4

from app.core.seam import (
    MirrorState,
    ReconcileDirection,
    apply_reconcile,
    derive_seam_status,
    propose_reconcile,
)
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage

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
    funding_state: FundingState = FundingState.NONE,
    user_id: UUID | None = None,
) -> FamilyRecord:
    """A FamilyRecord seeded with just the §4.1 seam-relevant columns."""
    return FamilyRecord(
        family_id=uuid4(),
        user_id=user_id,
        display_name="The Rivera Family",
        primary_contact_synthetic_email="rivera.synthetic@example.invalid",
        current_stage=current_stage,
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={"utm_source": "newsletter"},
        updated_at=updated_at,
        crm_synced_at=crm_synced_at,
    )


def test_derive_synced() -> None:
    """`synced`: crm_synced_at >= updated_at and the mirror agrees (§4.7)."""
    # crm_synced_at strictly after updated_at, mirror agrees on tracked field.
    synced = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror_agrees = MirrorState(stage=Stage.APPLY, mirror_updated_at=_AFTER)
    assert derive_seam_status(synced, mirror_agrees) is SeamStatus.SYNCED

    # synced boundary: crm_synced_at == updated_at is still synced (>= is inclusive).
    synced_boundary = _family_record(updated_at=_T0, crm_synced_at=_T0)
    assert (
        derive_seam_status(synced_boundary, MirrorState(stage=Stage.APPLY, mirror_updated_at=_T0))
        is SeamStatus.SYNCED
    )


def test_derive_unsynced() -> None:
    """`unsynced`: crm_synced_at is null or strictly before updated_at (§4.7)."""
    # crm_synced_at < updated_at (local edited after last push).
    unsynced = _family_record(updated_at=_T0, crm_synced_at=_BEFORE)
    assert (
        derive_seam_status(unsynced, MirrorState(stage=Stage.APPLY, mirror_updated_at=_BEFORE))
        is SeamStatus.UNSYNCED
    )

    # null edge: crm_synced_at is null ⇒ never pushed.
    never_synced = _family_record(updated_at=_T0, crm_synced_at=None)
    assert (
        derive_seam_status(never_synced, MirrorState(stage=Stage.APPLY, mirror_updated_at=None))
        is SeamStatus.UNSYNCED
    )


def test_derive_conflict() -> None:
    """`conflict`: mirror diverges on a tracked field, neither side newer (§4.7)."""
    # mirror holds a diverging tracked-field value (stage), and neither side is
    # clearly newer (mirror_updated_at == updated_at). The timestamps would
    # otherwise read `synced`, but divergence wins (§4.7).
    conflicting = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror_diverges = MirrorState(stage=Stage.ENROLL, mirror_updated_at=_T0)
    assert derive_seam_status(conflicting, mirror_diverges) is SeamStatus.CONFLICT


# ---------------------------------------------------------------------------
# R1 — multi-field reconcile with per-field authority (TODO.md R1; §4.7).
# `funding_state` is DB-authoritative (DB always wins → drift is a pending push,
# never a conflict). `owner` is CRM-authoritative (human-edited in HubSpot →
# any divergence is a genuine conflict, never silently overwritten). Row status
# aggregates per-field results: any conflict ⇒ conflict; else any unsynced ⇒
# unsynced; else synced.
# ---------------------------------------------------------------------------


def test_funding_state_drift_is_db_authoritative_unsynced() -> None:
    """`funding_state` diverges but `stage` matches ⇒ drift, DB wins (unsynced).

    The mirror holds a stale `funding_state`; the stage agrees and crm_synced_at
    is behind updated_at. `funding_state` is DB-authoritative, so its divergence
    is a pending push (→ push_local), NOT a conflict — even though the value
    differs. Aggregated row status is `unsynced`.
    """
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_BEFORE,
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
    )
    # Mirror agrees on stage, but holds a stale (diverging) funding_state.
    mirror = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.APPLIED,
        mirror_updated_at=_BEFORE,
    )
    assert derive_seam_status(record, mirror) is SeamStatus.UNSYNCED

    proposal = propose_reconcile(record, mirror)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.PUSH_LOCAL


def test_owner_divergence_is_crm_authoritative_conflict() -> None:
    """`owner` diverges ⇒ conflict (CRM-authoritative, never overwritten).

    Even with timestamps that would otherwise read `synced` (crm_synced_at after
    updated_at) and stage/funding agreeing, a diverging CRM-authoritative `owner`
    is a genuine conflict the gate must flag, not silently push over.
    """
    local_owner = uuid4()
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,
        current_stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        user_id=local_owner,
    )
    mirror = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        owner="someone-else-in-hubspot",
        mirror_updated_at=_AFTER,
    )
    assert derive_seam_status(record, mirror) is SeamStatus.CONFLICT

    proposal = propose_reconcile(record, mirror)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.FLAG_CONFLICT


def test_all_tracked_fields_match_is_synced() -> None:
    """Every tracked field agrees and CRM is fresh ⇒ `synced` (§4.7)."""
    local_owner = uuid4()
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
        user_id=local_owner,
    )
    mirror = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
        owner=str(local_owner),
        mirror_updated_at=_AFTER,
    )
    assert derive_seam_status(record, mirror) is SeamStatus.SYNCED


# ---------------------------------------------------------------------------
# Reconcile flow (FR-2.6; ARCH §4.7). Deterministic, human-gated, simulated v1.
# Proposal is computed by the core; application is only ever invoked via the
# human-approved API path — these tests pin the pure post-reconcile state.
# ---------------------------------------------------------------------------


def test_propose_reconcile_synced_is_noop() -> None:
    """A synced family needs no reconcile ⇒ `propose_reconcile` returns None."""
    synced = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror = MirrorState(stage=Stage.APPLY, mirror_updated_at=_AFTER)
    assert propose_reconcile(synced, mirror) is None


def test_propose_reconcile_unsynced_proposes_push_local() -> None:
    """An unsynced family (local newer / unpushed) ⇒ propose `push_local` (§4.7)."""
    unsynced = _family_record(updated_at=_T0, crm_synced_at=_BEFORE, current_stage=Stage.ENROLL)
    mirror = MirrorState(stage=Stage.APPLY, mirror_updated_at=_BEFORE)

    proposal = propose_reconcile(unsynced, mirror)

    assert proposal is not None
    assert proposal.direction is ReconcileDirection.PUSH_LOCAL
    assert proposal.family_id == unsynced.family_id
    # The local tracked-field value is what would be pushed to the mirror.
    assert proposal.local_stage is Stage.ENROLL
    assert proposal.summary  # human-readable, non-empty.


def test_propose_reconcile_conflict_flags_not_autoresolved() -> None:
    """A true conflict is FLAGGED, never silently resolved (INV-4-style, §4.7).

    `propose_reconcile` yields `flag_conflict` (no auto-picked winner), and
    `apply_reconcile` on that flagged proposal does NOT recompute to `synced` —
    it fails closed until a human supplies a chosen direction.
    """
    conflicting = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    mirror = MirrorState(stage=Stage.ENROLL, mirror_updated_at=_T0)

    proposal = propose_reconcile(conflicting, mirror)

    assert proposal is not None
    assert proposal.direction is ReconcileDirection.FLAG_CONFLICT

    # Fail-closed: applying a flagged conflict does NOT mark it synced.
    result = apply_reconcile(conflicting, proposal)
    assert result.applied is False
    assert result.seam_status is SeamStatus.CONFLICT
    assert derive_seam_status(conflicting, result.mirror) is SeamStatus.CONFLICT


def test_apply_reconcile_push_local_recomputes_synced() -> None:
    """After an approved push_local reconcile, the seam recomputes to `synced`."""
    unsynced = _family_record(updated_at=_T0, crm_synced_at=_BEFORE, current_stage=Stage.ENROLL)
    mirror = MirrorState(stage=Stage.APPLY, mirror_updated_at=_BEFORE)

    proposal = propose_reconcile(unsynced, mirror)
    assert proposal is not None

    result = apply_reconcile(unsynced, proposal)

    assert result.applied is True
    assert result.seam_status is SeamStatus.SYNCED
    # The returned post-reconcile record + mirror re-derive to synced (A-7 pattern):
    # local state is mirrored and crm_synced_at advances to updated_at.
    assert result.record.crm_synced_at == unsynced.updated_at
    assert result.mirror.stage is Stage.ENROLL
    assert derive_seam_status(result.record, result.mirror) is SeamStatus.SYNCED

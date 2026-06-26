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
    assigned_rep_id: UUID | None = None,
    assigned_at: datetime | None = None,
) -> FamilyRecord:
    """A FamilyRecord seeded with just the §4.1 seam-relevant columns."""
    return FamilyRecord(
        family_id=uuid4(),
        user_id=user_id,
        assigned_rep_id=assigned_rep_id,
        assigned_at=assigned_at,
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
# R1 + M4 — multi-field reconcile with per-field authority (TODO.md R1/M4; §4.7).
# `funding_state` is DB-authoritative (DB always wins → drift is a pending push,
# never a conflict). `owner` was CRM-authoritative; the M4 owner-authority flip
# (USER-RATIFIED 2026-06-17, A-30) makes `owner` DB-AUTHORITATIVE, driven by
# `assigned_rep_id` (the DB owns assignment now) — UNLESS the mirror's owner
# changed AFTER our last `assigned_at` (someone edited HubSpot post-assignment),
# in which case the seam FLAGS a conflict instead of stomping the human edit
# (INV-4-style fail-closed guard). Row status aggregates per-field results: any
# conflict ⇒ conflict; else any unsynced ⇒ unsynced; else synced.
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


def test_owner_db_authority_with_guard() -> None:
    """`owner` is DB-authoritative (M4 flip) with a post-`assigned_at` guard (A-30).

    Two pinned cases:

    (a) DB authority + PUSH: the DB owns the assignment (``assigned_rep_id``); a
        diverging mirror owner that did NOT change after our ``assigned_at`` is a
        plain pending push (DB wins → ``unsynced``), and ``propose_reconcile``
        yields ``push_local`` — the DB owner is pushed to the mirror, no conflict.
    (b) GUARD → CONFLICT: if the mirror's owner changed AFTER our ``assigned_at``
        (someone edited the HubSpot deal owner post-assignment), the seam FLAGS a
        conflict instead of stomping that human edit (INV-4-style fail-closed).
    """
    rep = uuid4()

    # (a) DB-authoritative push: mirror diverges on owner, but its last change
    # (mirror_updated_at) is NOT after our assigned_at — DB wins, plain push.
    pushable = _family_record(
        updated_at=_T0,
        crm_synced_at=_BEFORE,  # local touched after last push ⇒ unsynced baseline.
        current_stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        assigned_rep_id=rep,
        assigned_at=_T0,
    )
    mirror_stale_owner = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        owner="stale-owner-in-hubspot",
        mirror_updated_at=_BEFORE,  # mirror changed BEFORE assigned_at ⇒ no guard.
    )
    assert derive_seam_status(pushable, mirror_stale_owner) is SeamStatus.UNSYNCED
    proposal = propose_reconcile(pushable, mirror_stale_owner)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.PUSH_LOCAL

    # (b) Guard fires: the mirror owner changed AFTER assigned_at ⇒ conflict, not
    # a blind overwrite (don't stomp a post-assignment HubSpot edit).
    guarded = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,  # timestamps alone would read synced…
        current_stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        assigned_rep_id=rep,
        assigned_at=_T0,
    )
    mirror_changed_after = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.NONE,
        owner="someone-changed-it-in-hubspot",
        mirror_updated_at=_AFTER,  # …but the mirror owner changed AFTER assigned_at.
    )
    assert derive_seam_status(guarded, mirror_changed_after) is SeamStatus.CONFLICT
    proposal = propose_reconcile(guarded, mirror_changed_after)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.FLAG_CONFLICT


def test_all_tracked_fields_match_is_synced() -> None:
    """Every tracked field agrees and CRM is fresh ⇒ `synced` (§4.7)."""
    rep = uuid4()
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
        assigned_rep_id=rep,
        assigned_at=_T0,
    )
    mirror = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
        owner=str(rep),
        mirror_updated_at=_AFTER,
    )
    assert derive_seam_status(record, mirror) is SeamStatus.SYNCED


# ---------------------------------------------------------------------------
# A2 — flip the seam to CRM-as-source-of-truth for the human/pipeline-edited
# fields (`stage`, `owner`) via last-write-wins (TODO_v2 §A2; RESEARCH_v2 §II.1).
# HubSpot has no native last-write-wins, so we implement it: the mirror's
# `mirror_updated_at` (the HubSpot `hs_lastmodifieddate`) is compared against the
# local `updated_at`. `funding_state` stays DB-authoritative (INV-10 — GT owns it,
# never overwritten by the CRM). Ambiguous recency (equal/missing) still flags a
# conflict (INV-4 fail-closed).
# ---------------------------------------------------------------------------


def test_crm_wins_when_mirror_strictly_newer() -> None:
    """A2: `stage`/`owner` are CRM-as-truth via last-write-wins on the timestamps.

    For a CRM-authoritative tracked field that diverges, the strictly-newer side
    wins:

    - mirror `mirror_updated_at` (HubSpot `hs_lastmodifieddate`) strictly newer
      than local `updated_at` ⇒ CRM wins ⇒ propose `ACCEPT_MIRROR`;
    - local strictly newer ⇒ propose `push_local`;
    - equal instant ⇒ ambiguous ⇒ `flag_conflict` (INV-4 fail-closed).
    """
    # --- stage divergence ---
    # (a) mirror strictly newer ⇒ CRM wins ⇒ accept_mirror.
    crm_newer = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    proposal = propose_reconcile(crm_newer, MirrorState(stage=Stage.ENROLL, mirror_updated_at=_AFTER))
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.ACCEPT_MIRROR

    # (b) local strictly newer ⇒ push_local.
    local_newer = _family_record(updated_at=_T0, crm_synced_at=_BEFORE, current_stage=Stage.APPLY)
    proposal = propose_reconcile(
        local_newer, MirrorState(stage=Stage.ENROLL, mirror_updated_at=_BEFORE)
    )
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.PUSH_LOCAL

    # (c) equal instant ⇒ ambiguous ⇒ flag_conflict (no clear winner).
    ambiguous = _family_record(updated_at=_T0, crm_synced_at=_AFTER, current_stage=Stage.APPLY)
    proposal = propose_reconcile(ambiguous, MirrorState(stage=Stage.ENROLL, mirror_updated_at=_T0))
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.FLAG_CONFLICT

    # --- owner divergence (same last-write-wins rule) ---
    rep = uuid4()
    owner_crm_newer = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,
        current_stage=Stage.APPLY,
        assigned_rep_id=rep,
        assigned_at=_T0,
    )
    mirror_owner_newer = MirrorState(
        stage=Stage.APPLY,
        owner="hubspot-edited-owner",
        mirror_updated_at=_AFTER,  # CRM edited the owner AFTER local ⇒ CRM wins.
    )
    proposal = propose_reconcile(owner_crm_newer, mirror_owner_newer)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.ACCEPT_MIRROR


def test_funding_state_never_accepted_from_mirror() -> None:
    """A2/INV-10: `funding_state` is GT-controlled — NEVER accepted from the CRM.

    Even when the mirror's `funding_state` diverges AND the mirror is strictly
    newer (which for `stage`/`owner` would yield `ACCEPT_MIRROR`), `funding_state`
    stays DB-authoritative: the proposal is never `accept_mirror`. The GT value
    wins and is pushed to the CRM (`push_local`), never overwritten by it.
    """
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_BEFORE,
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
    )
    # Mirror is strictly newer AND holds a diverging funding_state.
    mirror = MirrorState(
        stage=Stage.APPLY,
        funding_state=FundingState.APPLIED,
        mirror_updated_at=_AFTER,  # strictly newer than local updated_at.
    )
    proposal = propose_reconcile(record, mirror)
    assert proposal is not None
    assert proposal.direction is not ReconcileDirection.ACCEPT_MIRROR
    assert proposal.direction is ReconcileDirection.PUSH_LOCAL


def test_accept_mirror_writes_crm_value_into_record() -> None:
    """A2: `apply_reconcile(ACCEPT_MIRROR)` adopts the CRM stage, re-derives synced.

    A CRM-newer `stage` divergence proposes `ACCEPT_MIRROR`; applying it returns a
    record whose `current_stage` equals the mirror's value (the CRM value written
    into the record) and a mirror that agrees, so the seam re-derives `synced`.
    `funding_state` is untouched (DB-authoritative, INV-10).
    """
    rep = uuid4()
    record = _family_record(
        updated_at=_T0,
        crm_synced_at=_AFTER,
        current_stage=Stage.APPLY,
        funding_state=FundingState.GT_CONFIRMED,
        assigned_rep_id=rep,
        assigned_at=_T0,
    )
    mirror = MirrorState(
        stage=Stage.ENROLL,  # diverging stage…
        owner=str(rep),  # …owner agrees (the CRM owner adoption is a later unit).
        mirror_updated_at=_AFTER,  # CRM strictly newer ⇒ accept_mirror.
    )
    proposal = propose_reconcile(record, mirror)
    assert proposal is not None
    assert proposal.direction is ReconcileDirection.ACCEPT_MIRROR

    result = apply_reconcile(record, proposal)
    assert result.applied is True
    # The CRM value is written into the record.
    assert result.record.current_stage is Stage.ENROLL
    assert result.mirror.stage is Stage.ENROLL
    # funding_state is GT-controlled — the DB value is kept (never the mirror's).
    assert result.record.funding_state is FundingState.GT_CONFIRMED
    # Record and mirror now agree ⇒ re-derives synced.
    assert result.seam_status is SeamStatus.SYNCED
    assert derive_seam_status(result.record, result.mirror) is SeamStatus.SYNCED


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

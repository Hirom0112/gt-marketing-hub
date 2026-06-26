"""A4 sync-parity aggregator tests (TODO_v2 §A4; ARCHITECTURE.md §4.7).

The pure cohort-level parity aggregator over the per-family seam status:

- **overall** — rows whose :func:`derive_seam_status` is ``SYNCED`` / total.
- **by_field** — for each tracked field (``stage`` / ``funding_state`` /
  ``owner``) the fraction of rows whose DB value equals the mirror value.

This is the INV-2 / A-7 *pure* core: no I/O, no adapters, no LLM — the API layer
(a different unit) reads the cohort + mirrors and feeds them in. The fixture pins
``overall == 0.8000`` (8/10) and the per-field breakdown to 4 dp, computed
explicitly from how the two non-synced rows distribute their divergence across
the tracked fields. Reuses :func:`derive_seam_status` (never re-implements the
status logic) so the fixture stays self-documenting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.parity import ParityScore, compute_parity
from app.core.seam import MirrorState, derive_seam_status
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage

# A fixed clock so every recency comparison is exact and reproducible — the seam
# pairs DB ``updated_at`` against the mirror's ``mirror_updated_at`` (A2).
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # local last-touched baseline.
_BEFORE = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)  # one hour earlier.
_AFTER = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)  # one hour later.


def _pair(
    *,
    current_stage: Stage,
    funding_state: FundingState,
    updated_at: datetime,
    crm_synced_at: datetime | None,
    mirror_stage: Stage | None,
    mirror_funding: FundingState | None,
    mirror_updated_at: datetime | None,
) -> tuple[FamilyRecord, MirrorState]:
    """One (FamilyRecord, MirrorState) cohort row with owner kept in agreement.

    Owner is always aligned (mirror owner == ``str(assigned_rep_id)``) so the
    fixture's divergence lives only in ``stage`` / ``funding_state`` — keeping the
    per-field expected values easy to compute by hand.
    """
    rep_id = uuid4()
    record = FamilyRecord(
        family_id=uuid4(),
        assigned_rep_id=rep_id,
        display_name="The Rivera Family",
        primary_contact_synthetic_email="rivera.synthetic@example.invalid",
        current_stage=current_stage,
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={"utm_source": "newsletter"},
        updated_at=updated_at,
        crm_synced_at=crm_synced_at,
    )
    mirror = MirrorState(
        stage=mirror_stage,
        funding_state=mirror_funding,
        owner=str(rep_id),
        mirror_updated_at=mirror_updated_at,
    )
    return record, mirror


def test_parity_score_on_fixture() -> None:
    """A 10-row cohort (8 synced / 1 unsynced / 1 conflict) → overall 0.8000.

    Per-field breakdown, computed explicitly from the fixture:

    - ``stage`` — diverges on the UNSYNCED row only (agree on the 8 synced + the
      conflict row) ⇒ 9/10 = 0.9000.
    - ``funding_state`` — diverges on the CONFLICT row only (agree on the 8 synced
      + the unsynced row) ⇒ 9/10 = 0.9000.
    - ``owner`` — always aligned ⇒ 10/10 = 1.0000.
    """
    # 8 fully SYNCED rows: every tracked field agrees and crm_synced_at >= updated_at.
    synced = [
        _pair(
            current_stage=Stage.APPLY,
            funding_state=FundingState.NONE,
            updated_at=_T0,
            crm_synced_at=_AFTER,
            mirror_stage=Stage.APPLY,
            mirror_funding=FundingState.NONE,
            mirror_updated_at=_AFTER,
        )
        for _ in range(8)
    ]

    # 1 UNSYNCED row: stage diverges (ENROLL vs APPLY) with local strictly newer
    # (push_local, not a conflict), and crm_synced_at strictly precedes updated_at.
    unsynced = _pair(
        current_stage=Stage.ENROLL,
        funding_state=FundingState.NONE,
        updated_at=_T0,
        crm_synced_at=_BEFORE,
        mirror_stage=Stage.APPLY,
        mirror_funding=FundingState.NONE,
        mirror_updated_at=_BEFORE,
    )

    # 1 CONFLICT row: funding_state diverges (FUNDED vs APPLIED). funding_state is
    # DB-authoritative (INV-10) and recency is ambiguous (mirror_updated_at ==
    # updated_at) ⇒ flag_conflict ⇒ CONFLICT. stage + owner agree.
    conflict = _pair(
        current_stage=Stage.APPLY,
        funding_state=FundingState.FUNDED,
        updated_at=_T0,
        crm_synced_at=_AFTER,
        mirror_stage=Stage.APPLY,
        mirror_funding=FundingState.APPLIED,
        mirror_updated_at=_T0,
    )

    # The fixture is self-documenting: assert each row's derived seam status.
    assert all(derive_seam_status(r, m) is SeamStatus.SYNCED for r, m in synced)
    assert derive_seam_status(*unsynced) is SeamStatus.UNSYNCED
    assert derive_seam_status(*conflict) is SeamStatus.CONFLICT

    cohort = [*synced, unsynced, conflict]
    score = compute_parity(cohort)

    assert isinstance(score, ParityScore)
    assert score.overall == pytest.approx(0.8000, abs=1e-4)  # 8 / 10
    assert score.by_field["stage"] == pytest.approx(0.9000, abs=1e-4)  # 9 / 10
    assert score.by_field["funding_state"] == pytest.approx(0.9000, abs=1e-4)  # 9 / 10
    assert score.by_field["owner"] == pytest.approx(1.0000, abs=1e-4)  # 10 / 10


def test_empty_cohort_is_full_parity() -> None:
    """Empty cohort ⇒ full parity (1.0): no rows means no divergence.

    The documented boring choice (A4): an empty active-program cohort reports
    overall and every per-field fraction as 1.0 — "nothing is out of sync".
    """
    score = compute_parity([])
    assert score.overall == 1.0
    assert score.by_field == {"stage": 1.0, "funding_state": 1.0, "owner": 1.0}

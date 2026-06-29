"""Module-5 aggregate-read tests for the SimulatedCRMAdapter (offline, INV-9/INV-6).

The Nurture overview/pipeline run on the simulate seam by default; these prove the
simulated engagement-tier mix and pipeline snapshot are DETERMINISTIC over a cohort
(the same ids ⇒ the same answer) and aggregate-only (counts, never per-person rows).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.core.seam import MirrorState

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_STAGE_ORDER = ["interest", "apply", "enroll", "tuition", "closed_lost"]
_HANDOFF = ["enroll", "tuition"]
_IDS = [UUID(int=i) for i in range(60)]


def test_engagement_mix_partitions_cohort_deterministically() -> None:
    adapter = SimulatedCRMAdapter()
    mix = adapter.read_engagement_mix(_IDS)
    # Each id falls in EXACTLY one tier ⇒ the three counts sum to the cohort size.
    assert mix.total == len(_IDS)
    assert mix.clicked + mix.opened + mix.cold == len(_IDS)
    # Deterministic: a re-read gives the identical mix.
    again = adapter.read_engagement_mix(_IDS)
    assert (again.clicked, again.opened, again.cold) == (mix.clicked, mix.opened, mix.cold)


def test_engagement_mix_empty_cohort() -> None:
    mix = SimulatedCRMAdapter().read_engagement_mix([])
    assert mix.total == 0


def test_pipeline_snapshot_counts_sum_to_cohort() -> None:
    adapter = SimulatedCRMAdapter()
    snap = adapter.read_pipeline_snapshot(
        _IDS,
        stage_order=_STAGE_ORDER,
        handoff_stages=_HANDOFF,
        now=_NOW,
        stuck_days=14,
        week_days=7,
        month_days=30,
    )
    assert {s.stage for s in snap.stages} == set(_STAGE_ORDER)
    assert sum(s.count for s in snap.stages) == len(_IDS)
    # Stuck never exceeds the stage count.
    assert all(s.stuck <= s.count for s in snap.stages)
    # Weekly handoff is a subset of the monthly handoff.
    assert snap.handoff_week <= snap.handoff_month


def test_read_last_modified_is_deterministic_max_over_mirror() -> None:
    """read_last_modified returns the DETERMINISTIC MAX mirror_updated_at (INV-9; no I/O)."""
    adapter = SimulatedCRMAdapter()
    # Empty recorder ⇒ no watermark yet.
    assert adapter.read_last_modified("contacts") is None
    older = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    newer = datetime(2026, 6, 10, 18, 30, tzinfo=UTC)
    adapter.seed_mirror(UUID(int=1), MirrorState(stage=None, mirror_updated_at=older))
    adapter.seed_mirror(UUID(int=2), MirrorState(stage=None, mirror_updated_at=newer))
    # The MAX over the seeded mirrors, identical for either object type (recorder keys
    # by family) and stable across re-reads.
    assert adapter.read_last_modified("contacts") == newer
    assert adapter.read_last_modified("deals") == newer


def test_pipeline_snapshot_is_deterministic() -> None:
    adapter = SimulatedCRMAdapter()
    kw = {
        "stage_order": _STAGE_ORDER,
        "handoff_stages": _HANDOFF,
        "now": _NOW,
        "stuck_days": 14,
        "week_days": 7,
        "month_days": 30,
    }
    a = adapter.read_pipeline_snapshot(_IDS, **kw)  # type: ignore[arg-type]
    b = adapter.read_pipeline_snapshot(_IDS, **kw)  # type: ignore[arg-type]
    assert [(s.stage, s.count, s.stuck) for s in a.stages] == [
        (s.stage, s.count, s.stuck) for s in b.stages
    ]
    assert (a.handoff_week, a.handoff_month) == (b.handoff_week, b.handoff_month)

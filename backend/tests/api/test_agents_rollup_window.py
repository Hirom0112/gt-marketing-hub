"""Optional time-window on the agent roster (D-15; REDESIGN_PLAN R4 task 3).

``GET /enrollment/agents`` gains an optional ``?window={day|week|month|all}`` filter
(default ``all``) so the admin Team Roster can scope each agent's rollup to a trailing
window. It is ADDITIVE: omitting the param is byte-identical to the current all-time
behavior (existing roster callers are unaffected), and a bounded window narrows the
candidate set to families ASSIGNED within the window.

STRICT-TDD (CLAUDE §4.1): asserts ``window=week`` narrows the rollup AND that omitting
the param equals an explicit ``window=all`` (the back-compat guarantee).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage, StallReason
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset
from app.main import app

client = TestClient(app)

AGENT_1 = UUID("a0000000-0000-4000-8000-000000000001")

_NOW = datetime.now(UTC)


def _days_ago(n: int) -> datetime:
    return _NOW - timedelta(days=n)


def _stalled_family(*, assigned_days_ago: int) -> FamilyRecord:
    """A stalled (active-candidate) family assigned to AGENT_1 at an offset."""
    return FamilyRecord(
        family_id=uuid4(),
        display_name="Roster Window Fixture",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=Stage.APPLY,
        stall_reason=StallReason.APP_INCOMPLETE,
        stalled_since=_days_ago(40),
        funding_state=FundingState.NONE,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=_days_ago(90),
        assigned_rep_id=AGENT_1,
        assigned_at=_days_ago(assigned_days_ago),
    )


def _mount(families: list[FamilyRecord]) -> None:
    repo = InMemoryFamilyRepository(SyntheticDataset(families=families))
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()


def _teardown() -> None:
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def _agent_1(window: str | None) -> dict:
    suffix = "" if window is None else f"?window={window}"
    resp = client.get(f"/enrollment/agents{suffix}", headers={"X-Demo-Role": "admin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return next(a for a in body["agents"] if a["agent_id"] == str(AGENT_1))


def test_omitting_window_equals_all_time() -> None:
    """No ``window`` param is byte-identical to ``window=all`` (back-compat, D-15)."""
    fams = [_stalled_family(assigned_days_ago=d) for d in (2, 5, 20, 100)]
    _mount(fams)
    try:
        default = _agent_1(None)
        explicit_all = _agent_1("all")
        assert default == explicit_all
        # All four stalled families are active candidates ⇒ queue_size 4.
        assert default["queue_size"] == 4
    finally:
        _teardown()


def test_week_window_narrows_the_rollup() -> None:
    """``window=week`` keeps only families assigned within the trailing week."""
    fams = [_stalled_family(assigned_days_ago=d) for d in (2, 5, 20, 100)]
    _mount(fams)
    try:
        week = _agent_1("week")
        # Assigned within 7d: the 2d + 5d families ⇒ queue_size 2 (20d/100d drop out).
        assert week["queue_size"] == 2
        # And it is strictly narrower than all-time.
        assert week["queue_size"] < _agent_1("all")["queue_size"]
    finally:
        _teardown()

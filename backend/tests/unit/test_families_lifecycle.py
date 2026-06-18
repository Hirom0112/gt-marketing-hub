"""Read-path wiring for the later-lifecycle states (COLD / PRESUMED_LOST / LOST).

The deterministic core (the deriver, the nurture policy, the LostRecord event)
landed first and is unit-tested; this proves the API composition root
(``_recovery_state_for``) now RESOLVES the three new facts from the audit log +
the request clock and passes them into the deriver — i.e. the states are LIVE on
``GET /families/{id}``, not merely derivable in isolation. Each case is driven
through a hermetic single-family repo so the assertion is deterministic and does
not depend on the synthetic seed.

Precedence locked here (the deriver's contract): RECOVERED beats a presumed-lost
signal, and WORKING (a logged outbound) beats COLD — the later-lifecycle facts
never fabricate a state the recency/recovery signals should win.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.core.params import load_params
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import InMemoryFamilyRepository, JoinedFamily
from app.data.synthetic import SyntheticDataset
from app.main import app
from app.observability.log_store import (
    ContactChannel,
    ContactDisposition,
    DecisionAction,
)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_NURTURE = load_params(EXAMPLE_PARAMS).nurture

client = TestClient(app)


def _stalled_family(
    *,
    stalled_days_ago: int,
    funding_state: FundingState = FundingState.NONE,
) -> JoinedFamily:
    """A non-recovered, uncontacted family stalled ``stalled_days_ago`` days back.

    APPLY stage with no stall_reason ⇒ stall_stage == current_stage (not advanced);
    no forms, funding NONE by default ⇒ none of the RECOVERED signals fire. So its
    derived state is decided purely by the later-lifecycle facts under test.
    """
    now = datetime.now(UTC)
    family = FamilyRecord(
        family_id=uuid4(),
        display_name="Lifecycle Fixture",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=Stage.APPLY,
        stalled_since=now - timedelta(days=stalled_days_ago),
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=now - timedelta(days=stalled_days_ago + 30),
    )
    return JoinedFamily(
        family=family, lead=None, app_form=None, enrollment_forms=None, community_profile=None
    )


def _drive(joined: JoinedFamily) -> tuple[InMemoryFamilyRepository, object]:
    """Mount a single-family repo + a fresh audit log; return (repo, log)."""
    repo = InMemoryFamilyRepository(SyntheticDataset(families=[joined.family]))
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()
    return repo, deps.get_observability_log()


def _state(family_id: UUID) -> str:
    resp = client.get(f"/families/{family_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["deal_view"]["recovery_state"]


def test_cold_surfaces_when_aged_and_uncontacted() -> None:
    """A stalled, uncontacted family past cold_after_days ⇒ recovery_state == cold."""
    joined = _stalled_family(stalled_days_ago=_NURTURE.cold_after_days + 10)
    _drive(joined)
    try:
        assert _state(joined.family.family_id) == "cold"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_recently_stalled_stays_stalled_not_cold() -> None:
    """Within cold_after_days the family is still plain STALLED (no premature cold)."""
    joined = _stalled_family(stalled_days_ago=_NURTURE.cold_after_days - 2)
    _drive(joined)
    try:
        assert _state(joined.family.family_id) == "stalled"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_presumed_lost_surfaces_after_threshold_silence() -> None:
    """5 no-answer attempts within 21 days ⇒ recovery_state == presumed_lost."""
    joined = _stalled_family(stalled_days_ago=_NURTURE.cold_after_days + 10)
    _, log = _drive(joined)
    now = datetime.now(UTC)
    try:
        for d in (1, 3, 5, 7, 9):  # all within within_days=21
            log.log_contact_outcome(
                family_id=joined.family.family_id,
                channel=ContactChannel.CALL,
                disposition=ContactDisposition.NO_ANSWER,
                human="rep",
                created_at=now - timedelta(days=d),
            )
        assert _state(joined.family.family_id) == "presumed_lost"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_lost_holds_after_human_confirm() -> None:
    """A logged LostRecord ⇒ recovery_state == lost (the human-confirmed close)."""
    joined = _stalled_family(stalled_days_ago=_NURTURE.cold_after_days + 10)
    _, log = _drive(joined)
    try:
        log.log_lost(
            family_id=joined.family.family_id,
            human="rep",
            reason="confirmed: family enrolled elsewhere",
        )
        assert _state(joined.family.family_id) == "lost"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_working_beats_cold() -> None:
    """A logged outbound (last_contact_at) keeps the family WORKING, never COLD."""
    joined = _stalled_family(stalled_days_ago=_NURTURE.cold_after_days + 10)
    _, log = _drive(joined)
    try:
        proposal_id = uuid4()
        log.log_proposal(
            proposal_id=proposal_id,
            flow="enrollment_draft",
            schema_version="1",
            payload={"action": "sms", "body": "hi"},
            family_id=joined.family.family_id,
        )
        log.log_decision(proposal_id=proposal_id, human="rep", action=DecisionAction.APPROVE)
        assert _state(joined.family.family_id) == "working"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()


def test_recovered_beats_presumed_lost() -> None:
    """A recovered signal (first installment) wins over accrued silence."""
    joined = _stalled_family(
        stalled_days_ago=_NURTURE.cold_after_days + 10,
        funding_state=FundingState.FIRST_INSTALLMENT_RECEIVED,
    )
    _, log = _drive(joined)
    now = datetime.now(UTC)
    try:
        for d in (1, 3, 5, 7, 9):
            log.log_contact_outcome(
                family_id=joined.family.family_id,
                channel=ContactChannel.CALL,
                disposition=ContactDisposition.NO_ANSWER,
                human="rep",
                created_at=now - timedelta(days=d),
            )
        assert _state(joined.family.family_id) == "recovered"
    finally:
        app.dependency_overrides.clear()
        deps.reset_observability_log()

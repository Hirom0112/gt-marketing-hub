"""Producer side of the close-loop: POST a contact outcome + confirm presumed-lost.

The deriver READS contact-outcome / lost events; these endpoints are how a rep
WRITES them. ``POST /families/{id}/contact-outcome`` logs a structured call result
(the 'log a call outcome' a rep never had); ``POST /families/{id}/presumed-lost-
confirm`` is the human-confirm gate (``requires_human_confirm``) that turns an
auto-SURFACED presumed-lost family into a recorded LOST — fail-closed: it refuses a
family the silence rule has NOT surfaced, so the system never drops a warm lead.
Both are owner-scoped through the single IDOR clamp (INV-5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import InMemoryFamilyRepository, JoinedFamily
from app.data.synthetic import SyntheticDataset
from app.main import app

client = TestClient(app)

AGENT_A = UUID("a0000000-0000-4000-8000-000000000001")
AGENT_B = UUID("a0000000-0000-4000-8000-000000000002")


def _family(*, assigned_rep_id: UUID | None = None) -> JoinedFamily:
    now = datetime.now(UTC)
    family = FamilyRecord(
        family_id=uuid4(),
        display_name="Close-loop Fixture",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=Stage.APPLY,
        stalled_since=now - timedelta(days=30),
        funding_state=FundingState.NONE,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=now - timedelta(days=60),
        assigned_rep_id=assigned_rep_id,
    )
    return JoinedFamily(
        family=family, lead=None, app_form=None, enrollment_forms=None, community_profile=None
    )


def _mount(joined: JoinedFamily) -> None:
    repo = InMemoryFamilyRepository(SyntheticDataset(families=[joined.family]))
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()


def _teardown() -> None:
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def test_log_contact_outcome_records_and_echoes_state() -> None:
    """A logged no-answer is persisted and the response carries the derived state."""
    joined = _family()
    fid = joined.family.family_id
    _mount(joined)
    try:
        resp = client.post(
            f"/families/{fid}/contact-outcome",
            json={"channel": "call", "disposition": "no_answer"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["family_id"] == str(fid)
        assert body["disposition"] == "no_answer"
        # One no-answer is not yet presumed-lost; the family is aged ⇒ cold.
        assert body["recovery_state"] == "cold"
    finally:
        _teardown()


def test_fifth_no_answer_flips_to_presumed_lost() -> None:
    """Five no-answer outcomes within the window flip the derived state."""
    joined = _family()
    fid = joined.family.family_id
    _mount(joined)
    try:
        states = []
        for _ in range(5):
            resp = client.post(
                f"/families/{fid}/contact-outcome",
                json={"channel": "call", "disposition": "no_answer"},
            )
            assert resp.status_code == 201
            states.append(resp.json()["recovery_state"])
        # The 5th attempt crosses the threshold (after_attempts=5).
        assert states[-1] == "presumed_lost"
        assert states[3] != "presumed_lost"
    finally:
        _teardown()


def test_confirm_presumed_lost_transitions_to_lost() -> None:
    """Confirming a surfaced presumed-lost family records LOST (the human gate)."""
    joined = _family()
    fid = joined.family.family_id
    _mount(joined)
    try:
        for _ in range(5):
            client.post(
                f"/families/{fid}/contact-outcome",
                json={"channel": "call", "disposition": "no_answer"},
            )
        confirm = client.post(
            f"/families/{fid}/presumed-lost-confirm",
            json={"reason": "family confirmed they enrolled elsewhere"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["recovery_state"] == "lost"
        # And it sticks on the read path.
        detail = client.get(f"/families/{fid}")
        assert detail.json()["deal_view"]["recovery_state"] == "lost"
    finally:
        _teardown()


def test_confirm_refuses_a_family_not_presumed_lost() -> None:
    """Fail-closed: a family the silence rule has NOT surfaced cannot be dropped."""
    joined = _family()  # aged ⇒ cold, but no contact outcomes ⇒ not presumed-lost
    fid = joined.family.family_id
    _mount(joined)
    try:
        confirm = client.post(
            f"/families/{fid}/presumed-lost-confirm",
            json={"reason": "trying to drop early"},
        )
        assert confirm.status_code == 409, confirm.text
        # Untouched: still on the active board.
        assert client.get(f"/families/{fid}").json()["deal_view"]["recovery_state"] == "cold"
    finally:
        _teardown()


def test_confirm_requires_non_blank_reason() -> None:
    """A confirmed-lost must record WHY — a blank reason is a 422."""
    joined = _family()
    fid = joined.family.family_id
    _mount(joined)
    try:
        for _ in range(5):
            client.post(
                f"/families/{fid}/contact-outcome",
                json={"channel": "call", "disposition": "no_answer"},
            )
        resp = client.post(f"/families/{fid}/presumed-lost-confirm", json={"reason": "   "})
        assert resp.status_code == 422
    finally:
        _teardown()


def test_unknown_family_is_404() -> None:
    """Posting an outcome to an unknown family is a clean 404, not a 500."""
    _mount(_family())
    try:
        resp = client.post(
            f"/families/{uuid4()}/contact-outcome",
            json={"channel": "sms", "disposition": "no_reply"},
        )
        assert resp.status_code == 404
    finally:
        _teardown()


def test_agent_cannot_log_for_a_foreign_family() -> None:
    """IDOR clamp: an agent posting to a family it does not own gets a 404."""
    joined = _family(assigned_rep_id=AGENT_A)
    fid = joined.family.family_id
    _mount(joined)
    try:
        resp = client.post(
            f"/families/{fid}/contact-outcome",
            json={"channel": "call", "disposition": "no_answer"},
            headers={"X-Demo-Role": "agent", "X-Demo-Agent-Id": str(AGENT_B)},
        )
        assert resp.status_code == 404, resp.text
        # The rightful owner can.
        ok = client.post(
            f"/families/{fid}/contact-outcome",
            json={"channel": "call", "disposition": "no_answer"},
            headers={"X-Demo-Role": "agent", "X-Demo-Agent-Id": str(AGENT_A)},
        )
        assert ok.status_code == 201
    finally:
        _teardown()

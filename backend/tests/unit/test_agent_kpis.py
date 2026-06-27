"""Agent personal-KPI aggregation endpoint (D-14; REDESIGN_PLAN R4 task 2).

``GET /enrollment/agent-kpis?window={day|week|month|all}&owner=`` returns the
agent's OWN performance over a time window — Leads Assigned, Contacts Made,
Follow-Ups Completed, Appointments Booked, Applications Started, Applications
Completed, Conversion Rate (= funded ÷ assigned). Each is a PURE aggregation over
already-logged facts (assignment timestamps, the contact-outcome log, app_form
state, funded state) — no new applicant data (INV-1), owner-scoped (INV-5).

STRICT-TDD (CLAUDE §4.1): this asserts EXACT values on a hand-seeded fixture
(red-first numeric targets), that ``window=`` narrows the result, and that an agent
can never read another agent's book (the IDOR clamp).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.data.models import (
    AppForm,
    FamilyRecord,
    FundingState,
    SeamStatus,
    Stage,
)
from app.data.repository import InMemoryFamilyRepository, JoinedFamily
from app.data.synthetic import SyntheticDataset
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt

client = TestClient(app)

AGENT_A = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1 closer (Riley Carter)
AGENT_B = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2 setter (Jordan Avery)


def _operator_headers(agent_id: UUID) -> dict[str, str]:
    """A signed operator JWT for ``agent_id`` (B1 — the verified successor to the
    deleted client-supplied role header)."""
    token = mint_jwt(role="operator", agent_id=agent_id, secret=TEST_JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


# A fixed "now" anchor. The endpoint reads wall-clock now; we date every fact a few
# days BEFORE the real now so the windows (computed off the request's now) bracket
# them deterministically regardless of the exact second the test runs.
_NOW = datetime.now(UTC)


def _days_ago(n: int) -> datetime:
    return _NOW - timedelta(days=n)


def _family(
    *,
    assigned_rep_id: UUID,
    assigned_days_ago: int,
    funding_state: FundingState = FundingState.NONE,
    app_form: AppForm | None = None,
) -> JoinedFamily:
    fid = uuid4()
    family = FamilyRecord(
        family_id=fid,
        display_name="KPI Fixture",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=Stage.APPLY,
        stalled_since=_days_ago(40),
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=_days_ago(90),
        assigned_rep_id=assigned_rep_id,
        assigned_at=_days_ago(assigned_days_ago),
        app_form_id=None if app_form is None else app_form.app_form_id,
    )
    bound_app = None if app_form is None else app_form.model_copy(update={"family_id": fid})
    return JoinedFamily(
        family=family,
        lead=None,
        app_form=bound_app,
        enrollment_forms=None,
        community_profile=None,
    )


def _app_form(*, started_days_ago: int, submitted_days_ago: int | None) -> AppForm:
    """An application: created (started) at one offset, optionally submitted later."""
    return AppForm(
        app_form_id=uuid4(),
        family_id=uuid4(),  # rebound to the family in `_family`
        created_at=_days_ago(started_days_ago),
        submitted_at=None if submitted_days_ago is None else _days_ago(submitted_days_ago),
    )


def _seed() -> list[JoinedFamily]:
    """A deterministic book for AGENT_A (+ one AGENT_B family that must never leak).

    AGENT_A's four families, chosen so day/week/month/all windows differ:
    - fam1: assigned 2d ago, FUNDED, app started 2d ago + submitted 1d ago.
    - fam2: assigned 5d ago, app started 5d ago, not submitted.
    - fam3: assigned 20d ago (outside week, inside month), funded, app started 20d
      ago + submitted 19d ago.
    - fam4: assigned 100d ago (outside month, inside all), app started 100d ago.
    """
    a_app1 = _app_form(started_days_ago=2, submitted_days_ago=1)
    a_app2 = _app_form(started_days_ago=5, submitted_days_ago=None)
    a_app3 = _app_form(started_days_ago=20, submitted_days_ago=19)
    a_app4 = _app_form(started_days_ago=100, submitted_days_ago=None)
    fams = [
        _family(
            assigned_rep_id=AGENT_A,
            assigned_days_ago=2,
            funding_state=FundingState.FUNDED,
            app_form=a_app1,
        ),
        _family(assigned_rep_id=AGENT_A, assigned_days_ago=5, app_form=a_app2),
        _family(
            assigned_rep_id=AGENT_A,
            assigned_days_ago=20,
            funding_state=FundingState.FUNDED,
            app_form=a_app3,
        ),
        _family(assigned_rep_id=AGENT_A, assigned_days_ago=100, app_form=a_app4),
        # AGENT_B family — owner-scope must keep it out of AGENT_A's KPIs.
        _family(assigned_rep_id=AGENT_B, assigned_days_ago=1, funding_state=FundingState.FUNDED),
    ]
    return fams


def _mount(fams: list[JoinedFamily]) -> dict[str, JoinedFamily]:
    repo = InMemoryFamilyRepository(
        SyntheticDataset(
            families=[j.family for j in fams],
            app_forms=[j.app_form for j in fams if j.app_form is not None],
        )
    )
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()
    return {str(j.family.family_id): j for j in fams}


def _seed_outcomes(fams: list[JoinedFamily]) -> None:
    """Log contact outcomes for AGENT_A's families at controlled offsets.

    Designed so day/week/month windows narrow Contacts/Follow-Ups/Appointments:
    - fam1: appointment_scheduled 0d ago; follow_up_needed 6d ago.
    - fam2: reached 3d ago; follow_up_needed 25d ago.
    - fam3: appointment_scheduled 18d ago.
    - fam4: no_answer 2d ago (a contact attempt, no follow-up/appointment).
    AGENT_B's family gets an appointment 0d ago that must NEVER count for AGENT_A.
    """
    from app.observability.log_store import ContactChannel, ContactDisposition

    log = deps.get_observability_log()
    a = fams  # positional
    fam1, fam2, fam3, fam4, famB = a[0], a[1], a[2], a[3], a[4]

    def out(fid: UUID, disp: ContactDisposition, days_ago: int) -> None:
        log.log_contact_outcome(
            family_id=fid,
            channel=ContactChannel.CALL,
            disposition=disp,
            human="rep",
            created_at=_days_ago(days_ago),
        )

    out(fam1.family.family_id, ContactDisposition.APPOINTMENT_SCHEDULED, 0)
    out(fam1.family.family_id, ContactDisposition.FOLLOW_UP_NEEDED, 6)
    out(fam2.family.family_id, ContactDisposition.REACHED, 3)
    out(fam2.family.family_id, ContactDisposition.FOLLOW_UP_NEEDED, 25)
    out(fam3.family.family_id, ContactDisposition.APPOINTMENT_SCHEDULED, 18)
    out(fam4.family.family_id, ContactDisposition.NO_ANSWER, 2)
    out(famB.family.family_id, ContactDisposition.APPOINTMENT_SCHEDULED, 0)


def _teardown() -> None:
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def _get(window: str, *, agent_id: UUID = AGENT_A) -> dict:
    headers = _operator_headers(agent_id)
    resp = client.get(f"/enrollment/agent-kpis?window={window}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_all_time_kpis_exact() -> None:
    """window=all aggregates every AGENT_A fact (the unbounded baseline)."""
    fams = _seed()
    _mount(fams)
    _seed_outcomes(fams)
    try:
        body = _get("all")
        # 4 families assigned to AGENT_A.
        assert body["leads_assigned"] == 4
        # 6 AGENT_A contact outcomes total (B's appointment excluded by owner-scope).
        assert body["contacts_made"] == 6
        # follow_up_needed: fam1 (6d) + fam2 (25d) = 2.
        assert body["follow_ups_completed"] == 2
        # appointment_scheduled: fam1 (0d) + fam3 (18d) = 2.
        assert body["appointments_booked"] == 2
        # app started: all four families have an app_form.
        assert body["applications_started"] == 4
        # app completed (submitted_at set): fam1 + fam3 = 2.
        assert body["applications_completed"] == 2
        # funded ÷ assigned = 2 / 4 = 0.5.
        assert body["conversion_rate"] == 0.5
    finally:
        _teardown()


def test_week_window_narrows() -> None:
    """window=week (7 days) keeps only facts inside the trailing week."""
    fams = _seed()
    _mount(fams)
    _seed_outcomes(fams)
    try:
        body = _get("week")
        # Assigned within 7d: fam1 (2d) + fam2 (5d) = 2.
        assert body["leads_assigned"] == 2
        # Contacts within 7d: fam1 appt(0) + fam1 follow(6) + fam2 reached(3)
        #   + fam4 no_answer(2) = 4.  (fam2 follow at 25d and fam3 appt at 18d are out.)
        assert body["contacts_made"] == 4
        # Follow-ups within 7d: fam1 (6d) only = 1.
        assert body["follow_ups_completed"] == 1
        # Appointments within 7d: fam1 (0d) only = 1.
        assert body["appointments_booked"] == 1
        # Apps started within 7d: fam1 (2d) + fam2 (5d) = 2.
        assert body["applications_started"] == 2
        # Apps completed within 7d: fam1 (submitted 1d) = 1.
        assert body["applications_completed"] == 1
        # funded assigned-in-window ÷ assigned-in-window = 1 (fam1) / 2 = 0.5.
        assert body["conversion_rate"] == 0.5
    finally:
        _teardown()


def test_month_window_between_week_and_all() -> None:
    """window=month (30 days) includes the 20d facts but excludes the 100d ones."""
    fams = _seed()
    _mount(fams)
    _seed_outcomes(fams)
    try:
        body = _get("month")
        # Assigned within 30d: fam1(2) + fam2(5) + fam3(20) = 3 (fam4 at 100d out).
        assert body["leads_assigned"] == 3
        # Appointments within 30d: fam1(0) + fam3(18) = 2.
        assert body["appointments_booked"] == 2
        # funded in-window: fam1 + fam3 = 2; assigned in-window = 3 ⇒ 2/3.
        assert body["conversion_rate"] == round(2 / 3, 4)
    finally:
        _teardown()


def test_conversion_rate_is_zero_when_no_assigned_in_window() -> None:
    """No assigned leads in a window ⇒ conversion_rate is 0.0 (no divide-by-zero)."""
    fams = _seed()
    _mount(fams)
    _seed_outcomes(fams)
    try:
        body = _get("day")  # nothing assigned within 1 day for AGENT_A (closest is 2d).
        assert body["leads_assigned"] == 0
        assert body["conversion_rate"] == 0.0
    finally:
        _teardown()


def test_owner_scope_blocks_cross_agent_read() -> None:
    """An agent principal is clamped to its OWN book — it can never read another's.

    AGENT_B passes ``owner=AGENT_A`` but the clamp ignores the client owner for an
    agent role, so the response reflects AGENT_B's single funded family, never
    AGENT_A's four (the IDOR defense, INV-5).
    """
    fams = _seed()
    _mount(fams)
    _seed_outcomes(fams)
    try:
        # AGENT_B asks for AGENT_A's book explicitly; the clamp must ignore it.
        headers = _operator_headers(AGENT_B)
        resp = client.get(f"/enrollment/agent-kpis?window=all&owner={AGENT_A}", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # AGENT_B has exactly ONE family (its own), funded ⇒ 1/1 conversion.
        assert body["leads_assigned"] == 1
        assert body["appointments_booked"] == 1  # B's own appointment, not A's.
        assert body["conversion_rate"] == 1.0
    finally:
        _teardown()

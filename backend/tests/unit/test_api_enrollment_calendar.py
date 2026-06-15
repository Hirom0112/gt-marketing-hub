"""Calendar + work-queue recency API tests (S9 W3; ARCHITECTURE.md §6).

Boots the FastAPI app over the SAME fixed-seed synthetic dataset the app is
seeded from (`DEFAULT_FAMILY_COUNT`/`DEFAULT_SEED`, `synthetic.generate`), so
every expectation is exact and deterministic (CLAUDE.md §4.1) — mirroring the
sibling `test_api_enrollment_read.py` style.

Covered:
- `GET /enrollment/calendar?month=YYYY-MM` — families whose apply_date
  (`app_form.submitted_at` else spine `created_at`) falls in the requested month,
  each `{family_id, display_name, apply_date, current_stage, contact_status}`,
  sorted ascending by apply_date. Out-of-month families are excluded; a bad
  `month` ⇒ 422; an empty month ⇒ `entries: []`. `contact_status` is composed in
  the API layer (now + audit log + params), same as Wave 2.
- `GET /work-queue` — each ranked row now carries `contact_status` +
  `last_contact_at`, composed in the API layer, with the existing ordering/score
  contract intact (the sibling test still passes).

Deterministic without a local `params/params.yaml` (gitignored): the app loads
the committed `params/params.example.yaml` via the `get_params` dependency, and
this test recomputes its expectations from that same committed file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.contact_log import last_contact_at
from app.core.contact_status import ContactStatus, derive_contact_status
from app.core.params import Params, load_params
from app.data.models import FamilyRecord, FundingState, SeamStatus, Stage
from app.data.repository import (
    DEFAULT_FAMILY_COUNT,
    DEFAULT_SEED,
    InMemoryFamilyRepository,
    JoinedFamily,
)
from app.data.synthetic import SyntheticDataset
from app.main import app
from app.observability.log_store import DecisionAction, InMemoryObservabilityLog

client = TestClient(app)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

_VALID_STATUSES = {s.value for s in ContactStatus}


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _seeded() -> InMemoryFamilyRepository:
    return InMemoryFamilyRepository.seeded(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)


def _apply_date(joined: JoinedFamily) -> datetime | None:
    """Mirror the deal-view apply_date rule: submitted_at else spine created_at."""
    apply_date = joined.app_form.submitted_at if joined.app_form is not None else None
    if apply_date is None:
        apply_date = joined.family.created_at
    return apply_date


def _stall_date(
    joined: JoinedFamily,
    *,
    log: InMemoryObservabilityLog,
    now: datetime,
    params: Params,
) -> datetime:
    """Mirror the A-16 stall-date precedence chain (document order):

    1. ``family.stalled_since``
    2. ``last_contact_at(log, family_id)``
    3. ``created_at + overdue_days``
    4. ``created_at``
    """
    family = joined.family
    if family.stalled_since is not None:
        return family.stalled_since
    contacted_at = last_contact_at(log, family.family_id)
    if contacted_at is not None:
        return contacted_at
    created_at = family.created_at or now
    return created_at + timedelta(days=params.enrollment.contact.overdue_days)


def _make_joined(
    *,
    created_at: datetime | None = None,
    stalled_since: datetime | None = None,
    funding_state: FundingState = FundingState.NONE,
) -> JoinedFamily:
    """A minimal JoinedFamily fixture for the stall-date precedence tests."""
    family = FamilyRecord(
        family_id=uuid4(),
        display_name="Fixture Family",
        primary_contact_synthetic_email="synthetic@example.test",
        current_stage=Stage.APPLY,
        stalled_since=stalled_since,
        funding_state=funding_state,
        attribution_source="referral",
        attribution_utm={},
        crm_seam_status=SeamStatus.UNSYNCED,
        created_at=created_at,
    )
    return JoinedFamily(
        family=family,
        lead=None,
        app_form=None,
        enrollment_forms=None,
        community_profile=None,
    )


def _approved_contact(log: InMemoryObservabilityLog, family_id: UUID) -> None:
    """Log a proposal + approve decision so ``last_contact_at`` resolves."""
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow="enrollment_draft",
        schema_version="1",
        payload={"action": "email", "body": "hi"},
        family_id=family_id,
    )
    log.log_decision(proposal_id=proposal_id, human="operator", action=DecisionAction.APPROVE)


def _expected_in_month(month: str) -> list[JoinedFamily]:
    """The seeded families whose stall_date falls in `month`, sorted by stall_date.

    Grouping/anchoring moved from apply_date to the A-16 derived stall_date
    (S11 W1). The expectation is recomputed against the empty default log (no
    approved outbounds), so the precedence reduces to stalled_since →
    created_at+overdue_days → created_at across the fixed-seed cohort.
    """
    year_s, month_s = month.split("-")
    year, mon = int(year_s), int(month_s)
    repo = _seeded()
    log = InMemoryObservabilityLog()
    params = _params()
    now = datetime.now(UTC)
    in_month = [
        joined
        for joined in repo.list_joined()
        if (sd := _stall_date(joined, log=log, now=now, params=params)).year == year
        and sd.month == mon
    ]
    return sorted(
        in_month,
        key=lambda j: _stall_date(j, log=log, now=now, params=params),
    )


def _most_recent_stall_month() -> str:
    """The YYYY-MM of the most-recent stall_date across the seeded cohort."""
    repo = _seeded()
    log = InMemoryObservabilityLog()
    params = _params()
    now = datetime.now(UTC)
    latest = max(_stall_date(j, log=log, now=now, params=params) for j in repo.list_joined())
    return f"{latest.year:04d}-{latest.month:02d}"


def test_calendar_returns_in_month_entries_sorted() -> None:
    """A populated month returns only its families, sorted ascending by stall_date."""
    # Anchor moved to the A-16 derived stall_date (S11 W1); pick a month that is
    # populated under that anchor (the most-recent stall_date's month).
    month = _most_recent_stall_month()
    resp = client.get("/enrollment/calendar", params={"month": month})
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == month

    entries = body["entries"]
    expected = _expected_in_month(month)
    assert len(entries) == len(expected)
    assert len(entries) > 0  # the most-recent-stall month is populated in the fixed seed.

    # Only in-month families appear (exact id set match).
    assert {e["family_id"] for e in entries} == {str(j.family.family_id) for j in expected}

    # Sorted ascending by stall_date.
    stall_dates = [datetime.fromisoformat(e["stall_date"]) for e in entries]
    assert stall_dates == sorted(stall_dates)

    # Each entry carries the full contract + a valid composed contact_status.
    repo = _seeded()
    by_id = {j.family.family_id: j for j in repo.list_joined()}
    for entry in entries:
        assert set(entry) >= {
            "family_id",
            "display_name",
            "stall_date",
            "apply_date",
            "current_stage",
            "contact_status",
            "value",
            "score",
        }
        joined = by_id[UUID(entry["family_id"])]
        assert entry["display_name"] == joined.family.display_name
        assert entry["current_stage"] == joined.family.current_stage.value
        assert entry["contact_status"] in _VALID_STATUSES


def test_calendar_empty_month_returns_empty_entries() -> None:
    """A month with no apply_dates returns `entries: []` (not an error)."""
    month = "2024-01"  # well before the seed window — no families apply here.
    assert _expected_in_month(month) == []
    resp = client.get("/enrollment/calendar", params={"month": month})
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == month
    assert body["entries"] == []


def test_calendar_bad_month_format_is_422() -> None:
    """A malformed `month` query param is rejected with 422 (validation)."""
    for bad in ["2025-13", "2025/11", "nov-2025", "2025", "25-11", "2025-1"]:
        resp = client.get("/enrollment/calendar", params={"month": bad})
        assert resp.status_code == 422, bad


def test_calendar_missing_month_resolves_to_most_recent_stall_month() -> None:
    """Omitting `month` resolves to the most-recent stall_date's month, non-empty (S11 W1)."""
    deps.reset_observability_log()
    try:
        resp = client.get("/enrollment/calendar")
        assert resp.status_code == 200
        body = resp.json()
        # Echoes the RESOLVED month so the client can read it back.
        assert body["month"] == _most_recent_stall_month()
        # The surface opens non-empty (the resolved month is, by construction,
        # the month of the most-recent stall_date — at least one family lands there).
        assert len(body["entries"]) > 0
    finally:
        deps.reset_observability_log()


def test_stall_date_tier1_stalled_since() -> None:
    """Tier 1: an explicit `stalled_since` is used as the stall_date."""
    params = _params()
    now = datetime(2026, 6, 15, tzinfo=UTC)
    log = InMemoryObservabilityLog()
    stalled = datetime(2026, 3, 1, tzinfo=UTC)
    joined = _make_joined(created_at=datetime(2026, 1, 1, tzinfo=UTC), stalled_since=stalled)
    assert _stall_date(joined, log=log, now=now, params=params) == stalled


def test_stall_date_tier2_last_contact() -> None:
    """Tier 2: no stalled_since but an approved outbound ⇒ last_contact_at is used."""
    params = _params()
    now = datetime(2026, 6, 15, tzinfo=UTC)
    log = InMemoryObservabilityLog()
    joined = _make_joined(created_at=datetime(2026, 1, 1, tzinfo=UTC), stalled_since=None)
    _approved_contact(log, joined.family.family_id)
    expected = last_contact_at(log, joined.family.family_id)
    assert expected is not None
    assert _stall_date(joined, log=log, now=now, params=params) == expected


def test_stall_date_tier3_created_plus_overdue() -> None:
    """Tier 3: uncontacted, aged ⇒ created_at + overdue_days."""
    params = _params()
    now = datetime(2026, 6, 15, tzinfo=UTC)
    log = InMemoryObservabilityLog()  # empty: no contact.
    created = datetime(2026, 1, 1, tzinfo=UTC)
    joined = _make_joined(created_at=created, stalled_since=None)
    expected = created + timedelta(days=params.enrollment.contact.overdue_days)
    assert _stall_date(joined, log=log, now=now, params=params) == expected


def test_calendar_groups_by_stall_date_not_apply_date() -> None:
    """A family whose apply_date is in month A but stall_date is in month B appears in B."""
    # apply_date here is created_at (no app_form): Jan 2026 (month A). An explicit
    # stalled_since in Apr 2026 (month B) moves the family into month B, not A.
    params = _params()
    now = datetime(2026, 6, 15, tzinfo=UTC)
    log = InMemoryObservabilityLog()
    joined = _make_joined(
        created_at=datetime(2026, 1, 10, tzinfo=UTC),
        stalled_since=datetime(2026, 4, 20, tzinfo=UTC),
    )
    apply_date = _apply_date(joined)
    assert apply_date is not None and apply_date.month == 1  # month A
    sd = _stall_date(joined, log=log, now=now, params=params)
    assert sd.month == 4  # month B — the family belongs to month B, not month A.

    # Drive it through a single-family repo to confirm the route groups by stall_date.
    repo = InMemoryFamilyRepository(SyntheticDataset(families=[joined.family]))
    original = app.dependency_overrides.get(deps.get_repository)
    app.dependency_overrides[deps.get_repository] = lambda: repo
    deps.reset_observability_log()
    try:
        # In month A (apply_date's month) the family does NOT appear.
        resp_a = client.get("/enrollment/calendar", params={"month": "2026-01"})
        assert resp_a.status_code == 200
        assert resp_a.json()["entries"] == []
        # In month B (stall_date's month) it DOES appear.
        resp_b = client.get("/enrollment/calendar", params={"month": "2026-04"})
        assert resp_b.status_code == 200
        ids_b = {e["family_id"] for e in resp_b.json()["entries"]}
        assert str(joined.family.family_id) in ids_b
    finally:
        if original is None:
            app.dependency_overrides.pop(deps.get_repository, None)
        else:
            app.dependency_overrides[deps.get_repository] = original
        deps.reset_observability_log()


def test_calendar_entries_carry_value_and_score() -> None:
    """Each entry carries value > 0 and a score consistent with score_family."""
    from app.api.families import _work_queue_family
    from app.core.work_queue import score_family, value

    params = _params()
    month = _most_recent_stall_month()
    resp = client.get("/enrollment/calendar", params={"month": month})
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) > 0

    repo = _seeded()
    by_id = {j.family.family_id: j for j in repo.list_joined()}
    for entry in entries:
        joined = by_id[UUID(entry["family_id"])]
        wqf = _work_queue_family(joined, params)
        assert entry["value"] == value(wqf, params)
        assert entry["value"] > 0
        # score's stall-recency term reads `now` (the route pins its own), so a
        # millisecond of clock drift between the route and this recompute is
        # expected for stalled families — assert consistency to a tight tolerance.
        assert entry["score"] == pytest.approx(score_family(wqf, params), abs=1e-6)


def test_calendar_composes_contact_status_from_log() -> None:
    """An approved outbound ⇒ the entry's contact_status is followed_up (composed).

    Approving an outbound also sets the family's stall_date to ``last_contact_at``
    (now, ≈ the demo month) via the A-16 precedence, so the family is grouped into
    the current month — which is where we query for it.
    """
    params = _params()
    # A non-funded family with NO explicit stalled_since, so the approved-outbound
    # last_contact_at wins the precedence chain (tier 2) and anchors it to `now`.
    repo = _seeded()
    sample = next(
        j.family
        for j in repo.list_joined()
        if j.family.funding_state is not FundingState.FUNDED and j.family.stalled_since is None
    )
    now = datetime.now(UTC)
    target_month = f"{now.year:04d}-{now.month:02d}"

    deps.reset_observability_log()
    log = deps.get_observability_log()
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow="enrollment_draft",
        schema_version="1",
        payload={"action": "email", "body": "hi"},
        family_id=sample.family_id,
    )
    log.log_decision(proposal_id=proposal_id, human="operator", action=DecisionAction.APPROVE)
    try:
        resp = client.get("/enrollment/calendar", params={"month": target_month})
        assert resp.status_code == 200
        entry = next(e for e in resp.json()["entries"] if e["family_id"] == str(sample.family_id))
        stamped = last_contact_at(log, sample.family_id)
        assert stamped is not None
        expected_status = derive_contact_status(
            created_at=sample.created_at,
            last_contact_at=stamped,
            now=datetime.now(UTC),
            funded=sample.funding_state is FundingState.FUNDED,
            params=params,
        )
        assert entry["contact_status"] == expected_status.value
        assert entry["contact_status"] == ContactStatus.FOLLOWED_UP.value
    finally:
        deps.reset_observability_log()


def test_work_queue_rows_carry_recency() -> None:
    """`GET /work-queue` rows now include a valid contact_status + last_contact_at."""
    resp = client.get("/work-queue")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == DEFAULT_FAMILY_COUNT

    # Existing contract: every row still carries identity + score components, and
    # the list is still ordered by descending score.
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)

    for item in items:
        assert "contact_status" in item
        assert item["contact_status"] in _VALID_STATUSES
        assert "last_contact_at" in item  # present (may be None when uncontacted).


def test_work_queue_recency_reflects_approved_contact() -> None:
    """Approving an outbound for a family flips its work-queue row to followed_up."""
    repo = _seeded()
    sample = next(f for f in repo.list_families() if f.funding_state is not FundingState.FUNDED)

    deps.reset_observability_log()
    log = deps.get_observability_log()
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow="enrollment_draft",
        schema_version="1",
        payload={"action": "email", "body": "hi"},
        family_id=sample.family_id,
    )
    log.log_decision(proposal_id=proposal_id, human="operator", action=DecisionAction.APPROVE)
    try:
        resp = client.get("/work-queue")
        assert resp.status_code == 200
        row = next(r for r in resp.json() if r["family_id"] == str(sample.family_id))
        assert row["contact_status"] == ContactStatus.FOLLOWED_UP.value
        assert row["last_contact_at"] is not None
    finally:
        deps.reset_observability_log()

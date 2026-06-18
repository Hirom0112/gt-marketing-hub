"""Leads-by-agent-per-day calendar API tests (DECISIONS.md D-3; ARCHITECTURE.md §6).

Drives `GET /enrollment/leads-calendar` over a small, hand-built in-memory cohort
(the same store-seam override pattern as `test_api_enrollment_calendar.py`): a set
of families seeded across a couple of intake days and owning agents, so every
per-day / per-agent / unowned count is exact and deterministic (CLAUDE.md §4.1).

Covered:
- per-day per-agent counts + the unowned (unassigned-pool) count + the day total;
- month resolution when `month` is omitted (the most-recent intake month);
- an out-of-month family is excluded;
- a malformed `month` ⇒ 422.

Read-only (INV-2): the route only reads + aggregates, never writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.sales_agents import SALES_AGENTS
from app.data.models import FamilyRecord, LeadsNew, ProductInterest, Stage
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset
from app.main import app

client = TestClient(app)

_AGENT_1 = SALES_AGENTS[0]  # Riley Carter
_AGENT_2 = SALES_AGENTS[1]  # Jordan Avery


def _family(*, assigned_rep_id: UUID | None) -> FamilyRecord:
    return FamilyRecord(
        family_id=uuid4(),
        display_name="The Fixture Family",
        primary_contact_synthetic_email="fixture@example.invalid",
        current_stage=Stage.INTEREST,
        assigned_rep_id=assigned_rep_id,
        attribution_source="referral",
        attribution_utm={},
    )


def _lead(family_id: UUID, *, created_at: datetime) -> LeadsNew:
    return LeadsNew(
        lead_id=uuid4(),
        family_id=family_id,
        synthetic_first_name="Jordan",
        synthetic_last_name="Fixture",
        synthetic_email="fixture@example.invalid",
        synthetic_phone="555-0100",
        source="referral",
        product_interest=ProductInterest.CAMPUS,
        grade_interest="3",
        region="Northeast",
        created_at=created_at,
    )


def _seed(specs: list[tuple[UUID | None, datetime]]) -> SyntheticDataset:
    """Build a dataset from (assigned_rep_id, intake_created_at) tuples."""
    families: list[FamilyRecord] = []
    leads: list[LeadsNew] = []
    for rep_id, created in specs:
        fam = _family(assigned_rep_id=rep_id)
        families.append(fam)
        leads.append(_lead(fam.family_id, created_at=created))
    return SyntheticDataset(families=families, leads=leads)


@pytest.fixture
def _override_repo() -> Iterator[None]:
    """Install a repo override + clean it up (restores any prior override)."""
    original = app.dependency_overrides.get(deps.get_repository)
    yield
    if original is None:
        app.dependency_overrides.pop(deps.get_repository, None)
    else:
        app.dependency_overrides[deps.get_repository] = original


def _install(ds: SyntheticDataset) -> None:
    repo = InMemoryFamilyRepository(ds)
    app.dependency_overrides[deps.get_repository] = lambda: repo


def test_leads_calendar_per_day_per_agent_and_unowned(_override_repo: None) -> None:
    """A seeded day yields exact per-agent chips + an unowned count + a day total."""
    may_10 = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
    may_11 = datetime(2026, 5, 11, 9, 0, tzinfo=UTC)
    ds = _seed(
        [
            (_AGENT_1.agent_id, may_10),
            (_AGENT_1.agent_id, may_10),
            (_AGENT_2.agent_id, may_10),
            (None, may_10),  # unowned intake pool
            (_AGENT_1.agent_id, may_11),
        ]
    )
    _install(ds)

    resp = client.get("/enrollment/leads-calendar", params={"month": "2026-05"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-05"

    days = {d["day"]: d for d in body["days"]}
    assert set(days) == {10, 11}  # zero-lead days omitted

    d10 = days[10]
    assert d10["unowned_count"] == 1
    assert d10["total"] == 4
    by_agent = {a["agent_id"]: a for a in d10["agents"]}
    assert by_agent[str(_AGENT_1.agent_id)]["count"] == 2
    assert by_agent[str(_AGENT_1.agent_id)]["synthetic_name"] == _AGENT_1.synthetic_name
    assert by_agent[str(_AGENT_2.agent_id)]["count"] == 1
    # Agents sorted ascending by synthetic_name (Jordan Avery before Riley Carter).
    assert [a["synthetic_name"] for a in d10["agents"]] == sorted(
        a["synthetic_name"] for a in d10["agents"]
    )

    d11 = days[11]
    assert d11["total"] == 1
    assert d11["unowned_count"] == 0
    assert d11["agents"][0]["agent_id"] == str(_AGENT_1.agent_id)
    assert d11["agents"][0]["count"] == 1


def test_leads_calendar_resolves_most_recent_month_when_omitted(_override_repo: None) -> None:
    """Omitting `month` resolves to the most-recent intake month, non-empty (D-3)."""
    ds = _seed(
        [
            (_AGENT_1.agent_id, datetime(2026, 3, 5, tzinfo=UTC)),
            (_AGENT_2.agent_id, datetime(2026, 7, 18, tzinfo=UTC)),  # most recent
        ]
    )
    _install(ds)

    resp = client.get("/enrollment/leads-calendar")
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-07"
    assert len(body["days"]) == 1
    assert body["days"][0]["day"] == 18
    assert body["days"][0]["total"] == 1


def test_leads_calendar_excludes_out_of_month_family(_override_repo: None) -> None:
    """A family whose intake is in another month does not appear in the queried month."""
    ds = _seed(
        [
            (_AGENT_1.agent_id, datetime(2026, 5, 10, tzinfo=UTC)),  # in-month
            (_AGENT_1.agent_id, datetime(2026, 4, 10, tzinfo=UTC)),  # out-of-month
        ]
    )
    _install(ds)

    resp = client.get("/enrollment/leads-calendar", params={"month": "2026-05"})
    assert resp.status_code == 200
    body = resp.json()
    assert [d["day"] for d in body["days"]] == [10]
    assert body["days"][0]["total"] == 1  # the April family is excluded


def test_leads_calendar_bad_month_format_is_422(_override_repo: None) -> None:
    """A malformed `month` query param is rejected with 422 (reuses _MONTH_PATTERN)."""
    _install(_seed([(_AGENT_1.agent_id, datetime(2026, 5, 10, tzinfo=UTC))]))
    for bad in ["2026-13", "2026/05", "may-2026", "2026", "26-05", "2026-5"]:
        resp = client.get("/enrollment/leads-calendar", params={"month": bad})
        assert resp.status_code == 422, bad

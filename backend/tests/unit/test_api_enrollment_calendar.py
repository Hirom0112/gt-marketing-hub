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

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.core.contact_log import last_contact_at
from app.core.contact_status import ContactStatus, derive_contact_status
from app.core.params import Params, load_params
from app.data.models import FundingState
from app.data.repository import (
    DEFAULT_FAMILY_COUNT,
    DEFAULT_SEED,
    InMemoryFamilyRepository,
    JoinedFamily,
)
from app.main import app
from app.observability.log_store import DecisionAction

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


def _expected_in_month(month: str) -> list[JoinedFamily]:
    """The seeded families whose apply_date falls in `month`, sorted by apply_date."""
    year_s, month_s = month.split("-")
    year, mon = int(year_s), int(month_s)
    repo = _seeded()
    in_month = [
        joined
        for joined in repo.list_joined()
        if (ad := _apply_date(joined)) is not None and ad.year == year and ad.month == mon
    ]
    return sorted(in_month, key=lambda j: _apply_date(j))  # type: ignore[arg-type, return-value]


def test_calendar_returns_in_month_entries_sorted() -> None:
    """A populated month returns only its families, sorted ascending by apply_date."""
    month = "2025-11"
    resp = client.get("/enrollment/calendar", params={"month": month})
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == month

    entries = body["entries"]
    expected = _expected_in_month(month)
    assert len(entries) == len(expected)
    assert len(entries) > 0  # 2025-11 is populated in the fixed seed.

    # Only in-month families appear (exact id set match).
    assert {e["family_id"] for e in entries} == {str(j.family.family_id) for j in expected}

    # Sorted ascending by apply_date.
    apply_dates = [datetime.fromisoformat(e["apply_date"]) for e in entries]
    assert apply_dates == sorted(apply_dates)

    # Each entry carries the full contract + a valid composed contact_status.
    repo = _seeded()
    by_id = {j.family.family_id: j for j in repo.list_joined()}
    for entry in entries:
        assert set(entry) >= {
            "family_id",
            "display_name",
            "apply_date",
            "current_stage",
            "contact_status",
        }
        joined = by_id[__import__("uuid").UUID(entry["family_id"])]
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


def test_calendar_missing_month_is_422() -> None:
    """`month` is required — omitting it is a 422."""
    resp = client.get("/enrollment/calendar")
    assert resp.status_code == 422


def test_calendar_composes_contact_status_from_log() -> None:
    """An approved outbound ⇒ the entry's contact_status is followed_up (composed)."""
    params = _params()
    repo = _seeded()
    # A non-funded family with an apply_date in a known populated month.
    target_month = "2025-11"
    candidates = _expected_in_month(target_month)
    sample = next(
        j.family for j in candidates if j.family.funding_state is not FundingState.FUNDED
    )

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
        entry = next(
            e for e in resp.json()["entries"] if e["family_id"] == str(sample.family_id)
        )
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

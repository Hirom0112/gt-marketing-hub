"""Module 11 Phase 2 auto-flag tests — hot-family escalation + field-events proposal.

Two NEW Decision-Queue feeders wired alongside the existing budget>10% variance flag:

- HOT-FAMILY ESCALATION (``POST /work-queue/escalate-hot``): leadership-gated; sweeps the
  cohort and enqueues ONE open ``hot_family_escalation`` decision per family whose
  work-queue ``recoverable_now`` meets ``params.nurture.escalation.recoverable_now_min``.
  IDEMPOTENT by family_id (a re-run never duplicates an open item); a sub-threshold
  cohort flags nothing.
- FIELD & EVENTS PROPOSAL (``POST /field/events/proposal``): any authenticated principal;
  lands an open ``field_event_proposal`` decision on the ``field_events`` workstream,
  ``raised_by`` stamped from the verified principal.

Both hit the REAL main app, overriding only the decisions store (a fresh in-memory one)
and — for the hot-family sweep — the family repository (a tiny hand-built cohort so every
recoverable_now is deterministic). The autouse conftest principal shim verifies Bearer
tokens against the test secret, so a minted operator/leader JWT drives the real gate.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.data.decisions_store import InMemoryDecisionsStore
from app.data.models import FamilyRecord, LeadsNew, ProductInterest, Stage
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt


def _auth(role: str) -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _auth_sub(role: str, sub: str) -> dict[str, str]:
    """A signed ``role`` JWT with an EXPLICIT ``sub`` (so ``raised_by`` is deterministic)."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET, sub=sub)}"}


_ALICE = "11111111-1111-4111-8111-111111111111"


def _family(*, current_stage: Stage, stalled_since: datetime | None) -> FamilyRecord:
    """A minimal stalled FamilyRecord (the only fields the work-queue scorer reads)."""
    return FamilyRecord(
        family_id=uuid4(),
        display_name="The Fixture Family",
        primary_contact_synthetic_email="fixture@example.invalid",
        current_stage=current_stage,
        stalled_since=stalled_since,
        attribution_source="referral",
        attribution_utm={},
    )


def _lead(family_id: UUID, *, num_children: int) -> LeadsNew:
    """A minimal lead carrying ``num_children`` (the value-term driver, A-23)."""
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
        num_children=num_children,
    )


def _install_repo(dataset: SyntheticDataset) -> None:
    """Override the family repository to a tiny hand-built cohort."""
    from app.main import app

    repo = InMemoryFamilyRepository(dataset)
    app.dependency_overrides[deps.get_repository] = lambda: repo


@pytest.fixture
def store() -> InMemoryDecisionsStore:
    """A fresh in-memory decisions store per test (full isolation)."""
    return InMemoryDecisionsStore()


@pytest.fixture
def client(store: InMemoryDecisionsStore) -> Iterator[TestClient]:
    """The main app with the decisions store overridden; clears any repo override after."""
    from app.main import app

    app.dependency_overrides[deps.get_decisions_store] = lambda: store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(deps.get_decisions_store, None)
        app.dependency_overrides.pop(deps.get_repository, None)


# ----------------------------------------------------------------- hot-family escalation
def test_hot_family_above_threshold_flags_exactly_one_and_is_idempotent(
    client: TestClient,
) -> None:
    """A family above the escalation bar produces exactly ONE open decision; a re-run
    creates no duplicate (idempotent by family_id), and the row carries the nurture
    workstream + a derived question."""
    now = datetime.now(UTC)
    hot = _family(current_stage=Stage.TUITION, stalled_since=now - timedelta(hours=1))
    dataset = SyntheticDataset(
        families=[hot],
        leads=[_lead(hot.family_id, num_children=3)],
    )
    _install_repo(dataset)

    first = client.post("/work-queue/escalate-hot", headers=_auth("leader"))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["scanned"] == 1
    assert body["flagged"] == [str(hot.family_id)]
    assert body["already_open"] == []

    # Exactly one OPEN escalation decision, with the expected shape.
    queue = client.get("/decisions", headers=_auth("leader")).json()
    hot_rows = [d for d in queue if d["source"] == "hot_family_escalation"]
    assert len(hot_rows) == 1
    row = hot_rows[0]
    assert row["state"] == "open"
    assert row["workstream"] == "nurture"
    assert row["priority"] == "urgent"
    assert str(hot.family_id) in row["question"]
    assert row["payload"]["family_id"] == str(hot.family_id)

    # Re-run: idempotent — no new decision, the family flips to already_open.
    second = client.post("/work-queue/escalate-hot", headers=_auth("leader"))
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["flagged"] == []
    assert body2["already_open"] == [str(hot.family_id)]

    queue2 = client.get("/decisions", headers=_auth("leader")).json()
    assert len([d for d in queue2 if d["source"] == "hot_family_escalation"]) == 1


def test_below_threshold_family_flags_nothing(client: TestClient) -> None:
    """A cold, low-value, long-stalled family is below the bar ⇒ no escalation decision."""
    now = datetime.now(UTC)
    cold = _family(current_stage=Stage.INTEREST, stalled_since=now - timedelta(days=60))
    dataset = SyntheticDataset(
        families=[cold],
        leads=[_lead(cold.family_id, num_children=1)],
    )
    _install_repo(dataset)

    resp = client.post("/work-queue/escalate-hot", headers=_auth("leader"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scanned"] == 1
    assert body["flagged"] == []
    assert body["already_open"] == []

    queue = client.get("/decisions", headers=_auth("leader")).json()
    assert [d for d in queue if d["source"] == "hot_family_escalation"] == []


def test_escalate_hot_operator_forbidden(client: TestClient) -> None:
    """An operator hitting the leadership escalation sweep → 403 (admin/leader-gated)."""
    _install_repo(SyntheticDataset(families=[]))
    resp = client.post("/work-queue/escalate-hot", headers=_auth("operator"))
    assert resp.status_code == 403, resp.text


# -------------------------------------------------------------------- field-events proposal
def test_event_proposal_enqueues_field_events_decision(client: TestClient) -> None:
    """A field-events proposal lands as an OPEN decision on the field_events workstream,
    raised_by stamped from the verified principal (never the body)."""
    resp = client.post(
        "/field/events/proposal",
        headers=_auth_sub("operator", _ALICE),
        json={
            "name": "Fall college fair booth",
            "recommendation": "Approve the booth before the fall fair.",
            "budget_ask": 3500.0,
            "due_date": "2026-09-01",
            "priority": "urgent",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "open"
    assert body["source"] == "field_event_proposal"
    assert body["workstream"] == "field_events"
    assert body["budget_ask"] == 3500.0
    assert body["due_date"] == "2026-09-01"
    assert body["priority"] == "urgent"
    assert "Fall college fair booth" in body["question"]
    assert body["raised_by"] == _ALICE

    # The leader sees it on the open queue.
    queue = client.get("/decisions", headers=_auth("leader")).json()
    assert any(d["source"] == "field_event_proposal" for d in queue)


def test_event_proposal_invalid_priority_unprocessable(client: TestClient) -> None:
    """An out-of-set priority → 422 (validated against the canonical PRIORITIES)."""
    resp = client.post(
        "/field/events/proposal",
        headers=_auth("operator"),
        json={"name": "Booth", "priority": "whenever"},
    )
    assert resp.status_code == 422, resp.text

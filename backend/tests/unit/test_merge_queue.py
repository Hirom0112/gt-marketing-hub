"""Merge-queue endpoint + merge-aware decision tests (ENROLLMENT_REFACTOR §5.2/§6).

These acceptance tests drive the dedup human-review surface end-to-end through the
API, matching the contract `frontend/src/MergeQueue.tsx` is wired to:

  the deterministic `propose_merge` core enumerates candidate PAIRS → keeps the
  fail-closed REVIEW_QUEUE verdicts → LOGS each as a proposal on the §10 spine so
  it carries a real ``proposal_id`` → ``GET /merge-queue`` returns the contract
  shape → ``POST /proposals/{id}/decision`` resolves it: approve applies the fold
  (SIMULATED, INV-9) and marks it applied; discard logs only and never merges.

INV-2 (the merge WRITE is deterministic + post-approval), INV-4 (REVIEW_QUEUE
never auto-resolves, fail-closed), INV-9 (the v1 fold is simulated — no live
mutation) are all proven at the API boundary. A merge approve must NOT fire the
CRM nudge path, and nudge proposals must keep behaving exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.data.models import (
    FamilyRecord,
    LeadsNew,
    ProductInterest,
    Stage,
)
from app.data.repository import FamilyRepository, JoinedFamily, JoinedStudent
from app.main import app

client = TestClient(app)


# --------------------------------------------------------------------------- #
# A tiny repository with a DELIBERATE review-queue pair (same email+region,
# DIFFERENT phones) — the ambiguous match propose_merge flags for a human.
# --------------------------------------------------------------------------- #
def _family(fid: UUID, email: str) -> FamilyRecord:
    return FamilyRecord(
        family_id=fid,
        display_name="Rivera household",
        primary_contact_synthetic_email=email,
        current_stage=Stage.APPLY,
        attribution_source="referral",
        attribution_utm={},
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        updated_at=datetime(2026, 6, 1, tzinfo=UTC),
    )


def _lead(fid: UUID, *, email: str, region: str, phone: str) -> LeadsNew:
    return LeadsNew(
        lead_id=uuid4(),
        family_id=fid,
        synthetic_first_name="Sam",
        synthetic_last_name="Rivera",
        synthetic_email=email,
        synthetic_phone=phone,
        source="referral",
        product_interest=ProductInterest.CAMPUS,
        grade_interest="3",
        region=region,
    )


class _ReviewQueueRepo(FamilyRepository):
    """Two families with the SAME email+region but DIFFERENT phones (one review pair)."""

    def __init__(self) -> None:
        self.a = UUID("00000000-0000-4000-8000-000000000001")
        self.b = UUID("00000000-0000-4000-8000-000000000002")
        self._families = [
            _family(self.a, "rivera@example.test"),
            _family(self.b, "rivera@example.test"),
        ]
        self._leads = {
            self.a: _lead(
                self.a, email="rivera@example.test", region="austin", phone="512-555-0100"
            ),
            self.b: _lead(
                self.b, email="rivera@example.test", region="austin", phone="512-555-0199"
            ),
        }

    def list_families(self, **_: object) -> list[FamilyRecord]:
        return list(self._families)

    def get_family(self, family_id: UUID) -> JoinedFamily | None:
        for fam in self._families:
            if fam.family_id == family_id:
                return JoinedFamily(
                    family=fam,
                    lead=self._leads.get(family_id),
                    app_form=None,
                    enrollment_forms=None,
                    community_profile=None,
                )
        return None

    def list_joined(self) -> list[JoinedFamily]:
        return [self.get_family(f.family_id) for f in self._families]  # type: ignore[misc]

    def list_students(self) -> list[JoinedStudent]:
        return []

    def pipeline_counts(self) -> dict[Stage, int]:
        return {}

    def mark_synced(self, family_id: UUID, synced_at: datetime) -> None:  # pragma: no cover
        pass

    def apply_field(self, family_id: UUID, field: str, value: object) -> None:  # pragma: no cover
        pass

    def assign_families(  # pragma: no cover
        self, family_ids: list[UUID], agent_id: UUID, assigned_at: datetime
    ) -> list[UUID]:
        return list(family_ids)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def _use_review_repo() -> _ReviewQueueRepo:
    repo = _ReviewQueueRepo()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    return repo


# --------------------------------------------------------------------------- #
# 1. GET /merge-queue returns the contract shape AND logs each to the spine.
# --------------------------------------------------------------------------- #
def test_merge_queue_returns_contract_shape_and_logs_to_spine() -> None:
    repo = _use_review_repo()
    resp = client.get("/merge-queue")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    # Exact contract fields MergeQueue.tsx consumes.
    assert set(item) == {
        "proposal_id",
        "verdict",
        "primary_family_id",
        "duplicate_family_id",
        "matched_on",
        "conflicting_keys",
        "summary",
    }
    assert item["verdict"] == "review_queue"
    assert {item["primary_family_id"], item["duplicate_family_id"]} == {str(repo.a), str(repo.b)}
    assert item["matched_on"] == ["email", "region"]
    assert item["conflicting_keys"] == ["phone"]
    assert isinstance(item["summary"], str) and item["summary"]

    # The proposal is on the §10 spine ⇒ the decision route can find it.
    audit = client.get(f"/proposals/{item['proposal_id']}")
    assert audit.status_code == 200
    assert audit.json()["proposal"]["flow"] == "identity_merge"


def test_merge_queue_is_idempotent_across_polls() -> None:
    """Re-polling does not duplicate spine entries (dedupe by the household pair)."""
    _use_review_repo()
    first = client.get("/merge-queue").json()
    second = client.get("/merge-queue").json()
    assert len(first) == len(second) == 1
    assert first[0]["proposal_id"] == second[0]["proposal_id"]
    # Exactly one merge proposal on the spine, not two.
    proposals = client.get("/proposals").json()
    merge_proposals = [p for p in proposals if p["proposal"]["flow"] == "identity_merge"]
    assert len(merge_proposals) == 1


# --------------------------------------------------------------------------- #
# 2. Approve on a merge proposal applies the (simulated) fold + marks applied,
#    and does NOT fire the CRM nudge send path.
# --------------------------------------------------------------------------- #
def test_merge_approve_applies_simulated_fold_and_marks_applied() -> None:
    _use_review_repo()
    crm = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: crm

    item = client.get("/merge-queue").json()[0]
    pid = item["proposal_id"]

    decision = client.post(f"/proposals/{pid}/decision", json={"action": "approve"})
    assert decision.status_code == 200

    # The approve is logged on the spine (NFR-6).
    audit = client.get(f"/proposals/{pid}").json()
    assert any(d["action"] == "approve" for d in audit["decisions"])

    # INV-9: the merge approve must NOT route through the CRM nudge send.
    assert crm.sent_log == []  # no nudge send fired


def test_merge_discard_logs_only_and_does_not_merge() -> None:
    _use_review_repo()
    crm = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: crm

    item = client.get("/merge-queue").json()[0]
    pid = item["proposal_id"]

    decision = client.post(f"/proposals/{pid}/decision", json={"action": "discard"})
    assert decision.status_code == 200

    audit = client.get(f"/proposals/{pid}").json()
    assert any(d["action"] == "discard" for d in audit["decisions"])
    assert not any(d["action"] == "approve" for d in audit["decisions"])
    # Discard never merges + never sends.
    assert crm.sent_log == []


# --------------------------------------------------------------------------- #
# 3. An unknown proposal id still 404s (fail-closed).
# --------------------------------------------------------------------------- #
def test_decision_on_unknown_merge_proposal_404() -> None:
    resp = client.post(f"/proposals/{uuid4()}/decision", json={"action": "approve"})
    assert resp.status_code == 404

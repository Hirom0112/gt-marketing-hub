"""Seed-to-HubSpot endpoint tests (S10 W3; ARCH §7.1; INV-2/INV-9).

Acceptance tests for ``POST /enrollment/families/{id}/seed`` — the deterministic
post-decision route that pushes a synthetic family into the CRM through the
``CRMAdapter`` seam (mode-agnostic: the simulated adapter records, the live
adapter writes to HubSpot). The route:

  1. loads the family (404 if unknown);
  2. calls ``adapter.push_family(record)`` — the SOLE caller of the seam's write
     op on this path (INV-2: a deterministic route, never ``app/ai``);
  3. advances ``crm_synced_at`` and re-derives the §4.7 seam (derive-and-return,
     A-7) so the seam flips ``unsynced → synced``;
  4. returns ``{simulated, deal_id, stage, seam_status}`` — ``deal_id`` is the
     adapter's ``recorded_id`` (the live HubSpot deal id under ``CRM_MODE=live``).

The adapter is OVERRIDDEN here with a ``SimulatedCRMAdapter`` (records, never
sends — INV-9) so no live write happens in the suite; a separate mock proves the
route calls ``push_family`` on the deterministic path.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import CRMAdapter, SendResult, SimulatedCRMAdapter, SyncResult
from app.api import deps
from app.core.seam import MirrorState
from app.data.models import FamilyRecord, SeamStatus, Stage
from app.data.repository import InMemoryFamilyRepository
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + no stray dependency overrides per test."""
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def _repo() -> InMemoryFamilyRepository:
    return deps.get_repository()  # type: ignore[return-value]


def _an_unsynced_family() -> FamilyRecord:
    """A seeded family whose seam column is not yet synced (the seed candidate)."""
    for family in _repo().list_families(seam_status=SeamStatus.UNSYNCED):
        return family
    raise AssertionError("no seeded unsynced family")


class _RecordingAdapter(CRMAdapter):
    """A CRMAdapter that records the push and returns a fixed live-shaped deal id."""

    def __init__(self) -> None:
        self.pushed: list[FamilyRecord] = []

    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        self.pushed.append(family_record)
        return SyncResult(
            simulated=False,
            recorded_id="live-deal-99887766",
            contact_id="live-contact-11223344",
            family_id=family_record.family_id,
            stage=family_record.current_stage,
        )

    def read_mirror(self, family_id: UUID) -> MirrorState:
        # After a push the mirror reflects the pushed family's stage.
        for record in self.pushed:
            if record.family_id == family_id:
                return MirrorState(stage=record.current_stage, mirror_updated_at=record.updated_at)
        return MirrorState(stage=None, mirror_updated_at=None)

    def send_message(self, message: dict[str, Any]) -> SendResult:
        return SendResult(simulated=False, recorded_id="note-1", channel="email")


def test_seed_pushes_family_and_returns_live_deal_id() -> None:
    """Seed calls push_family on the deterministic path and returns the live deal id."""
    family = _an_unsynced_family()
    adapter = _RecordingAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(f"/enrollment/families/{family.family_id}/seed")

    assert resp.status_code == 200
    body = resp.json()
    # push_family was called once, on the deterministic post-decision route.
    assert len(adapter.pushed) == 1
    assert adapter.pushed[0].family_id == family.family_id
    # The returned deal id is the adapter's recorded_id (the live HubSpot id).
    assert body["deal_id"] == "live-deal-99887766"
    # The contact id is surfaced too — the cockpit deep-links the live Contact.
    assert body["contact_id"] == "live-contact-11223344"
    assert body["simulated"] is False
    assert body["stage"] == family.current_stage.value


def test_seed_flips_seam_unsynced_to_synced() -> None:
    """After the push, crm_synced_at advances and the §4.7 seam re-derives synced."""
    family = _an_unsynced_family()
    adapter = _RecordingAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(f"/enrollment/families/{family.family_id}/seed")

    assert resp.status_code == 200
    assert resp.json()["seam_status"] == SeamStatus.SYNCED.value


def test_seed_mode_agnostic_with_simulated_adapter() -> None:
    """With the simulated recorder the route records (never sends) and still syncs."""
    family = _an_unsynced_family()
    adapter = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(f"/enrollment/families/{family.family_id}/seed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["simulated"] is True  # simulated recorder, no live send (INV-9)
    assert len(adapter.pushed_log) == 1  # the push was recorded
    assert body["seam_status"] == SeamStatus.SYNCED.value
    assert body["deal_id"]  # a recorded id is returned even when simulated


def test_seed_unknown_family_404() -> None:
    """Seeding an unknown family ⇒ 404 (the family must exist to be pushed)."""
    adapter = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter
    resp = client.post(f"/enrollment/families/{uuid4()}/seed")
    assert resp.status_code == 404


def test_seed_stage_is_the_family_stage() -> None:
    """The returned stage equals the family's current funnel stage (the pushed stage)."""
    family = _an_unsynced_family()
    adapter = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(f"/enrollment/families/{family.family_id}/seed")

    assert resp.status_code == 200
    assert resp.json()["stage"] in {s.value for s in Stage}
    assert resp.json()["stage"] == family.current_stage.value

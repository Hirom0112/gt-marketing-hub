"""A2 CRM-as-truth incremental poll — the composition-root endpoints (acceptance).

Acceptance tests for ``POST /crm/sync/poll`` + ``GET /crm/sync/status`` — the
watermark-driven CRM-as-truth reconcile. The poll pulls every record modified since
the persisted per-program watermark (window-chunked under the 10k cap), reconciles
each into the program store (CRM-authoritative for stage/owner via the §4.7 seam),
advances the watermark, and LOGS each proposal + decision (NFR-6). A second
immediate poll is a near-noop (the watermark advanced ⇒ nothing newer).

The wiring is exercised end-to-end on synthetic data through dependency overrides:
a seeded in-memory family repo, a seeded :class:`SimulatedCRMAdapter` whose mirror
holds a CRM-NEWER stage for a couple of families (so reconcile yields
``ACCEPT_MIRROR``), a fresh in-memory watermark store, and an in-memory audit log.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.core.seam import MirrorState
from app.data.models import Stage
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.data.watermark_store import InMemoryWatermarkStore
from app.main import app
from app.observability.log_store import InMemoryObservabilityLog

client = TestClient(app)

_SYNC_FLOW = "crm_sync_reconcile"


def _other_stage(stage: Stage) -> Stage:
    """A funnel stage that is NOT ``stage`` (so the mirror diverges from local)."""
    return next(s for s in Stage if s is not stage)


class _Fixtures:
    """The overridden deps for one poll test — repo, adapter, watermark store, log."""

    def __init__(self) -> None:
        self.repo: FamilyRepository = InMemoryFamilyRepository.seeded()
        self.adapter = SimulatedCRMAdapter()
        self.store = InMemoryWatermarkStore()
        self.log = InMemoryObservabilityLog()
        # Seed a CRM-NEWER mirror stage on the first two families with a usable
        # local `updated_at`: the mirror's stage differs from local AND its
        # `mirror_updated_at` is strictly AFTER the local `updated_at`, so the §4.7
        # last-write-wins reconcile yields ACCEPT_MIRROR (the CRM is the source of
        # truth for stage). funding_state/owner are left None (not tracked) so ONLY
        # stage diverges. We record the expected adopted stages to assert on.
        self.expected: dict[str, Stage] = {}  # str(family_id) -> mirror stage
        seeded = 0
        for record in self.repo.list_families():
            if record.updated_at is None:
                continue
            mirror_stage = _other_stage(record.current_stage)
            self.adapter.seed_mirror(
                record.family_id,
                MirrorState(
                    stage=mirror_stage,
                    mirror_updated_at=record.updated_at + timedelta(hours=1),
                ),
            )
            self.expected[str(record.family_id)] = mirror_stage
            seeded += 1
            if seeded >= 2:
                break
        assert seeded >= 1, "fixture needs at least one family with a usable updated_at"


@pytest.fixture
def fx() -> Iterator[_Fixtures]:
    """Install the four overrides for the poll composition root; tear them down."""
    fixtures = _Fixtures()
    app.dependency_overrides[deps.get_repository] = lambda: fixtures.repo
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: fixtures.adapter
    app.dependency_overrides[deps.get_watermark_store] = lambda: fixtures.store
    app.dependency_overrides[deps.get_observability_log] = lambda: fixtures.log
    yield fixtures
    app.dependency_overrides.clear()


def test_poll_pulls_reconciles_and_adopts_crm_stage(fx: _Fixtures) -> None:
    """The poll pulls CRM-modified records and ADOPTS the CRM-newer stage (A2)."""
    resp = client.post("/crm/sync/poll")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pulled"] >= 1
    assert body["applied"] >= 1
    assert body["conflicts"] == 0
    assert body["unmatched"] == 0

    # The reconciled family's STORED stage now equals the CRM mirror stage (the
    # CRM-as-truth flip: stage is CRM-authoritative, ACCEPT_MIRROR adopted it).
    for fid_str, mirror_stage in fx.expected.items():
        from uuid import UUID

        joined = fx.repo.get_family(UUID(fid_str))
        assert joined is not None
        assert joined.family.current_stage is mirror_stage


def test_poll_advances_watermark_reflected_in_status(fx: _Fixtures) -> None:
    """The poll advances the watermark; GET /crm/sync/status reflects it (A2)."""
    # Before any poll the watermark is unset (a cold full backfill).
    status_before = client.get("/crm/sync/status").json()
    deal_row = next(o for o in status_before["objects"] if o["object_type"] == "deal")
    assert deal_row["watermark"] is None

    poll = client.post("/crm/sync/poll").json()
    assert poll["watermark"] is not None

    status_after = client.get("/crm/sync/status").json()
    deal_after = next(o for o in status_after["objects"] if o["object_type"] == "deal")
    assert deal_after["watermark"] == poll["watermark"]
    # The configured tunables are surfaced (INV-11 — read from params).
    assert status_after["chunk_days"] >= 1
    assert status_after["search_qps"] >= 1


def test_poll_logs_proposal_and_decision(fx: _Fixtures) -> None:
    """Each reconcile logs a proposal AND a decision to the §10 spine (NFR-6)."""
    client.post("/crm/sync/poll")

    proposals = [p for p in fx.log.list_proposals() if p.flow == _SYNC_FLOW]
    assert proposals, "expected at least one crm_sync reconcile proposal logged"
    # Each logged proposal carries a human decision (the audit join, NFR-6).
    audit = fx.log.get_audit(proposals[0].proposal_id)
    assert audit is not None
    assert audit.decisions, "the reconcile proposal must carry a logged decision"


def test_second_immediate_poll_is_noop(fx: _Fixtures) -> None:
    """A second immediate poll pulls nothing — the watermark already advanced (A2)."""
    first = client.post("/crm/sync/poll").json()
    assert first["pulled"] >= 1

    second = client.post("/crm/sync/poll").json()
    assert second["pulled"] == 0
    assert second["applied"] == 0
    # The watermark did not move backward (it stays at the first poll's value).
    assert second["watermark"] == first["watermark"]

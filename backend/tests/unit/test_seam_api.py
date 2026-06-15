"""Seam reconcile endpoint tests (FR-1.3/2.6; ARCH §4.7/§6; NFR-6; INV-4).

Acceptance tests for the S3 seam API — the §4.7 deriver + the FR-2.6 reconcile
flow surfaced over REST, human-gated and LOGGED:

  ``GET  /seam``                   — non-synced families (family_id + seam_status).
  ``POST /seam/{id}/reconcile``    — propose → apply (human-approved) → recompute,
                                     LOGGING the proposal + the approve decision to
                                     the §10 observability spine (NFR-6).

Fail-closed (INV-4): a flagged CONFLICT is NOT silently resolved — ``apply_reconcile``
returns ``applied=False`` and the seam stays ``conflict``. These tests prove that
surfaces over HTTP and that the reconcile is auditable via ``GET /proposals``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.data.models import SeamStatus
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


def _family_with_seam(status: SeamStatus):
    """A seeded family whose seam column is the given status (first match)."""
    for family in _repo().list_families(seam_status=status):
        return family
    raise AssertionError(f"no seeded family with seam_status={status}")


def test_seam_lists_only_non_synced_families() -> None:
    """GET /seam returns the unsynced/conflict cohort, never a synced family."""
    resp = client.get("/seam")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) > 0  # seeded data has a non-synced tail.
    statuses = {row["seam_status"] for row in rows}
    assert SeamStatus.SYNCED.value not in statuses
    assert statuses <= {SeamStatus.UNSYNCED.value, SeamStatus.CONFLICT.value}
    # Every listed family carries an id + its derived seam status.
    for row in rows:
        assert "family_id" in row and "seam_status" in row


def test_reconcile_unsynced_family_becomes_synced_and_is_logged() -> None:
    """An unsynced family reconciles to synced, with a logged proposal+decision."""
    family = _family_with_seam(SeamStatus.UNSYNCED)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["seam_status"] == SeamStatus.SYNCED.value

    # NFR-6: the reconcile is in the audit spine — a proposal with the approve
    # decision attached (proposal + decision both present).
    audit = client.get("/proposals")
    assert audit.status_code == 200
    proposals = audit.json()
    matches = [
        p
        for p in proposals
        if p["proposal"]["flow"] == "seam_reconcile"
        and p["proposal"]["family_id"] == str(family.family_id)
    ]
    assert len(matches) == 1
    entry = matches[0]
    assert len(entry["decisions"]) == 1
    assert entry["decisions"][0]["action"] == "approve"
    assert entry["decisions"][0]["human"] == "operator"


def test_reconcile_conflict_stays_conflict_fail_closed() -> None:
    """A conflict family is NOT silently resolved: applied=False, seam stays conflict."""
    family = _family_with_seam(SeamStatus.CONFLICT)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["seam_status"] == SeamStatus.CONFLICT.value

    # The conflict reconcile is still logged (the audit records the flag).
    audit = client.get("/proposals").json()
    matches = [
        p
        for p in audit
        if p["proposal"]["flow"] == "seam_reconcile"
        and p["proposal"]["family_id"] == str(family.family_id)
    ]
    assert len(matches) == 1


def test_reconcile_unknown_family_404() -> None:
    """Reconciling an unknown family is a clean 404."""
    from uuid import uuid4

    resp = client.post(f"/seam/{uuid4()}/reconcile")
    assert resp.status_code == 404

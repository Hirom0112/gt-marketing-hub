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

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.core.seam import MirrorState
from app.data.models import FundingState, SeamStatus, Stage
from app.data.repository import InMemoryFamilyRepository
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + CRM adapter + no stray dependency overrides per test."""
    deps.reset_observability_log()
    deps.reset_crm_adapter()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    deps.reset_crm_adapter()


def _repo() -> InMemoryFamilyRepository:
    return deps.get_repository()  # type: ignore[return-value]


def _family_with_seam(status: SeamStatus):
    """A seeded family whose seam column is the given status (first match)."""
    for family in _repo().list_families(seam_status=status):
        return family
    raise AssertionError(f"no seeded family with seam_status={status}")


def _override_seam_adapter(adapter: SimulatedCRMAdapter) -> None:
    """Route the seam endpoints at a test-controlled, seeded CRM adapter (R1)."""
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: adapter


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


# ===========================================================================
# R1 — the seam reads the REAL adapter mirror (multi-field), driven by seeded
# divergence so PUSH_LOCAL / FLAG_CONFLICT / SYNCED are exercised end-to-end
# through ``crm_adapter.read_mirror`` rather than a fabricated mirror.
# ===========================================================================


def test_matching_mirror_reads_synced_and_is_not_listed() -> None:
    """A mirror matching the DB record across every tracked field ⇒ synced (no row).

    Seeding the adapter mirror to mirror the family's stage + funding_state +
    owner at the same instant — with the DB record's ``crm_synced_at`` advanced so
    the record itself reads synced — makes ``read_mirror`` vs the record derive
    ``synced``: the family is NOT in ``GET /seam`` and ``reconcile`` is a no-op.
    """
    import dataclasses

    from app.data.repository import DEFAULT_FAMILY_COUNT, DEFAULT_SEED
    from app.data.synthetic import generate

    # Rebuild the seeded dataset (keeping every joined source row) with ONE family's
    # crm_synced_at advanced so the §4.7 deriver reads it as synced
    # (crm_synced_at >= updated_at). Seeded families otherwise carry a stale/None
    # synced marker, so the synced derivation needs this advance.
    seeded = generate(n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED)
    family = seeded.families[0]
    advanced_families = [
        (
            f.model_copy(update={"crm_synced_at": f.updated_at})
            if f.family_id == family.family_id
            else f
        )
        for f in seeded.families
    ]
    dataset = dataclasses.replace(seeded, families=advanced_families)
    repo = InMemoryFamilyRepository(dataset)
    app.dependency_overrides[deps.get_repository] = lambda: repo

    adapter = SimulatedCRMAdapter()
    owner = None if family.user_id is None else str(family.user_id)
    adapter.seed_mirror(
        family.family_id,
        MirrorState(
            stage=family.current_stage,
            mirror_updated_at=family.updated_at,
            funding_state=family.funding_state,
            owner=owner,
        ),
    )
    _override_seam_adapter(adapter)

    listed = {row["family_id"] for row in client.get("/seam").json()}
    assert str(family.family_id) not in listed

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["seam_status"] == SeamStatus.SYNCED.value


def test_db_newer_drift_proposes_push_local_through_adapter() -> None:
    """A DB-newer stage drift (empty/stale mirror) ⇒ unsynced ⇒ push_local applies.

    The DB record is unsynced (crm_synced_at < updated_at) and the adapter mirror
    holds nothing for it (a pending push), so ``read_mirror`` ⇒ unsynced ⇒
    ``propose_reconcile`` proposes PUSH_LOCAL and ``apply_reconcile`` syncs it.
    """
    family = _family_with_seam(SeamStatus.UNSYNCED)
    adapter = SimulatedCRMAdapter()  # empty mirror — nothing pushed for this family.
    _override_seam_adapter(adapter)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["seam_status"] == SeamStatus.SYNCED.value

    # Logged to the audit spine as a seam_reconcile proposal+approve decision.
    audit = client.get("/proposals").json()
    matches = [
        p
        for p in audit
        if p["proposal"]["flow"] == "seam_reconcile"
        and p["proposal"]["family_id"] == str(family.family_id)
    ]
    assert len(matches) == 1
    assert matches[0]["proposal"]["payload"]["direction"] == "push_local"


def test_owner_divergence_is_crm_authoritative_flag_conflict() -> None:
    """A divergent OWNER (CRM-authoritative) ⇒ flag_conflict, NOT push_local (R1).

    Even when the DB stage matches and recency would favor local, a mirror whose
    HubSpot ``owner`` differs from the DB owner is a genuine conflict the gate
    must flag (never silently overwrite a human edit) — fail-closed (INV-4).
    """
    family = next(iter(_repo().list_families()))
    adapter = SimulatedCRMAdapter()
    adapter.seed_mirror(
        family.family_id,
        MirrorState(
            stage=family.current_stage,
            mirror_updated_at=family.updated_at,
            funding_state=family.funding_state,
            # A HubSpot staff/user id that disagrees with the DB owner — the
            # CRM-authoritative divergence. (Distinct from any seeded user_id.)
            owner="hubspot-owner-9999-divergent",
        ),
    )
    _override_seam_adapter(adapter)

    # It surfaces in the non-synced list as a conflict.
    listed = {row["family_id"]: row["seam_status"] for row in client.get("/seam").json()}
    assert listed.get(str(family.family_id)) == SeamStatus.CONFLICT.value

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False  # fail-closed: never silently resolved.
    assert body["seam_status"] == SeamStatus.CONFLICT.value

    audit = client.get("/proposals").json()
    matches = [
        p
        for p in audit
        if p["proposal"]["flow"] == "seam_reconcile"
        and p["proposal"]["family_id"] == str(family.family_id)
    ]
    assert len(matches) == 1
    assert matches[0]["proposal"]["payload"]["direction"] == "flag_conflict"


def test_no_clear_winner_funding_divergence_flag_conflict() -> None:
    """A DB-authoritative field diverging with NO clear recency winner ⇒ conflict.

    A ``funding_state`` divergence at an equal mirror/local instant has no clear
    winner, so the §4.7 rule flags it as a conflict (not a plain push) — surfaced
    over HTTP through the adapter mirror, fail-closed.
    """
    family = next(iter(_repo().list_families()))
    # Pick a funding_state that differs from the family's so the mirror diverges.
    divergent_funding = next(fs for fs in FundingState if fs is not family.funding_state)
    adapter = SimulatedCRMAdapter()
    adapter.seed_mirror(
        family.family_id,
        MirrorState(
            stage=family.current_stage,
            mirror_updated_at=family.updated_at,  # equal instant ⇒ no clear winner.
            funding_state=divergent_funding,
            owner=None if family.user_id is None else str(family.user_id),
        ),
    )
    _override_seam_adapter(adapter)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["seam_status"] == SeamStatus.CONFLICT.value


def test_stage_drift_with_local_newer_proposes_push_local() -> None:
    """A diverging stage with the LOCAL side clearly newer ⇒ push_local (not conflict).

    A DB-authoritative stage divergence where local is strictly newer than the
    mirror is a plain pending push — the deriver reads unsynced and the reconcile
    pushes local, distinct from the no-clear-winner conflict path above.
    """
    from datetime import UTC, datetime

    family = next(iter(_repo().list_families()))
    older = datetime(2000, 1, 1, tzinfo=UTC)  # strictly before any synthetic updated_at.
    diverging_stage = next(s for s in Stage if s != family.current_stage)
    adapter = SimulatedCRMAdapter()
    adapter.seed_mirror(
        family.family_id,
        MirrorState(
            stage=diverging_stage,
            mirror_updated_at=older,  # mirror clearly older ⇒ local newer ⇒ push.
            funding_state=family.funding_state,
            owner=None if family.user_id is None else str(family.user_id),
        ),
    )
    _override_seam_adapter(adapter)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["seam_status"] == SeamStatus.SYNCED.value

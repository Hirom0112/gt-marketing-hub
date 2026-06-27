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
from tests.conftest import install_test_principal_override

client = TestClient(app)


# The per-test in-memory store. The reconcile now PERSISTS (R1), so the booted
# app's module-singleton repo would carry writes ACROSS tests; each test gets a
# FRESH seeded repo via a dependency override so isolation holds.
_active_repo: list[InMemoryFamilyRepository] = []


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """Fresh observability log + CRM adapter + a fresh seeded repo per test (R1)."""
    deps.reset_observability_log()
    deps.reset_crm_adapter()
    app.dependency_overrides.clear()
    # Re-assert the conftest token-aware principal shim wiped by the clear() above.
    install_test_principal_override()
    repo = InMemoryFamilyRepository.seeded()
    _active_repo[:] = [repo]
    app.dependency_overrides[deps.get_repository] = lambda: repo
    yield
    app.dependency_overrides.clear()
    _active_repo.clear()
    deps.reset_observability_log()
    deps.reset_crm_adapter()


def _repo() -> InMemoryFamilyRepository:
    # The repo the route will actually use this test — the override if a test
    # installed its own, else the fixture's fresh seeded repo.
    override = app.dependency_overrides.get(deps.get_repository)
    if override is not None:
        return override()  # type: ignore[return-value]
    return _active_repo[0]


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


# ===========================================================================
# R1 — PERSIST the reconcile result through the store seam (TODO.md R1). A
# push_local apply writes crm_synced_at back + re-pushes; a flagged conflict
# persists NOTHING (fail-closed). The idempotency fence reuses the seam freshness
# rule so a synced family does not re-push (no write loops).
# ===========================================================================


def test_push_local_persists_crm_synced_at_through_the_store() -> None:
    """A push_local reconcile PERSISTS crm_synced_at — a re-read shows the advance.

    The reconcile is no longer derive-and-return: after a PUSH_LOCAL apply the
    endpoint writes ``crm_synced_at`` back through the store seam. The persisted
    record advances (its seam derives ``synced`` on the next read) and a second
    reconcile is a clean no-op (the idempotency fence — local ``updated_at`` did
    not advance, so nothing re-pushes).
    """
    family = _family_with_seam(SeamStatus.UNSYNCED)
    before = family.crm_synced_at
    adapter = SimulatedCRMAdapter()  # empty mirror ⇒ pending push.
    _override_seam_adapter(adapter)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    assert resp.json()["applied"] is True

    # PERSISTED: the stored record's crm_synced_at advanced to updated_at.
    reloaded = _repo().get_family(family.family_id)
    assert reloaded is not None
    assert reloaded.family.crm_synced_at == family.updated_at
    assert reloaded.family.crm_synced_at != before

    # The push went through the adapter on the persisted advance.
    assert any(p.family_id == family.family_id for p in adapter.pushed_log)

    # Idempotency fence: a second reconcile re-reads the now-synced record + the
    # mirror the first push wrote, so it is a no-op — no second push.
    pushes_after_first = len(adapter.pushed_log)
    resp2 = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp2.status_code == 200
    assert resp2.json()["seam_status"] == SeamStatus.SYNCED.value
    assert len(adapter.pushed_log) == pushes_after_first


def test_conflict_persists_nothing_fail_closed() -> None:
    """A flagged conflict writes NOTHING — crm_synced_at unchanged, no push (INV-4).

    A CRM-authoritative owner divergence flags a conflict; the endpoint must not
    persist a sync or push the family — the store record is untouched until a human
    resolves it.
    """
    family = next(iter(_repo().list_families()))
    before = family.crm_synced_at
    adapter = SimulatedCRMAdapter()
    adapter.seed_mirror(
        family.family_id,
        MirrorState(
            stage=family.current_stage,
            mirror_updated_at=family.updated_at,
            funding_state=family.funding_state,
            owner="hubspot-owner-9999-divergent",
        ),
    )
    _override_seam_adapter(adapter)

    resp = client.post(f"/seam/{family.family_id}/reconcile")
    assert resp.status_code == 200
    assert resp.json()["applied"] is False

    reloaded = _repo().get_family(family.family_id)
    assert reloaded is not None
    assert reloaded.family.crm_synced_at == before  # nothing persisted.
    assert not any(p.family_id == family.family_id for p in adapter.pushed_log)

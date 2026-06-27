"""CRM-Ops data-quality view tests (C1) — ``GET /crm/ops``.

The endpoint COMPOSES the committed C1 cores over the active-program cohort —
A4 sync-parity (:func:`app.core.parity.compute_parity`, REUSED, not forked), the
auto data-quality queue (:func:`app.core.data_quality.build_dq_queue`),
UTM-health (:func:`app.core.utm_health.check_utm`), and the field-reliability
flags (:func:`app.core.field_reliability.field_flag`) — and raises the
data-confidence banner when overall parity drops below
``params.crm_ops.parity_floor``.

The cohort + mirror are injected through dependency overrides (a seeded in-memory
family repo + a :class:`SimulatedCRMAdapter`) so the parity is KNOWN and at least
one row is a genuine §4.7 CONFLICT:

  * a CONFLICT mirror (a diverging stage at an EQUAL instant ⇒ neither side
    clearly newer) on one family + an otherwise-empty mirror ⇒ overall parity
    below the floor ⇒ ``data_confidence_banner`` True, and the queue lists the
    conflict issue severity-ordered.
  * an EMPTY mirror with the floor pinned to 0.0 ⇒ no CONFLICT rows (plain
    unsynced is not a data-quality issue), well-formed synthetic UTMs, and no
    per-row low-trust field ⇒ an EMPTY ``dq_queue`` + no banner.

Auth mirrors ``GET /crm/status`` — gated only by ``Depends(get_principal)`` (any
authenticated seat); the no-token case must 401 (the S1 default-DENY). Fully
offline (INV-9): the simulated adapter seeds its mirror in memory, no live call.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.core.parity import compute_parity
from app.core.seam import MirrorState
from app.core.settings import Settings
from app.data.models import Stage
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.main import app
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt
from tests.conftest import install_test_principal_override

client = TestClient(app)


def _auth(role: str = "leader") -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT (B1)."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Clear dependency overrides around each test, restoring the auth shim."""
    app.dependency_overrides.clear()
    install_test_principal_override()
    yield
    app.dependency_overrides.clear()


def _install(repo: FamilyRepository, adapter: SimulatedCRMAdapter) -> None:
    """Bind the cohort + seam CRM adapter the CRM-Ops view reads (C1)."""
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: adapter


def _other_stage(stage: Stage) -> Stage:
    """A funnel stage that is NOT ``stage`` (so the mirror diverges from local)."""
    return next(s for s in Stage if s is not stage)


def test_crm_ops_surfaces_parity_dq_queue_and_field_flags() -> None:
    """Below-floor parity ⇒ banner + a severity-ordered queue with the conflict (C1)."""
    repo = InMemoryFamilyRepository.seeded()
    adapter = SimulatedCRMAdapter()

    # Seed ONE family with a genuine §4.7 CONFLICT: a diverging stage at an EQUAL
    # instant (neither side clearly newer ⇒ flag_conflict ⇒ CONFLICT). Every other
    # family's mirror stays empty ⇒ unsynced ⇒ overall parity 0.0 (none SYNCED).
    conflict_record = next(r for r in repo.list_families() if r.updated_at is not None)
    adapter.seed_mirror(
        conflict_record.family_id,
        MirrorState(
            stage=_other_stage(conflict_record.current_stage),
            mirror_updated_at=conflict_record.updated_at,
        ),
    )

    # The A4 reuse: the endpoint must match compute_parity over the SAME pairs.
    pairs = [(r, adapter.read_mirror(r.family_id)) for r in repo.list_families()]
    expected = compute_parity(pairs)
    floor = deps._params.crm_ops.parity_floor
    assert expected.overall < floor, "fixture must drive parity below the floor"

    _install(repo, adapter)
    resp = client.get("/crm/ops", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Parity is the A4 core's value over the same cohort (no fork).
    assert body["parity_overall"] == expected.overall
    assert body["parity_by_field"] == expected.by_field
    # Parity below the crm_ops floor ⇒ the cross-module data-confidence banner.
    assert body["data_confidence_banner"] is True

    # The queue carries the conflict, attributed to the seeded entity.
    dq = body["dq_queue"]
    assert dq, "a cohort with a CONFLICT row must produce a non-empty queue"
    conflicts = [i for i in dq if i["kind"] == "conflict"]
    assert any(i["entity_id"] == str(conflict_record.family_id) for i in conflicts)
    # Severity-ordered (conflict highest ⇒ lowest severity rank, sorted ascending).
    severities = [i["severity"] for i in dq]
    assert severities == sorted(severities)
    assert dq[0]["kind"] == "conflict"

    # The honest low-trust field list: every configured unreliable field is flagged.
    flags = {f["field"]: f["status"] for f in body["field_flags"]}
    for field_name in deps._params.crm_ops.unreliable_fields:
        assert flags[field_name] == "unreliable"

    # UTM-health is surfaced as an ok/broken aggregate.
    assert set(body["utm_health"]) >= {"ok", "broken", "broken_entities"}


def test_crm_ops_clean_cohort_has_empty_queue_and_tracks_floor() -> None:
    """No CONFLICT + well-formed UTMs ⇒ empty queue; banner tracks the floor (C1)."""
    repo = InMemoryFamilyRepository.seeded()
    # An EMPTY mirror ⇒ every family unsynced (NOT a conflict — a plain pending push
    # is not a data-quality issue) ⇒ no conflict issues; the synthetic UTMs are
    # well-formed; no per-row low-trust field ⇒ an EMPTY queue.
    adapter = SimulatedCRMAdapter()
    _install(repo, adapter)

    # Pin the floor to 0.0 so the (0.0-parity) cohort does NOT trip the banner —
    # the banner tracks the configured threshold, not a fixed cutoff.
    real = deps._params
    low = real.model_copy(update={"crm_ops": real.crm_ops.model_copy(update={"parity_floor": 0.0})})
    app.dependency_overrides[deps.get_params] = lambda: low

    resp = client.get("/crm/ops", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["dq_queue"] == []
    # 0.0 < 0.0 is False ⇒ no banner even at full out-of-sync.
    assert body["data_confidence_banner"] is False


def test_crm_ops_no_token_unauthorized() -> None:
    """No bearer token → 401 (the S1 default-DENY; CRM-Ops still needs a seat)."""
    # Pop the conftest admin-on-no-token shim and run the REAL verifier with the
    # test secret configured, so the missing-token path reaches the default-deny.
    app.dependency_overrides.pop(deps.get_principal, None)
    app.dependency_overrides[deps.get_settings_dep] = lambda: Settings(
        supabase_jwt_secret=TEST_JWT_SECRET
    )
    try:
        resp = client.get("/crm/ops")
        assert resp.status_code == 401, resp.text
    finally:
        install_test_principal_override()

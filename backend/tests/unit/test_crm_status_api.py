"""GET /crm/status parity + data-confidence banner (TODO_v2 §A4).

Extends the S14-W4 CRM seam window with the A4 sync-parity surface: the endpoint
now ALSO computes overall + per-field sync-parity across the active-program cohort
(reusing :func:`app.core.parity.compute_parity` over the SAME ``(record, mirror)``
pairing the seam endpoints use — ``repository.list_families`` + the seam CRM
adapter's ``read_mirror``) and raises a ``data_confidence_banner`` flag when
overall parity drops below ``params.data_confidence.min_parity``.

The cohort + mirror are injected through dependency overrides (a seeded in-memory
family repo + a :class:`SimulatedCRMAdapter`) so the parity is KNOWN:

  * an EMPTY adapter mirror ⇒ every family reads ``unsynced`` ⇒ overall parity 0.0
    ⇒ below the configured ``min_parity`` ⇒ ``data_confidence_banner`` True.
  * the SAME 0.0-parity cohort with ``min_parity`` pinned to 0.0 (a params
    override) ⇒ ``overall < min_parity`` is false ⇒ ``data_confidence_banner``
    False — the flag tracks the threshold, not a fixed cutoff.

Fully offline (INV-9): the simulated adapter does pure in-memory seeding, no live
call. The pre-existing pure-settings fields/behavior are unchanged (see
tests/unit/test_crm_status.py — those still pass against the default deps).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Clear dependency overrides around each test (test isolation)."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _install(repo: FamilyRepository, adapter: SimulatedCRMAdapter) -> None:
    """Bind the cohort + seam CRM adapter the parity computation reads (A4)."""
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: adapter


def test_parity_surfaced_and_banner_flag() -> None:
    """The endpoint surfaces parity + raises the banner when parity < min_parity (A4)."""
    min_parity = deps.get_params().data_confidence.min_parity
    assert 0.0 < min_parity <= 1.0, "fixture assumes a non-trivial parity floor"

    # --- Banner ON: an EMPTY mirror ⇒ every family unsynced ⇒ overall parity 0.0. ---
    repo = InMemoryFamilyRepository.seeded()
    empty_adapter = SimulatedCRMAdapter()  # nothing seeded ⇒ every read_mirror is empty
    _install(repo, empty_adapter)

    resp = client.get("/crm/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Parity is surfaced: an overall float and a per-field dict.
    assert data["parity_overall"] == 0.0
    assert isinstance(data["parity_by_field"], dict)
    assert set(data["parity_by_field"]) == {"stage", "funding_state", "owner"}
    # 0.0 < min_parity ⇒ the data-confidence banner is raised.
    assert data["data_confidence_banner"] is True
    # The pre-existing pure-settings fields are still present (no regression).
    assert "effective_mode" in data
    assert "kill_switch" in data

    # --- Banner OFF: the SAME 0.0-parity cohort, but min_parity pinned to 0.0. ---
    # The banner tracks the configured threshold, not a fixed cutoff: with
    # min_parity=0.0, `overall < min_parity` is never true ⇒ no banner.
    real = deps.get_params()
    low = real.model_copy(
        update={"data_confidence": real.data_confidence.model_copy(update={"min_parity": 0.0})}
    )
    app.dependency_overrides[deps.get_params] = lambda: low

    resp_ok = client.get("/crm/status")
    assert resp_ok.status_code == 200, resp_ok.text
    ok = resp_ok.json()
    assert ok["parity_overall"] == 0.0
    assert isinstance(ok["parity_by_field"], dict)
    # 0.0 < 0.0 is False ⇒ the banner is NOT raised.
    assert ok["data_confidence_banner"] is False

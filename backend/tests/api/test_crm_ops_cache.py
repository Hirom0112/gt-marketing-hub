"""Short-TTL single-flight cache for the LIVE CRM-Ops snapshots (perf; robustness).

Guards the rate-limit-storm fix: the per-program LIVE parity scan (and the overview's
aggregate reads) are memoized for ``params.crm_ops.snapshot_ttl_seconds`` and shared by
all concurrent/repeated callers, so a data-confidence banner (``GET /crm/status``) +
a CRM-Ops page load is ONE live ``read_mirror`` scan, not a storm. Two layers:

  * the generic :class:`TtlSingleFlightCache` in isolation, with an INJECTED clock — the
    single-flight (compute once within TTL), TTL-expiry recompute, and per-program keying
    contracts (no sleeping, no wall clock);
  * the wired endpoints, with a COUNTING ``SimulatedCRMAdapter`` — two loads within the
    TTL drive the live ``read_mirror`` exactly once per family, and ``GET /crm/status``
    shares the same cached parity as ``GET /crm/ops/sync-parity``.

Caching changes only recomputation, never the computed values or the honest source labels
(a cached LIVE read is still live). Fully offline (INV-9): the simulated adapter seeds its
mirror in memory, no live call.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.api import deps
from app.api._crm_ops_cache import TtlSingleFlightCache
from app.core.program import Program
from app.core.seam import MirrorState
from app.data.repository import InMemoryFamilyRepository
from app.main import app
from tests.conftest import install_test_principal_override

client = TestClient(app)


# ===========================================================================
# Unit — the generic cache contract with an injected clock (no wall clock).
# ===========================================================================
def test_single_flight_computes_once_within_ttl() -> None:
    """Two calls within the TTL ⇒ ``compute`` runs ONCE; both get the same value."""
    cache: TtlSingleFlightCache[int] = TtlSingleFlightCache(clock=lambda: 1000.0, register=False)
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    first = cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60)
    second = cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60)

    assert first == second == 1
    assert calls["n"] == 1, "the single-flight cache must not recompute within the TTL"


def test_recomputes_after_ttl_expiry() -> None:
    """Past the TTL the entry is stale ⇒ ``compute`` runs again (the live read refreshes)."""
    now = {"t": 1000.0}
    cache: TtlSingleFlightCache[int] = TtlSingleFlightCache(clock=lambda: now["t"], register=False)
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    assert cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60) == 1
    now["t"] += 61  # advance past the TTL
    assert cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60) == 2
    assert calls["n"] == 2


def test_distinct_programs_do_not_share_an_entry() -> None:
    """One program's snapshot must NEVER serve another — the cache is keyed by program."""
    cache: TtlSingleFlightCache[Program] = TtlSingleFlightCache(clock=lambda: 0.0, register=False)
    calls = {"n": 0}

    def make(program: Program) -> Program:
        calls["n"] += 1
        return program

    fall = cache.get_or_compute(
        Program.FALL_ENROLLMENT, lambda: make(Program.FALL_ENROLLMENT), ttl_seconds=60
    )
    camp = cache.get_or_compute(
        Program.SUMMER_CAMP, lambda: make(Program.SUMMER_CAMP), ttl_seconds=60
    )

    assert fall is Program.FALL_ENROLLMENT
    assert camp is Program.SUMMER_CAMP
    assert calls["n"] == 2, "distinct programs must each compute their own entry"


def test_reset_drops_cached_entries() -> None:
    """``reset`` clears every entry so the next call recomputes (the test-isolation seam)."""
    cache: TtlSingleFlightCache[int] = TtlSingleFlightCache(clock=lambda: 0.0, register=False)
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60)
    cache.reset()
    cache.get_or_compute(Program.FALL_ENROLLMENT, compute, ttl_seconds=60)
    assert calls["n"] == 2


# ===========================================================================
# Wired endpoints — the live read_mirror storm is collapsed to one scan.
# ===========================================================================
class _CountingAdapter(SimulatedCRMAdapter):
    """A :class:`SimulatedCRMAdapter` that COUNTS its ``read_mirror`` calls (the live read)."""

    def __init__(self) -> None:
        super().__init__()
        self.read_mirror_calls = 0

    def read_mirror(self, family_id: UUID) -> MirrorState:
        self.read_mirror_calls += 1
        return super().read_mirror(family_id)


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    install_test_principal_override()
    yield
    app.dependency_overrides.clear()


def _install(repo: InMemoryFamilyRepository, adapter: _CountingAdapter) -> None:
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_seam_crm_adapter_dep] = lambda: adapter


def test_two_loads_within_ttl_scan_the_mirror_once_per_family() -> None:
    """Two /crm/ops/sync-parity loads ⇒ read_mirror runs once per family (cache hit, no storm)."""
    repo = InMemoryFamilyRepository.seeded()
    adapter = _CountingAdapter()
    _install(repo, adapter)
    n = len(list(repo.list_families()))
    assert n > 0, "the seeded cohort must be non-empty for the assertion to bite"

    first = client.get("/crm/ops/sync-parity")
    second = client.get("/crm/ops/sync-parity")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # Cold load scans every family once; the second load is a pure cache hit (0 reads).
    assert adapter.read_mirror_calls == n
    # The cached LIVE read is still live — the value is identical across both loads.
    assert first.json()["parity_overall"] == second.json()["parity_overall"]


def test_status_banner_and_ops_share_one_parity_scan() -> None:
    """The /crm/status banner + a /crm/ops/sync-parity page load share ONE live scan."""
    repo = InMemoryFamilyRepository.seeded()
    adapter = _CountingAdapter()
    _install(repo, adapter)
    n = len(list(repo.list_families()))

    status = client.get("/crm/status")
    ops = client.get("/crm/ops/sync-parity")
    assert status.status_code == 200, status.text
    assert ops.status_code == 200, ops.text
    # The banner computed the scan; the page reused it (same program key) ⇒ n total reads.
    assert adapter.read_mirror_calls == n
    assert status.json()["parity_overall"] == ops.json()["parity_overall"]

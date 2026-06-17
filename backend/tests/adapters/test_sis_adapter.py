"""EnrollmentSystemAdapter contract — M0 interface + RosterRecord shape + SIS_MODE seam.

M0 builds ONLY the *agnostic* enrollment-system adapter contract (INV-9): the
``EnrollmentSystemAdapter`` interface and the normalized ``RosterRecord`` the M5
reconcile core consumes, plus the registry selector wired on a new ``SIS_MODE``
env seam (default ``simulate``; TECH_STACK §5). The concrete
``SimulatedSISAdapter`` impl + the synthetic roster generator are **M5, NOT M0**
(TODO.md M5) — so for M0 the selector fails **loud**, mirroring the established
``get_funding_signal_adapter`` / ``get_geo_sampling_adapter`` fail-loud pattern:

- ``SIS_MODE=simulate`` (default) ⇒ ``NotImplementedError`` (no SimulatedSISAdapter yet — M5).
- ``SIS_MODE=live`` ⇒ ``NotImplementedError`` (no LiveSISAdapter in v1).

This proves the env seam is **wired** even though no impl exists yet. The contract
test asserts the interface *shape* and that the selector is *resolvable by
SIS_MODE* — not that a working sim adapter returns records.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path

import pytest

from app.adapters.registry import get_enrollment_system_adapter
from app.adapters.sis.base import EnrollmentSystemAdapter, MatchAttrs, RosterRecord

# The committed example params (the loader's bare default is gitignored); ``parents[3]``
# from ``tests/adapters/`` is the repo root (the house pattern).
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_roster_record_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The M0 SIS adapter contract: RosterRecord shape, abstract interface, wired seam."""
    # --- RosterRecord carries the four normalized fields the reconcile core reads. ---
    record = RosterRecord(
        external_id="sis-001",
        match_attrs=MatchAttrs(email="parent@example.test", phone="+15555550100"),
        enrollment_status="confirmed",
        confirmed_at=None,
    )
    assert record.external_id == "sis-001"
    assert record.match_attrs.email == "parent@example.test"
    assert record.match_attrs.phone == "+15555550100"
    assert record.enrollment_status == "confirmed"
    assert record.confirmed_at is None

    # match_attrs email/phone are both optional (a roster row may carry only one).
    sparse = RosterRecord(
        external_id="sis-002",
        match_attrs=MatchAttrs(email=None, phone=None),
        enrollment_status="pending",
        confirmed_at=None,
    )
    assert sparse.match_attrs.email is None
    assert sparse.match_attrs.phone is None

    # --- EnrollmentSystemAdapter is an abstract interface (cannot instantiate). ---
    assert issubclass(EnrollmentSystemAdapter, ABC)
    assert "fetch_roster" in EnrollmentSystemAdapter.__abstractmethods__
    with pytest.raises(TypeError):
        EnrollmentSystemAdapter()  # type: ignore[abstract]

    # --- The registry exposes the SIS selector, resolvable by SIS_MODE. ---
    # simulate (the v1 default): now wired to the M5 SimulatedSISAdapter, which
    # yields a non-empty synthetic roster (adapter behavior is covered in depth by
    # tests/adapters/test_simulated_sis.py).
    from app.adapters.sis.simulated import SimulatedSISAdapter

    monkeypatch.setenv("SIS_MODE", "simulate")
    sim = get_enrollment_system_adapter()
    assert isinstance(sim, SimulatedSISAdapter)
    assert list(sim.fetch_roster())

    # live: no LiveSISAdapter in v1.
    monkeypatch.setenv("SIS_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_enrollment_system_adapter()


def test_demo_scenario_adapter_yields_all_three_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    """DH-3: under ``COCKPIT_SCENARIO=demo`` the registry adapter is aligned to the
    served demo cohort, so ``run_sis_reconcile`` yields ≥1 of every SIS bucket.

    Before the fix the registry built the roster over an INDEPENDENT default cohort
    (``from_seed(DEFAULT_FAMILY_COUNT, DEFAULT_SEED)``) regardless of scenario, so
    no demo family matched the roster ⇒ every paid family fell into 🔴 paid_not_in_sis
    (0 🟡, 0 ✅). After the fix the demo roster is built over ``generate_demo_cohort``
    — the SAME dataset the cockpit serves — so all three buckets are reachable.
    """
    from app.core.params import load_params
    from app.core.sis_reconcile import SisBucket
    from app.data.repository import InMemoryFamilyRepository
    from app.data.sis_reconcile_job import run_sis_reconcile
    from app.data.synthetic import generate_demo_cohort

    params = load_params(EXAMPLE_PARAMS)

    monkeypatch.setenv("SIS_MODE", "simulate")
    monkeypatch.setenv("COCKPIT_SCENARIO", "demo")

    # The SAME repository the demo path serves (deps.py `scenario == "demo"` branch).
    repo = InMemoryFamilyRepository(generate_demo_cohort(params=params), params=params)
    # The registry hands back the SIS adapter — now scenario-aware (DH-3 fix).
    adapter = get_enrollment_system_adapter()

    verdicts = run_sis_reconcile(repo, adapter, params)
    buckets = {v.bucket for v in verdicts}

    assert SisBucket.PAID_NOT_IN_SIS in buckets, "≥1 🔴 paid_not_in_sis"
    assert SisBucket.RECORDS_LAG in buckets, "≥1 🟡 records_lag"
    assert SisBucket.CONFIRMED in buckets, "≥1 ✅ confirmed"

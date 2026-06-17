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

import pytest

from app.adapters.registry import get_enrollment_system_adapter
from app.adapters.sis.base import EnrollmentSystemAdapter, MatchAttrs, RosterRecord


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
    # M0: no impl exists yet, so both modes fail loud — proving the seam is WIRED.
    # simulate (the v1 default): SimulatedSISAdapter is M5's job (TODO.md M5).
    monkeypatch.setenv("SIS_MODE", "simulate")
    with pytest.raises(NotImplementedError, match="M5"):
        get_enrollment_system_adapter()

    # live: no LiveSISAdapter in v1.
    monkeypatch.setenv("SIS_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_enrollment_system_adapter()

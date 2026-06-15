"""Simulated HubSpot CRM adapter — INV-9, OUT-3 (ARCHITECTURE.md §7, §7.1).

Every external boundary is an interface with two impls — Simulated and
Production — selected at startup by config (`adapters/registry.py`, NFR-8). v1
wires all to Simulated: a write-shaped call is **recorded, never sent** — there
is no network client at all in the simulated impl, so "records, never sends" is
provable structurally (an in-memory log) rather than by mocking sockets.

These are the §4.1-adapter-scope RED tests: the contract is that `send_mode ==
"simulate"` (the v1 lock, settings/D-9) yields the `SimulatedCRMAdapter`, that
`send_message` returns a `SendResult(simulated=True, ...)` and appends to the
recorder, and that `send_mode == "live"` fails **loud** (`NotImplementedError`)
because no production impl exists in v1 — never a silent live send.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.adapters.hubspot.crm_adapter import (
    CRMAdapter,
    SendResult,
    SimulatedCRMAdapter,
    SyncResult,
)
from app.adapters.registry import get_crm_adapter
from app.core.seam import MirrorState
from app.data.models import FamilyRecord, Stage


def _family() -> FamilyRecord:
    """A minimal valid family record for push/read happy-paths."""
    now = datetime(2026, 1, 2, tzinfo=UTC)
    return FamilyRecord(
        family_id=uuid4(),
        display_name="Synthetic Household",
        primary_contact_synthetic_email="parent@example.test",
        current_stage=Stage.APPLY,
        attribution_source="organic",
        attribution_utm={},
        updated_at=now,
    )


def test_crm_adapter_send_is_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SEND_MODE=simulate` ⇒ sim adapter records the send, no live send (INV-9).

    The registry returns a `SimulatedCRMAdapter`; `send_message` returns a
    `SendResult` flagged `simulated=True`, and the call is appended to the
    in-memory recorder — proving "records, never sends".
    """
    monkeypatch.setenv("SEND_MODE", "simulate")

    adapter = get_crm_adapter()
    assert isinstance(adapter, SimulatedCRMAdapter)
    assert isinstance(adapter, CRMAdapter)

    result = adapter.send_message(
        {"family_id": str(uuid4()), "channel": "email", "body": "Welcome!"}
    )

    assert isinstance(result, SendResult)
    assert result.simulated is True
    assert result.channel == "email"
    assert result.recorded_id

    # Recorded, never sent: the send lives only in an in-memory log.
    assert len(adapter.sent_log) == 1
    assert adapter.sent_log[0].recorded_id == result.recorded_id


def test_registry_live_mode_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SEND_MODE=live` ⇒ fail loud (no production impl in v1; never silent send)."""
    monkeypatch.setenv("SEND_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_crm_adapter()


def test_push_family_records_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """`push_family` is write-shaped: sim records it and returns a `SyncResult`."""
    monkeypatch.setenv("SEND_MODE", "simulate")
    adapter = get_crm_adapter()
    record = _family()

    result = adapter.push_family(record)

    assert isinstance(result, SyncResult)
    assert result.simulated is True
    assert result.family_id == record.family_id
    assert len(adapter.pushed_log) == 1
    assert adapter.pushed_log[0].family_id == record.family_id


def test_read_mirror_returns_seam_mirrorstate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`read_mirror` feeds the §4.7 deriver: it returns the existing MirrorState."""
    monkeypatch.setenv("SEND_MODE", "simulate")
    adapter = get_crm_adapter()
    record = _family()

    # No push yet ⇒ empty mirror (nothing pushed).
    empty = adapter.read_mirror(record.family_id)
    assert isinstance(empty, MirrorState)
    assert empty.stage is None
    assert empty.mirror_updated_at is None

    # After a push the mirror reflects the pushed stage.
    adapter.push_family(record)
    mirror = adapter.read_mirror(record.family_id)
    assert isinstance(mirror, MirrorState)
    assert mirror.stage == record.current_stage

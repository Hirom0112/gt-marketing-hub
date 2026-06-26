"""Simulated HubSpot CRM adapter — INV-9, OUT-3 (ARCHITECTURE.md §7, §7.1).

Every external boundary is an interface with two impls — Simulated and
Production — selected at startup by config (`adapters/registry.py`, NFR-8). v1
wires all to Simulated: a write-shaped call is **recorded, never sent** — there
is no network client at all in the simulated impl, so "records, never sends" is
provable structurally (an in-memory log) rather than by mocking sockets.

These are the §4.1-adapter-scope tests for the simulated impl: the contract is
that `CRM_MODE == "simulate"` (the default) yields the `SimulatedCRMAdapter`, that
`send_message` returns a `SendResult(simulated=True, ...)` and appends to the
recorder. The CRM boundary's `live` mode is the ONE adapter that DOES have a
production impl (S10 W2, `LiveHubSpotCRMAdapter`) — its selection (and the
fail-loud-on-misconfig, kill-switch-degrade, and guard behavior) is asserted in
``test_live_hubspot_adapter.py``. The `SEND_MODE=live ⇒ NotImplementedError` lock
that once lived here is superseded: the CRM seam now keys on `CRM_MODE`, while the
OTHER adapters keep their `SEND_MODE` locks.
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


def _family(*, updated_at: datetime | None = None) -> FamilyRecord:
    """A minimal valid family record for push/read happy-paths."""
    now = updated_at or datetime(2026, 1, 2, tzinfo=UTC)
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
    """`CRM_MODE=simulate` ⇒ sim adapter records the send, no live send (INV-9).

    The registry returns a `SimulatedCRMAdapter`; `send_message` returns a
    `SendResult` flagged `simulated=True`, and the call is appended to the
    in-memory recorder — proving "records, never sends".
    """
    monkeypatch.setenv("CRM_MODE", "simulate")

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


def test_crm_simulate_is_unaffected_by_send_mode_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CRM seam keys on `CRM_MODE`, not `SEND_MODE`: `SEND_MODE=live` is moot here.

    Supersedes the old `SEND_MODE=live ⇒ NotImplementedError` CRM lock: the CRM
    boundary now has its own `CRM_MODE` seam (S10). With `CRM_MODE` defaulting to
    `simulate`, flipping `SEND_MODE` to `live` (which still locks the OTHER
    adapters) leaves the CRM adapter simulated — no fall-through to a live write.
    """
    monkeypatch.setenv("SEND_MODE", "live")
    monkeypatch.delenv("CRM_MODE", raising=False)
    assert isinstance(get_crm_adapter(), SimulatedCRMAdapter)


def test_push_family_records_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """`push_family` is write-shaped: sim records it and returns a `SyncResult`."""
    monkeypatch.setenv("CRM_MODE", "simulate")
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
    monkeypatch.setenv("CRM_MODE", "simulate")
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


def test_simulated_search_modified_since_filters_and_sorts() -> None:
    """A2 twin: search_modified_since returns mirrors modified strictly after the
    watermark, ascending — reconstructed purely from the in-memory recorder (INV-9).
    """
    adapter = SimulatedCRMAdapter()
    before = _family(updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    # Two families AFTER the watermark, recorded newest-first so the sort is load-bearing.
    later = _family(updated_at=datetime(2026, 1, 10, tzinfo=UTC))
    earlier = _family(updated_at=datetime(2026, 1, 5, tzinfo=UTC))
    adapter.push_family(before)
    adapter.push_family(later)
    adapter.push_family(earlier)

    watermark_ms = int(datetime(2026, 1, 3, tzinfo=UTC).timestamp() * 1000)
    records = adapter.search_modified_since("deals", watermark_ms)

    # Only the two strictly-after the watermark, ascending by modified-at.
    assert [fid for fid, _ in records] == [earlier.family_id, later.family_id]
    assert all(isinstance(mirror, MirrorState) for _, mirror in records)
    assert before.family_id not in {fid for fid, _ in records}


# A-24 — per-child push (one application per child ⇒ one per-child CRM object).


def _student():
    from app.data.models import Student

    return Student(
        student_id=uuid4(),
        family_id=uuid4(),
        display_label="Synthetic household — Alex · Grade 3",
        synthetic_first_name="Alex",
        grade="3",
        current_stage=Stage.ENROLL,
    )


def test_simulated_push_student_records_never_sends() -> None:
    """push_student records the per-child push and returns simulated=True (INV-9)."""
    from app.adapters.hubspot.crm_adapter import StudentSyncResult

    adapter = SimulatedCRMAdapter()
    student = _student()

    result = adapter.push_student(student)

    assert isinstance(result, StudentSyncResult)
    assert result.simulated is True
    assert result.student_id == student.student_id
    assert result.family_id == student.family_id
    assert result.stage is Stage.ENROLL
    assert result.object_id is None  # no live object on the simulated recorder
    # Recorded in the per-child audit log (structural "records, never sends").
    assert adapter.pushed_student_log == [result]

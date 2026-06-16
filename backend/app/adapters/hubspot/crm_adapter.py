"""The CRM adapter boundary — interface + simulated impl (ARCHITECTURE.md §7.1).

§7 (authoritative): "Every external boundary is an interface with two
implementations — Simulated and Production — selected at startup by config
(`adapters/registry.py`, NFR-8). v1 wires all to Simulated. Going live =
flipping config + supplying the production impl, with zero changes to `core/` or
`ai/`."

The §7.1 `CRMAdapter` interface has three operations:

- ``push_family(family_record) -> SyncResult`` — write-shaped; the sim **records**
  the push, never sends.
- ``read_mirror(family_id) -> MirrorState`` — feeds the §4.7 seam-status deriver,
  so it returns the *existing* :class:`app.core.seam.MirrorState` (not a second
  type) — the simulated mirror is rebuilt from what was recorded.
- ``send_message(message) -> SendResult`` — email/nudge; simulated in v1.

INV-9: the simulated impl is a pure in-memory recorder — **no network client at
all**. "Records, never sends" is therefore a structural property (an in-memory
log), provable without mocking sockets. This module imports nothing from
``anthropic``/``langgraph`` and keeps ``core/`` pure (it only *reads*
``core.seam.MirrorState`` and ``data.models``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from app.core.seam import MirrorState
from app.data.models import FamilyRecord, Stage, Student


class SyncResult(BaseModel):
    """Outcome of a ``push_family`` write (§7.1).

    Attributes:
        simulated: ``True`` whenever the simulated impl handled it — the v1 lock
            (INV-9). A production impl would return ``False``.
        recorded_id: The id under which the push was recorded (the simulated
            stand-in for a CRM object id).
        family_id: The pushed family's id (the §4.7 mirror key).
        stage: The funnel stage written into the mirror — what ``read_mirror``
            will later reflect.
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    family_id: UUID
    stage: Stage
    # The associated CRM contact id, when the push created/upserted one — the
    # live HubSpot contact id under CRM_MODE=live, so the cockpit can deep-link
    # the captured Contact alongside the Deal (S10 W3). None for the simulated
    # recorder (which has no contact object), and optional so adding it is
    # non-breaking for existing SyncResult construction.
    contact_id: str | None = None


class StudentSyncResult(BaseModel):
    """Outcome of a ``push_student`` write — one child to its own CRM object (A-24).

    The per-child analog of :class:`SyncResult`: one application per child maps to
    one per-child CRM object. ``simulated`` is the v1 lock (INV-9); ``object_id``
    is the live per-child HubSpot object id under ``CRM_MODE=live`` (None for the
    simulated recorder). ``stage`` is the child's own funnel stage written to CRM.
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    student_id: UUID
    family_id: UUID
    stage: Stage
    object_id: str | None = None


class SendResult(BaseModel):
    """Outcome of a ``send_message`` send (§7.1).

    Attributes:
        simulated: ``True`` when recorded-not-sent (the v1 lock, INV-9).
        recorded_id: The id under which the send was recorded.
        channel: The send channel (e.g. ``"email"``, ``"nudge"``).
    """

    model_config = ConfigDict(frozen=True)

    simulated: bool
    recorded_id: str
    channel: str


class CRMAdapter(ABC):
    """The CRM external boundary (§7.1).

    Two impls — Simulated (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. Core/AI depend only on this interface.
    """

    @abstractmethod
    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        """Push a family record to the CRM. Write-shaped (§7.1)."""

    @abstractmethod
    def push_student(self, student: Student) -> StudentSyncResult:
        """Push ONE child to its own per-child CRM object (A-24). Write-shaped.

        One application per child ⇒ one per-child object. The sim records the
        push (INV-9); a live impl upserts a per-child object behind the
        synthetic-write guard + INV-8 budget.
        """

    @abstractmethod
    def read_mirror(self, family_id: UUID) -> MirrorState:
        """Read the CRM mirror for one family, for §4.7 seam derivation."""

    @abstractmethod
    def send_message(self, message: dict[str, Any]) -> SendResult:
        """Send an outbound email/nudge. Simulated in v1 (INV-9)."""


class SimulatedCRMAdapter(CRMAdapter):
    """In-memory recorder — records writes/sends, performs **no** I/O (INV-9).

    There is no network client here by construction, so "records, never sends"
    holds structurally: every ``push_family``/``send_message`` appends to an
    in-memory log and the call returns immediately with ``simulated=True``.
    ``read_mirror`` reconstructs a :class:`MirrorState` purely from what
    ``push_family`` recorded, so the same instance feeds the §4.7 deriver in a
    test/demo without any external HubSpot.
    """

    def __init__(self) -> None:
        # Append-only audit logs (the "recorder"). No network client.
        self.pushed_log: list[SyncResult] = []
        self.pushed_student_log: list[StudentSyncResult] = []  # A-24 per-child pushes.
        self.sent_log: list[SendResult] = []
        # The simulated HubSpot mirror, keyed by family — rebuilt from pushes.
        self._mirror: dict[UUID, tuple[Stage, datetime | None]] = {}

    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        """Record a family push and update the simulated mirror (never sends)."""
        result = SyncResult(
            simulated=True,
            recorded_id=uuid4().hex,
            family_id=family_record.family_id,
            stage=family_record.current_stage,
        )
        self.pushed_log.append(result)
        self._mirror[family_record.family_id] = (
            family_record.current_stage,
            family_record.updated_at,
        )
        return result

    def push_student(self, student: Student) -> StudentSyncResult:
        """Record a per-child push (never sends) — the v1 simulated path (A-24)."""
        result = StudentSyncResult(
            simulated=True,
            recorded_id=uuid4().hex,
            student_id=student.student_id,
            family_id=student.family_id,
            stage=student.current_stage,
        )
        self.pushed_student_log.append(result)
        return result

    def read_mirror(self, family_id: UUID) -> MirrorState:
        """Return the simulated mirror for ``family_id`` as a core MirrorState.

        Nothing pushed yet ⇒ an empty mirror (``stage=None``), which the §4.7
        deriver reads as "not pushed" (an ``unsynced`` concern, not divergence).
        """
        entry = self._mirror.get(family_id)
        if entry is None:
            return MirrorState(stage=None, mirror_updated_at=None)
        stage, mirror_updated_at = entry
        return MirrorState(stage=stage, mirror_updated_at=mirror_updated_at)

    def send_message(self, message: dict[str, Any]) -> SendResult:
        """Record an outbound send (email/nudge) and return it. No live send."""
        channel = str(message.get("channel", "email"))
        result = SendResult(simulated=True, recorded_id=uuid4().hex, channel=channel)
        self.sent_log.append(result)
        return result

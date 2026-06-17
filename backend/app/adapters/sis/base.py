"""The agnostic enrollment-system (SIS) boundary — interface + roster model (INV-9).

MULTI_AGENT_COCKPIT.md §4 (authoritative):

    Agnostic enrollment-system adapter (INV-9): EnrollmentSystemAdapter interface
    returning a normalized RosterRecord { external_id, match_attrs:{email,phone},
    enrollment_status, confirmed_at }. Impls: SimulatedSISAdapter (reads the
    synthetic roster / a CSV; v1 default) and a future LiveSISAdapter per real SIS.
    The reconcile core consumes RosterRecord only — it never knows which SIS.

This is the boundary the M5 SIS reconcile core consults: it matches GT's pipeline
families against a school's Student Information System roster to detect divergence
(paid-but-not-in-SIS, records-lag, etc.). Like every external boundary (INV-9),
it is an interface with two impls — Simulated (v1 default, reads the synthetic
roster) and Live (a real SIS, go-live) — selected by config in
:mod:`app.adapters.registry` via the ``SIS_MODE`` seam (TECH_STACK §5).

M0 ships ONLY this contract; the concrete ``SimulatedSISAdapter`` + the synthetic
roster generator are M5. INV-1: any roster the impls read is SYNTHETIC, never real
student PII. This module imports nothing from ``anthropic`` and keeps ``core/``
untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MatchAttrs(BaseModel):
    """The attributes the reconcile core matches a roster row to a family on.

    Both are optional — a roster row may carry an email, a phone, neither, or
    both; the matcher (M5) decides confidence from whatever is present. Frozen:
    a roster read is an immutable snapshot, not a mutable record.

    Attributes:
        email: The contact email on the SIS roster row, if any.
        phone: The contact phone on the SIS roster row, if any.
    """

    model_config = ConfigDict(frozen=True)

    email: str | None = None
    phone: str | None = None


class RosterRecord(BaseModel):
    """One normalized SIS roster row (the reconcile core's ONLY view of an SIS).

    SIS-agnostic by construction (INV-9): whatever the source SIS, an impl
    normalizes one student/family record to this shape, so the M5 reconcile core
    never branches on which SIS produced it. Frozen — a roster read is immutable.

    Attributes:
        external_id: The SIS's own opaque id for the record (its primary key in
            that system) — the join key back to the source, never a GT id.
        match_attrs: The :class:`MatchAttrs` (email/phone) the core matches on.
        enrollment_status: The normalized enrollment status string the source SIS
            reports (e.g. ``"confirmed"`` / ``"pending"``). Kept a plain ``str``
            at the boundary so any SIS's vocabulary normalizes here; the M5
            classifier maps it to GT's bucket verdict.
        confirmed_at: When the SIS confirmed the enrollment, if confirmed; ``None``
            when not yet confirmed.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str
    match_attrs: MatchAttrs
    enrollment_status: str
    confirmed_at: datetime | None = None


class EnrollmentSystemAdapter(ABC):
    """The agnostic enrollment-system (SIS) external boundary (INV-9).

    Two impls — Simulated (v1 default; reads the synthetic roster/CSV) and Live (a
    real SIS, go-live) — selected by config in :mod:`app.adapters.registry` via the
    ``SIS_MODE`` seam. The M5 reconcile core depends only on this interface and the
    :class:`RosterRecord` it yields; it never knows which SIS is behind it.
    """

    @abstractmethod
    def fetch_roster(self) -> Iterable[RosterRecord]:
        """Yield the normalized SIS roster as :class:`RosterRecord`s (INV-1 synthetic in v1)."""

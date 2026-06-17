"""Append-only `security_event` audit feed — the DETECTION (defense-in-depth)
spine the M7 SecurityTab Panel B reads (MULTI_AGENT_COCKPIT.md §3, §7).

This is the application-side mirror of the `0015_security_event.sql` table: one
APPEND-ONLY record per observed suspicious signal, each carrying an OWASP category
mapping (§7). It is DETECTION, NOT inline blocking — RLS is the inline owner
boundary; this only RECORDS what the edge middleware observes.

v1 is in-memory (ASSUMPTIONS A-3) and the populate path is the app-layer
``service_role`` repository (the edge middleware writes server-side) — there is NO
public definer-rights helper (D-RLS-7). Per INV-9, the v1 feed is a SIMULATED,
clearly-labeled stream: every seeded row carries ``simulated=True`` so the panel
labels it like every other v1 adapter. Production swaps a Supabase-backed impl
behind the same interface — the identical seam pattern as ``log_store.py``.

Append + query + acknowledge ONLY: an audit row is immutable once written. The one
mutation we allow is the operator ACKNOWLEDGE/report action (§7), which appends an
acknowledgement marker rather than mutating the row — the row's facts never change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ActorKind(StrEnum):
    """`security_event.actor_kind` — the principal class behind a signal (§3).

    Mirrors the SQL CHECK constraint tokens in 0015_security_event.sql.
    """

    ANON = "anon"
    AUTHENTICATED = "authenticated"
    SERVICE_ROLE = "service_role"


class Severity(StrEnum):
    """The severity band of an observed signal (§7) — info → critical."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecuritySignal(StrEnum):
    """The signal class an edge observation maps to (§3/§7).

    Each value is paired with an OWASP category in :data:`OWASP_BY_SIGNAL` — the
    EXACT category ids from MULTI_AGENT_COCKPIT.md §7.
    """

    FOREIGN_OBJECT_READ = "foreign_object_read"
    USER_ID_REASSIGN_ATTEMPT = "user_id_reassign_attempt"
    ANON_ADMIN_ROUTE = "anon_admin_route"
    AUTH_FAILURE_BURST = "auth_failure_burst"
    OVERSIZED_RESULT = "oversized_result"
    RLS_POSTURE_REGRESSION = "rls_posture_regression"


# The §7 OWASP mapping — the EXACT category ids. Each signal carries its OWASP
# category id so the panel can render the mapping (INV: never invent ids).
OWASP_BY_SIGNAL: dict[SecuritySignal, str] = {
    # Foreign object read by id ⇒ BOLA / CWE-639 (A01:2021 Broken Access Control).
    SecuritySignal.FOREIGN_OBJECT_READ: "API1:2023",
    # Attempt to change user_id / a field not owned ⇒ Broken Object Property Level Auth.
    SecuritySignal.USER_ID_REASSIGN_ATTEMPT: "API3:2023",
    # Anon hitting an admin/service route ⇒ Broken Function Level Auth.
    SecuritySignal.ANON_ADMIN_ROUTE: "API5:2023",
    # Forged/expired token, brute force ⇒ Identification & Auth Failures.
    SecuritySignal.AUTH_FAILURE_BURST: "A07:2021",
    # Enumeration / scraping / wide-band pulls ⇒ Unrestricted Resource Consumption.
    SecuritySignal.OVERSIZED_RESULT: "API4:2023",
    # An RLS posture regression (a table lost FORCE / a policy lost its guard) ⇒
    # Broken Access Control. Detected by the live posture check (Panel A) and
    # recorded here so the feed shows the regression event too.
    SecuritySignal.RLS_POSTURE_REGRESSION: "API1:2023",
}


def _now() -> datetime:
    """Default wall-clock timestamp (UTC). Never asserted on in a pinned test."""
    return datetime.now(UTC)


class SecurityEvent(BaseModel):
    """A `security_event` row — one observed suspicious signal (§3). Frozen.

    Mirrors the migration columns. ``owasp`` is derived from ``signal`` via
    :data:`OWASP_BY_SIGNAL` at construction so the mapping is never hand-typed.
    ``simulated`` labels the v1 feed (INV-9). ``acknowledged`` flips when the
    operator acks/reports it (§7) — the only state change, append-style.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=_now)
    actor_kind: ActorKind
    actor_ref: str | None = None
    surface: str | None = None
    signal: SecuritySignal
    severity: Severity = Severity.MEDIUM
    owasp: str
    detail: str | None = None
    # INV-9: the v1 feed is a SIMULATED, labeled stream (like every other v1
    # adapter). Live drains land here in prod with simulated=False.
    simulated: bool = True
    acknowledged: bool = False


def make_event(
    *,
    signal: SecuritySignal,
    actor_kind: ActorKind,
    actor_ref: str | None = None,
    surface: str | None = None,
    severity: Severity = Severity.MEDIUM,
    detail: str | None = None,
    simulated: bool = True,
    occurred_at: datetime | None = None,
) -> SecurityEvent:
    """Build a :class:`SecurityEvent`, stamping the OWASP id from the signal (§7).

    The OWASP category is ALWAYS derived from the signal via
    :data:`OWASP_BY_SIGNAL` — a caller can never pass a mismatched id.
    """
    return SecurityEvent(
        signal=signal,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        surface=surface,
        severity=severity,
        owasp=OWASP_BY_SIGNAL[signal],
        detail=detail,
        simulated=simulated,
        occurred_at=occurred_at if occurred_at is not None else _now(),
    )


class SecurityEventLog(ABC):
    """Append-only audit feed over the §3 `security_event` spine (Panel B).

    The surface is append + query + acknowledge only — there is no mutate/delete
    of a recorded fact (the audit posture). Acknowledge appends an ack marker by
    rebuilding the row with ``acknowledged=True`` (the row's FACTS never change).
    """

    @abstractmethod
    def record(self, event: SecurityEvent) -> SecurityEvent:
        """Append one observed signal. Append-only."""
        raise NotImplementedError

    @abstractmethod
    def list_events(self) -> list[SecurityEvent]:
        """Every recorded event, in append order (the feed index)."""
        raise NotImplementedError

    @abstractmethod
    def acknowledge(self, event_id: UUID) -> SecurityEvent | None:
        """Mark an event acknowledged/reported (§7). Returns None for an unknown id."""
        raise NotImplementedError


class InMemorySecurityEventLog(SecurityEventLog):
    """In-memory append-only security-event feed (v1; ASSUMPTIONS A-3).

    Storage is an insertion-ordered dict keyed on ``event_id``. Acknowledge
    replaces the stored row with a frozen copy flipped to ``acknowledged=True``
    (the facts — signal, owasp, actor — are unchanged). Production swaps a
    Supabase-backed impl behind the same interface.
    """

    def __init__(self) -> None:
        self._events: dict[UUID, SecurityEvent] = {}

    def record(self, event: SecurityEvent) -> SecurityEvent:
        self._events[event.event_id] = event
        return event

    def list_events(self) -> list[SecurityEvent]:
        return list(self._events.values())

    def acknowledge(self, event_id: UUID) -> SecurityEvent | None:
        existing = self._events.get(event_id)
        if existing is None:
            return None
        acked = existing.model_copy(update={"acknowledged": True})
        self._events[event_id] = acked
        return acked


def seed_simulated_feed(log: SecurityEventLog) -> None:
    """Seed the v1 SIMULATED suspicious-event stream (INV-9; §7 Panel B).

    A small, deterministic, clearly-labeled (``simulated=True``) set covering each
    OWASP signal class so Panel B is demoable on synthetic data with no live log
    drain. INV-1: every detail string is synthetic — no PII, no child key, no real
    actor identity (``actor_ref`` is a synthetic uid or None).
    """
    seeds: list[tuple[SecuritySignal, ActorKind, str | None, Severity, str]] = [
        (
            SecuritySignal.FOREIGN_OBJECT_READ,
            ActorKind.AUTHENTICATED,
            "sim-uid-0001",
            Severity.HIGH,
            "Authenticated rep requested a family record assigned to another rep (BOLA).",
        ),
        (
            SecuritySignal.USER_ID_REASSIGN_ATTEMPT,
            ActorKind.AUTHENTICATED,
            "sim-uid-0002",
            Severity.HIGH,
            "PATCH attempted to set user_id to a value the actor does not own "
            "(property-level auth).",
        ),
        (
            SecuritySignal.ANON_ADMIN_ROUTE,
            ActorKind.ANON,
            None,
            Severity.MEDIUM,
            "Anonymous request hit an admin/service-scoped route (function-level auth).",
        ),
        (
            SecuritySignal.AUTH_FAILURE_BURST,
            ActorKind.ANON,
            None,
            Severity.MEDIUM,
            "Repeated 401/403 responses from one source within the rolling window (brute force).",
        ),
        (
            SecuritySignal.OVERSIZED_RESULT,
            ActorKind.AUTHENTICATED,
            "sim-uid-0003",
            Severity.LOW,
            "A list response exceeded the oversized-result threshold (enumeration/scraping).",
        ),
    ]
    for signal, actor_kind, actor_ref, severity, detail in seeds:
        log.record(
            make_event(
                signal=signal,
                actor_kind=actor_kind,
                actor_ref=actor_ref,
                surface="(simulated feed)",
                severity=severity,
                detail=detail,
                simulated=True,
            )
        )

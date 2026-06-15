"""Contact-log recency aggregation tests (S9 W1; A-14; ARCH §10).

`last_contact_at` is a PURE aggregation over the append-only audit log
(`app/observability/log_store.py`), mirroring `core/scoreboard.py`: a family's
latest contact is the MAX `created_at` over its proposals' decisions where the
action is APPROVE — *the last time we sent the family an approved outbound*.
Recency is DERIVED from the log, not a stored family field (A-14): the family
store stays read-only (A-3) and the log is the write spine (INV-2).

A family with no approved decision (only discards, or no decision at all) has
no contact ⇒ `None`. Reading the in-memory store through its public query API is
not I/O (A-3 / scoreboard precedent), so this stays in the deterministic core.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.contact_log import last_contact_at

from app.observability.log_store import DecisionAction, InMemoryObservabilityLog

# Stable UUIDs so the aggregation is fully deterministic (no uuid4 in the path).
FAMILY_CONTACTED = UUID("00000000-0000-0000-0000-0000000000f1")
FAMILY_DISCARD_ONLY = UUID("00000000-0000-0000-0000-0000000000f2")
FAMILY_NO_DECISION = UUID("00000000-0000-0000-0000-0000000000f3")
FAMILY_ABSENT = UUID("00000000-0000-0000-0000-0000000000f9")

PID_C1 = UUID("00000000-0000-0000-0000-0000000000c1")  # contacted family, earlier approve
PID_C2 = UUID("00000000-0000-0000-0000-0000000000c2")  # contacted family, later approve
PID_D1 = UUID("00000000-0000-0000-0000-0000000000d1")  # discard-only family
PID_N1 = UUID("00000000-0000-0000-0000-0000000000e1")  # no-decision family

DRAFT_FLOW = "enrollment_draft"

EARLIER = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 6, 14, 17, 30, tzinfo=UTC)


def _seed_log() -> InMemoryObservabilityLog:
    """Two approvals for one family (out of order), a discard, and a bare proposal."""
    log = InMemoryObservabilityLog()

    # FAMILY_CONTACTED: two approved outbounds — LATER must win over EARLIER even
    # though the later-timestamped approve is appended first.
    log.log_proposal(
        proposal_id=PID_C2,
        family_id=FAMILY_CONTACTED,
        flow=DRAFT_FLOW,
        schema_version="1",
        payload={},
    )
    log.log_decision(
        proposal_id=PID_C2, human="director", action=DecisionAction.APPROVE, created_at=LATER
    )
    log.log_proposal(
        proposal_id=PID_C1,
        family_id=FAMILY_CONTACTED,
        flow=DRAFT_FLOW,
        schema_version="1",
        payload={},
    )
    log.log_decision(
        proposal_id=PID_C1, human="director", action=DecisionAction.APPROVE, created_at=EARLIER
    )

    # FAMILY_DISCARD_ONLY: a discard is not a contact ⇒ None.
    log.log_proposal(
        proposal_id=PID_D1,
        family_id=FAMILY_DISCARD_ONLY,
        flow=DRAFT_FLOW,
        schema_version="1",
        payload={},
    )
    log.log_decision(
        proposal_id=PID_D1, human="director", action=DecisionAction.DISCARD, created_at=LATER
    )

    # FAMILY_NO_DECISION: a proposal with no decision logged ⇒ None.
    log.log_proposal(
        proposal_id=PID_N1,
        family_id=FAMILY_NO_DECISION,
        flow=DRAFT_FLOW,
        schema_version="1",
        payload={},
    )

    return log


def test_last_contact_at_returns_latest_approve() -> None:
    """The MAX approve `created_at` over the family's proposals is returned."""
    log = _seed_log()
    assert last_contact_at(log, FAMILY_CONTACTED) == LATER


def test_discard_only_family_has_no_contact() -> None:
    """A family with only a DISCARD decision has never been contacted ⇒ None."""
    log = _seed_log()
    assert last_contact_at(log, FAMILY_DISCARD_ONLY) is None


def test_no_decision_family_has_no_contact() -> None:
    """A family whose proposal has no decision yet ⇒ None."""
    log = _seed_log()
    assert last_contact_at(log, FAMILY_NO_DECISION) is None


def test_absent_family_has_no_contact() -> None:
    """A family with no proposals at all ⇒ None (clean miss, not a raise)."""
    log = _seed_log()
    assert last_contact_at(log, FAMILY_ABSENT) is None

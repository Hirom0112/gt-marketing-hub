"""Contact-recency aggregation — last_contact_at, derived from the audit log (S9 W1).

Recency is DERIVED, not stored (ASSUMPTIONS A-14): the cockpit's contact-color
system needs *when did we last reach this family*, and the answer already lives
in the append-only audit spine (``app/observability/log_store.py``). An approved
outbound proposal is a contact; its decision's ``created_at`` is when it
happened. So a family's ``last_contact_at`` is the MAX ``created_at`` over its
proposals' decisions where the action is :attr:`DecisionAction.APPROVE`.

This keeps the family store read-only (A-3) and the audit log the single write
spine (INV-2) — no new mutable ``last_contact_at`` column. The function mirrors
``core/scoreboard.py``'s pure rollup: it reads the log through its public query
API (``list_proposals`` + ``get_audit``), which is not I/O (A-3 / scoreboard
precedent), so it belongs in the deterministic core. It imports only the
:class:`~app.observability.log_store.ObservabilityLog` interface + the
:class:`~app.observability.log_store.DecisionAction` enum and stdlib — no
``anthropic``/``app.ai``/``app.adapters``, no ``datetime.now``. Same log ⇒ same
result.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.observability.log_store import DecisionAction, ObservabilityLog


def last_contact_at(
    log: ObservabilityLog, family_id: UUID, *, student_id: UUID | None = None
) -> datetime | None:
    """Latest approved-outbound timestamp for a family/child, from the audit log (A-14).

    Pure aggregation over ``log`` (mirrors ``scoreboard._enrollment_summary``):
    scans every proposal belonging to ``family_id`` and returns the MAX
    ``created_at`` across their decisions whose action is
    :attr:`DecisionAction.APPROVE` — the last time an approved outbound went out.
    A family/child with no approved decision (only discards/edits, or no decision
    at all, or no proposals) has never been contacted ⇒ ``None``.

    ``student_id`` scopes the aggregation to ONE child for per-student flows
    (A-24): proposals are matched on BOTH ``family_id`` and ``student_id``, so a
    family-level query (``student_id=None``) ignores per-child proposals and a
    per-child query ignores a sibling's. Family-level proposals carry
    ``student_id=None`` and so match only the family-level query.

    Deterministic: reads through the public query API only (no private state, no
    wall-clock). Same log ⇒ same answer.

    Args:
        log: The append-only NFR-6 audit spine to aggregate.
        family_id: The family whose latest contact is sought.
        student_id: Restrict to one child's proposals (A-24); ``None`` = family-level.

    Returns:
        The latest APPROVE-decision ``created_at`` for the family/child, or
        ``None`` if there is no approved decision.
    """
    latest: datetime | None = None
    for proposal in log.list_proposals():
        if proposal.family_id != family_id or proposal.student_id != student_id:
            continue
        audit = log.get_audit(proposal.proposal_id)
        if audit is None:
            continue
        for decision in audit.decisions:
            if decision.action is not DecisionAction.APPROVE:
                continue
            if latest is None or decision.created_at > latest:
                latest = decision.created_at
    return latest

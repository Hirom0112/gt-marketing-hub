"""Daily Anthropic spend aggregation — cross-run DAILY cost cap accumulator (NFR-5).

The per-run governor (`app/ai/cost.py::RunBudget`) bounds a SINGLE run. The DAILY
ceiling (`COST_DAILY_USD_CAP`, `Settings.cost_daily_usd_cap`) is a SEPARATE
cross-run mechanism: cumulative Anthropic spend per day. On breach the global kill
switch trips and every AI feature degrades deterministically (TECH_STACK §6.2).

The runtime is stateless (Lambda/Mangum; ARCH §12), so today's spend cannot live in
a module-global counter — it is DERIVED from the append-only audit spine, exactly as
``core/contact_log.last_contact_at`` and ``core/scoreboard`` derive state from the
log. Each completed live run stamps its ``budget.usd_spent`` onto the proposal it
logs (``ProposalRecord.usd_spent``); this function sums those over a calendar day.

Purity (CLAUDE §3 / §7): deterministic core. It reads the log through its public
query API (``list_proposals``) — not I/O (A-3 / scoreboard precedent) — and imports
only the :class:`~app.observability.log_store.ObservabilityLog` interface + stdlib.
No ``anthropic``/``app.ai``/``app.adapters``, and **no ``datetime.now``**: the ``day``
is INJECTED by the caller (the composition root reads the wall clock), so the same
log + day always yields the same total. USD is never inferred from a per-token rate
(INV-11) — it is whatever the caller stamped.
"""

from __future__ import annotations

from datetime import date

from app.observability.log_store import ObservabilityLog


def daily_usd_spent(log: ObservabilityLog, *, day: date) -> float:
    """Sum the Anthropic ``usd_spent`` over proposals logged on ``day`` (NFR-5).

    Pure aggregation over ``log`` (mirrors ``contact_log.last_contact_at`` /
    ``scoreboard``): scans every proposal and adds ``usd_spent`` for those whose
    ``created_at`` falls on the calendar ``day`` (UTC date of the stamped instant).
    A day with no logged spend — or only degraded/non-live runs that stamped 0.0 —
    sums to ``0.0`` (a clean zero, not an error).

    ``day`` is injected (no wall-clock read here) so the core stays clock-free and
    deterministic: same log + same day ⇒ same total.

    Args:
        log: the append-only NFR-6 audit spine to aggregate.
        day: the calendar day (UTC) whose cumulative spend is summed.

    Returns:
        The total ``usd_spent`` across that day's proposals.
    """
    return sum(
        proposal.usd_spent for proposal in log.list_proposals() if proposal.created_at.date() == day
    )

"""Agent personal-KPI aggregation — pure, params-windowed (D-14; REDESIGN_PLAN R4).

The sales-agent KPI Dashboard (Tab 5) shows the agent's OWN performance over a
time window: Leads Assigned, Contacts Made, Follow-Ups Completed, Appointments
Booked, Applications Started, Applications Completed, Conversion Rate. Every metric
is a PURE aggregation over facts already on the spine — the family's ``assigned_at``,
the contact-outcome log, ``app_form`` state, and ``funding_state`` — so this adds NO
new applicant data (INV-1) and owns no scoring math beyond a count/ratio.

Pure core (CLAUDE §3, INV-2): a total function of its arguments. The API layer
resolves the owner scope (the IDOR clamp), gathers the owner's joined families +
their contact outcomes, computes the window cutoff from ``params.kpi.windows`` and
``now``, then calls :func:`agent_kpis`. No clock, no log, no I/O here — same inputs
⇒ same KPIs. Imports only the typed models/enums + stdlib (the core-purity test
guards it).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.data.models import FundingState
from app.data.repository import JoinedFamily
from app.observability.log_store import ContactDisposition, ContactOutcomeRecord


@dataclass(frozen=True, slots=True)
class AgentKpis:
    """One agent's personal KPIs over a window (D-14). Read-only, no writes (INV-2)."""

    leads_assigned: int
    contacts_made: int
    follow_ups_completed: int
    appointments_booked: int
    applications_started: int
    applications_completed: int
    conversion_rate: float


# The funded floor for the conversion numerator — a family at FUNDED has converted
# (the deal closed). The single home for "funded" in the KPI rollup (INV-11).
_FUNDED_STATE: FundingState = FundingState.FUNDED

# Rounding for the conversion ratio — a presentation precision (no decision is gated
# on it), mirroring the agent-roster rollup's display rounding.
_CONVERSION_DP = 4


def _in_window(when: datetime | None, *, cutoff: datetime | None) -> bool:
    """Whether ``when`` falls inside the trailing window (``cutoff`` ..), pure.

    ``cutoff`` is ``None`` for the unbounded ``all`` window (everything passes,
    even a fact with no timestamp). For a bounded window a ``None`` timestamp is
    OUT (it cannot be placed in time) and a dated fact passes iff it is at or after
    the cutoff.
    """
    if cutoff is None:
        return True
    if when is None:
        return False
    return when >= cutoff


def agent_kpis(
    families: Iterable[JoinedFamily],
    outcomes: Iterable[ContactOutcomeRecord],
    *,
    cutoff: datetime | None,
) -> AgentKpis:
    """Aggregate one owner's KPIs over a window (D-14) — pure count/ratio rollup.

    Args:
        families: the owner's joined families (already owner-scoped by the caller).
        outcomes: those families' contact-outcome events (owner-scoped by the caller).
        cutoff: the window's lower bound (``None`` = the unbounded ``all`` window).
            A bounded window keys families on ``assigned_at`` / ``app_form`` dates and
            outcomes on ``created_at``; a fact dated before the cutoff (or undated,
            for a bounded window) does not count.

    Returns:
        The :class:`AgentKpis` for the window. ``conversion_rate`` is funded ÷
        assigned over the SAME windowed assigned set (0.0 when none assigned — never
        a divide-by-zero), rounded to 4 dp.
    """
    fams = list(families)

    # Leads Assigned — families whose assignment falls in the window.
    assigned = [j for j in fams if _in_window(j.family.assigned_at, cutoff=cutoff)]
    leads_assigned = len(assigned)

    # Conversion — funded ÷ assigned over the windowed assigned set.
    funded = sum(1 for j in assigned if j.family.funding_state is _FUNDED_STATE)
    conversion_rate = round(funded / leads_assigned, _CONVERSION_DP) if leads_assigned else 0.0

    # Applications — started = an app_form exists (created in window); completed =
    # that app_form is submitted (submitted in window). Keyed on the app_form's own
    # timestamps so the funnel work is attributed to WHEN it happened.
    apps_started = 0
    apps_completed = 0
    for j in fams:
        app = j.app_form
        if app is None:
            continue
        if _in_window(app.created_at, cutoff=cutoff):
            apps_started += 1
        if app.submitted_at is not None and _in_window(app.submitted_at, cutoff=cutoff):
            apps_completed += 1

    # Contact-outcome derived metrics — windowed on each event's created_at.
    contacts_made = 0
    follow_ups_completed = 0
    appointments_booked = 0
    for o in outcomes:
        if not _in_window(o.created_at, cutoff=cutoff):
            continue
        # A logged contact attempt is a "contact made" regardless of disposition —
        # silence (a no-answer) is still an attempt the rep made.
        contacts_made += 1
        # The booked/follow-up KPIs are disposition-EXACT (the D-16 dropdown values),
        # so they read the specific disposition rather than the engagement class.
        if o.disposition is ContactDisposition.FOLLOW_UP_NEEDED:
            follow_ups_completed += 1
        if o.disposition is ContactDisposition.APPOINTMENT_SCHEDULED:
            appointments_booked += 1

    return AgentKpis(
        leads_assigned=leads_assigned,
        contacts_made=contacts_made,
        follow_ups_completed=follow_ups_completed,
        appointments_booked=appointments_booked,
        applications_started=apps_started,
        applications_completed=apps_completed,
        conversion_rate=conversion_rate,
    )

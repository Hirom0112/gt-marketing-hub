"""Contact-recency deriver — the PURE color-system core (S9 W1).

The enrollment cockpit color-codes every family by *how stale our contact is*:
a brand-new uncontacted lead is grey (fresh), one we have let sit too long is
red (overdue), one we have followed up on is light-green, and a won family is
neutral (closed). This module derives that :class:`ContactStatus` deterministically.

Recency is DERIVED, not stored (ASSUMPTIONS A-14): a family's ``last_contact_at``
comes from the append-only audit log (``core/contact_log.py``), and this deriver
consumes it alongside the family's ``created_at`` and an injected ``now``. Per
CLAUDE.md §3 / INV-2 this is deterministic core: it is a pure function of its
arguments — no I/O, no ``datetime.now`` (``now`` is injected), no
``anthropic``/``app.ai``/``app.adapters``. Same inputs ⇒ same status.

The day thresholds live in ``params.enrollment.contact`` (INV-11), never
hardcoded: ``grey_window_days`` bounds the fresh region and ``overdue_days`` is
the red threshold. The 4th-day rule (with the committed ``overdue_days=4``): an
uncontacted family at age 3 is still FRESH, at age 4 it is OVERDUE.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.core.params import Params


class ContactStatus(StrEnum):
    """A family's contact-recency color (S9 W1).

    - ``FRESH`` (grey): uncontacted and still young — ``age_days`` within the
      grey window; no follow-up is overdue yet.
    - ``OVERDUE`` (red): uncontacted and aged past the red threshold — we have
      let the lead sit (the ``no_response`` stall surfaces here).
    - ``FOLLOWED_UP`` (light-green): we have contacted the family at least once
      (an approved outbound) and it is not yet won.
    - ``CLOSED`` (neutral): the family is funded — the deal is won.
    """

    FRESH = "fresh"
    OVERDUE = "overdue"
    FOLLOWED_UP = "followed_up"
    CLOSED = "closed"


def derive_contact_status(
    *,
    created_at: datetime,
    last_contact_at: datetime | None,
    now: datetime,
    funded: bool,
    params: Params,
) -> ContactStatus:
    """Derive a family's contact-recency :class:`ContactStatus` (S9 W1; INV-11).

    Pure and deterministic — a function of its arguments alone. The rules are
    LOCKED:

      1. ``funded`` ⇒ :attr:`ContactStatus.CLOSED` (the deal is won; recency is
         moot).
      2. else ``last_contact_at is not None`` ⇒ :attr:`ContactStatus.FOLLOWED_UP`
         (we have contacted them and it is not yet won).
      3. else (uncontacted) compute ``age_days = (now - created_at).days``:
         ``age_days >= overdue_days`` ⇒ :attr:`ContactStatus.OVERDUE` (red),
         otherwise :attr:`ContactStatus.FRESH` (grey).

    The thresholds come from ``params.enrollment.contact`` (``overdue_days`` is
    the red threshold; ``grey_window_days`` documents the fresh ceiling) — never
    hardcoded (INV-11). With ``overdue_days=4`` an uncontacted family at age 3 is
    FRESH and at age 4 is OVERDUE.

    Args:
        created_at: When the family record was created — the recency clock start.
        last_contact_at: Latest approved-outbound timestamp from the audit log
            (``core/contact_log.py``), or ``None`` if never contacted (A-14).
        now: Reference time for the age comparison; injected for determinism
            (no ``datetime.now`` in core).
        funded: Whether the family is funded (won) — short-circuits to CLOSED.
        params: Loaded params; supplies ``enrollment.contact`` day windows.

    Returns:
        The family's :class:`ContactStatus`.
    """
    if funded:
        return ContactStatus.CLOSED

    if last_contact_at is not None:
        return ContactStatus.FOLLOWED_UP

    overdue_days = params.enrollment.contact.overdue_days
    age_days = (now - created_at).days
    if age_days >= overdue_days:
        return ContactStatus.OVERDUE
    return ContactStatus.FRESH

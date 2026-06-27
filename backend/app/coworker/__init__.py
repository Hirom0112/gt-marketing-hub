"""The rep-facing Claude coworker (M6) — a READ-PROXY of the owner-scoped cockpit.

The founder connects Claude Desktop as the **closer** (Agent #1) and runs
``/check-in`` + a ``draft→confirm`` loop. The coworker authenticates AS the closer
with a SIGNED ``Authorization: Bearer`` operator JWT (B1: the verified successor to
the deleted, spoofable client-supplied role header, S1; MULTI_AGENT_COCKPIT §10.3)
and is just a SECOND client of the same owner-scoped reads the cockpit UI uses — the
IDOR clamp holds through the coworker.

Its defining invariant (MULTI_AGENT_COCKPIT §2.5; CLAUDE INV-2/INV-9):

    **The coworker performs NO direct HubSpot write.** Every write — coworker or
    UI — routes through the ONE gated decision route
    ``POST /proposals/{proposal_id}/decision``, timestamped to the audit spine
    (NFR-6). The coworker never imports or calls a HubSpot/CRM client.

This package is the TESTABLE core (pure-ish proxy logic over an HTTP client seam):

* :func:`app.coworker.core.check_in` — composes the four ``/check-in`` blocks
  (who-to-contact ranked / pending-notes / hygiene-gaps / voucher-clocks) from the
  owner-scoped reads.
* :func:`app.coworker.core.draft` — calls ``POST /ai/enrollment/draft`` and returns
  the eval-gated outcome VERBATIM (a ``surfaced=false`` block is surfaced as-is —
  never softened/rewritten/retried; INV-4).
* :func:`app.coworker.core.confirm` — calls ONLY
  ``POST /proposals/{id}/decision`` (the SOLE write path) and returns the
  timestamp + note/decision id.

The MCP server entrypoint (:mod:`app.coworker.mcp_server`) is a THIN stdio wrapper
that imports this core; it is import-guarded so the SUITE never requires the ``mcp``
SDK to pass (the core logic is what the gate tests).
"""

from __future__ import annotations

from app.coworker.core import (
    CheckInBriefing,
    ConfirmResult,
    DraftOutcome,
    HygieneGap,
    PendingNote,
    VoucherClock,
    WhoToContact,
    check_in,
    confirm,
    draft,
)

__all__ = [
    "CheckInBriefing",
    "ConfirmResult",
    "DraftOutcome",
    "HygieneGap",
    "PendingNote",
    "VoucherClock",
    "WhoToContact",
    "check_in",
    "confirm",
    "draft",
]

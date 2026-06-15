"""Adapter registry — startup selection of impls by config (ARCHITECTURE.md §7, NFR-8).

§7 (authoritative): impls are "selected at startup by config (`adapters/registry.py`,
NFR-8). v1 wires all to Simulated. Going live = flipping config + supplying the
production impl, with zero changes to `core/` or `ai/`."

The selector keys on ``SEND_MODE`` — read **only** through
:func:`app.core.settings.get_settings` (the §5 env seam; never ``os.environ``
here). v1 is locked to ``simulate`` (D-9, OUT-3) ⇒ the simulated impl. ``live``
**raises** ``NotImplementedError``: no production CRM impl exists in v1, and the
INV-9 posture is fail-loud — never silently fall through to a live send.
"""

from __future__ import annotations

from app.adapters.hubspot.crm_adapter import CRMAdapter, SimulatedCRMAdapter
from app.core.settings import get_settings


def get_crm_adapter() -> CRMAdapter:
    """Return the CRM adapter impl for the current ``SEND_MODE`` (§7, NFR-8).

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`SimulatedCRMAdapter` (records,
      never sends; INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production impl in v1; fail loud
      rather than silently send (INV-9, OUT-3).

    Raises:
        NotImplementedError: when ``SEND_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().send_mode
    if mode == "simulate":
        return SimulatedCRMAdapter()
    raise NotImplementedError(
        "No production CRMAdapter in v1: SEND_MODE='live' is reserved for a "
        "supplied production impl (ARCHITECTURE.md §7; INV-9 fail-loud). "
        "v1 is locked to SEND_MODE='simulate'."
    )

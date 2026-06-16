"""CRM seam status endpoint — surface the HubSpot kill switch in the UI (S14 W4).

The read-only window the operator looks through to SEE the CRM/HubSpot seam state
so the live-sync action can FAIL CLOSED in the UI (the INV-3 pattern: "a red eval
disables the action in the UI"). The kill switch's MECHANISM stays a server env var
(``HUBSPOT_KILL_SWITCH``); flipping a server secret from the browser would be unsafe,
so this endpoint only REPORTS state — it never writes settings and exposes no toggle.

  ``GET /crm/status``
    The effective CRM seam state, derived purely from the §5 env settings + the
    one canonical registry precedence (:func:`app.adapters.registry.effective_crm_mode`,
    INV-11 — reused, not forked). Returns NO secret: ``token_configured`` is a bool,
    never the token itself. The frontend reads this to render the "CRM: Simulated /
    LIVE / Kill switch ON" indicator and to disable the live-push control when the
    kill switch is on.

This module is the composition root (it may import ``app.adapters`` / the settings
seam); it makes no live call and writes no state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.registry import effective_crm_mode
from app.api.deps import get_settings_dep
from app.core.settings import CrmMode, Settings

router = APIRouter(tags=["crm"])

# Dependency alias (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/scoreboard.py + app/api/seam.py).
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


class CrmStatus(BaseModel):
    """The CRM/HubSpot seam state for the operator UI (S14 W4) — NO secrets.

    Every field is derivable purely from the §5 env settings; the token itself is
    NEVER surfaced (``token_configured`` is a bool). ``effective_mode`` is what the
    registry would ACTUALLY select (``simulate`` when the kill switch is on, even
    with ``CRM_MODE=live``), so the UI can fail closed against the real behavior.
    """

    crm_mode: CrmMode
    kill_switch: bool
    effective_mode: CrmMode
    token_configured: bool
    calls_per_run_cap: int


@router.get("/crm/status", response_model=CrmStatus)
def get_crm_status(settings: SettingsDep) -> CrmStatus:
    """The effective CRM seam state, no secrets (S14 W4; INV-3/INV-8 surfaced).

    Derived purely from the §5 env settings: ``crm_mode`` (the configured
    ``CRM_MODE``), ``kill_switch`` (``HUBSPOT_KILL_SWITCH``), ``effective_mode``
    (what :func:`app.adapters.registry.effective_crm_mode` — the one canonical
    precedence, INV-11 — would actually select: ``simulate`` when the kill switch
    is on even though ``CRM_MODE=live``), ``token_configured`` (a bool — the token
    is NEVER returned), and ``calls_per_run_cap`` (the INV-8 per-run ceiling).

    Read-only by design: the kill switch's MECHANISM stays a server env var. This
    endpoint SURFACES state and the frontend fail-closes the live-push action on it;
    it does NOT add a browser-writable kill toggle (flipping a server secret from
    the browser would be unsafe).
    """
    return CrmStatus(
        crm_mode=settings.crm_mode,
        kill_switch=settings.hubspot_kill_switch,
        effective_mode=effective_crm_mode(settings),
        token_configured=settings.hubspot_private_app_token is not None,
        calls_per_run_cap=settings.hubspot_calls_per_run_cap,
    )

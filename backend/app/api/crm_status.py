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

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.adapters.registry import effective_crm_mode
from app.api._crm_ops_cache import parity_snapshot
from app.api.deps import (
    get_active_program,
    get_params,
    get_repository,
    get_seam_crm_adapter_dep,
    get_settings_dep,
)
from app.core.params import Params
from app.core.program import Program
from app.core.settings import CrmMode, Settings
from app.data.repository import FamilyRepository

router = APIRouter(tags=["crm"])

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/scoreboard.py + app/api/seam.py).
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
# The SAME seam CRM adapter the §4.7 seam endpoints read (R1): its mirror carries
# real multi-field data, so parity measures DB-vs-real-mirror agreement.
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_seam_crm_adapter_dep)]
ParamsDep = Annotated[Params, Depends(get_params)]
ProgramDep = Annotated[Program, Depends(get_active_program)]


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
    # A4 — the active-program cohort's sync-parity + the data-confidence banner.
    parity_overall: float
    parity_by_field: dict[str, float]
    data_confidence_banner: bool


@router.get("/crm/status", response_model=CrmStatus)
def get_crm_status(
    settings: SettingsDep,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    params: ParamsDep,
    program: ProgramDep,
) -> CrmStatus:
    """The effective CRM seam state + the A4 sync-parity surface, no secrets.

    The pure-settings half is unchanged (S14 W4; INV-3/INV-8 surfaced): ``crm_mode``
    (the configured ``CRM_MODE``), ``kill_switch`` (``HUBSPOT_KILL_SWITCH``),
    ``effective_mode`` (what :func:`app.adapters.registry.effective_crm_mode` — the
    one canonical precedence, INV-11 — would actually select: ``simulate`` when the
    kill switch is on even though ``CRM_MODE=live``), ``token_configured`` (a bool —
    the token is NEVER returned), and ``calls_per_run_cap`` (the INV-8 per-run
    ceiling).

    The A4 half computes the active-program cohort's sync-parity
    (:func:`app.core.parity.compute_parity`) over the SAME ``(record, mirror)``
    pairing the §4.7 seam endpoints use: every family from
    ``repository.list_families`` (already program-scoped at the repo layer, A1) paired
    with the seam CRM adapter's ``read_mirror`` (the seeded simulated mirror in v1, the
    live portal mirror under ``CRM_MODE=live`` — INV-9). ``parity_overall`` is the
    fraction of rows fully ``synced``, ``parity_by_field`` the per-tracked-field
    agreement, and ``data_confidence_banner`` is raised when overall parity drops
    below ``params.data_confidence.min_parity`` (INV-11 — the single threshold home),
    so a meaningfully out-of-sync cohort is surfaced rather than silently trusted. The
    O(n) mirror reads ride the shared short-TTL single-flight cache
    (:func:`app.api._crm_ops_cache.parity_snapshot`, keyed by program): this banner
    fires on every CRM page load alongside the page's own parity read, so memoizing the
    live scan for ``params.crm_ops.snapshot_ttl_seconds`` collapses them into ONE live
    mirror scan per TTL instead of storming the HubSpot rate limit. A cached LIVE read
    is STILL live — only the recomputation is skipped, never the values or labels.

    Read-only by design: the kill switch's MECHANISM stays a server env var. This
    endpoint SURFACES state and the frontend fail-closes the live-push action on it;
    it does NOT add a browser-writable kill toggle (flipping a server secret from
    the browser would be unsafe).
    """
    # The A4 parity scan rides the shared short-TTL single-flight cache, so this
    # banner read and the CRM-Ops page loads collapse to ONE live mirror scan per TTL
    # instead of storming the HubSpot rate limit. A cached LIVE read is still live —
    # only the recomputation is skipped, never the values or the source labels.
    parity = parity_snapshot(
        repository,
        crm_adapter,
        program=program,
        ttl_seconds=params.crm_ops.snapshot_ttl_seconds,
    ).parity
    return CrmStatus(
        crm_mode=settings.crm_mode,
        kill_switch=settings.hubspot_kill_switch,
        effective_mode=effective_crm_mode(settings),
        token_configured=settings.hubspot_private_app_token is not None,
        calls_per_run_cap=settings.hubspot_calls_per_run_cap,
        parity_overall=parity.overall,
        parity_by_field=parity.by_field,
        data_confidence_banner=parity.overall < params.data_confidence.min_parity,
    )

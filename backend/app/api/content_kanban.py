"""Content production-tracker kanban — the Google-Sheet read+write surface (S-Sheets).

The thin HTTP composition over the :class:`~app.adapters.sheets.base.SheetsAdapter`
seam. The Content Owner's production tracker lives in a Google Sheet (their tool of
record); this router makes the cockpit's kanban a REAL two-way view of it:

  ``GET  /content/kanban``
    Read every content row through the adapter and return them grouped by the five
    canonical :data:`~app.adapters.sheets.base.STAGES`, plus an honest ``sync``
    block describing the effective seam state (``live`` vs ``simulate``). On the
    FIRST read of an EMPTY live sheet the adapter writes the header row + a seeded
    set (the demo's "reset to a clean, known state"), so the sheet and the cockpit
    start matching.

  ``POST /content/kanban``
    Upsert one row (by ``title``) and write it back to the sheet — a kanban move
    (same title, new ``stage``) rewrites that row in place; a new title appends.

INV-9: which impl is behind the seam (in-memory simulated vs live Sheets v4) is the
registry's call; this router depends only on the interface. INV-8: the live adapter
enforces a per-run call cap + honors the kill switch (degrade to simulate) — both
owned by the adapter/registry, not re-implemented here. The "SYNCED" label the UI
shows is derived from :func:`app.adapters.registry.effective_sheets_mode`, never a
hardcoded constant — when the seam is simulated it says so honestly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters import registry
from app.adapters.sheets.base import STAGES, ContentRow, SheetsAdapter, default_seed_rows
from app.api.deps import Principal, get_principal, get_settings_dep, get_sheets_adapter_dep
from app.core.settings import Settings

router = APIRouter(tags=["content-kanban"])

SheetsDep = Annotated[SheetsAdapter, Depends(get_sheets_adapter_dep)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _sync_block(settings: Settings) -> dict[str, object]:
    """The honest sync-status block the UI's pill renders (never a hardcoded label).

    ``mode`` is the EFFECTIVE seam (the registry's precedence — a kill-switched live
    reports ``simulate``), so a simulated seam is labeled honestly as simulated and
    only a genuine live seam claims to be synced to the real sheet.
    """
    mode = registry.effective_sheets_mode(settings)
    live = mode == "live"
    return {
        "mode": mode,
        "synced": live,
        "tab": settings.gsheets_tab if live else None,
        # Never leak the full sheet id in the simulate case; only surface it live.
        "sheet_id": settings.gsheets_sheet_id if live else None,
    }


def _grouped(rows: list[ContentRow]) -> list[dict[str, object]]:
    """Group rows into the five canonical kanban columns (stable stage order)."""
    by_stage: dict[str, list[dict[str, object]]] = {stage: [] for stage in STAGES}
    for row in rows:
        by_stage[row.stage].append(row.model_dump(mode="json"))
    return [{"stage": stage, "cards": by_stage[stage]} for stage in STAGES]


@router.get("/content/kanban", response_model=dict[str, object])
def get_kanban(
    adapter: SheetsDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> dict[str, object]:
    """Read the kanban from the sheet (seeding an empty live sheet on first read).

    Returns the rows both flat (``rows``) and grouped by stage (``columns``), the
    canonical ``stages`` order, and the honest ``sync`` block. ``ensure_seeded``
    writes the header + seed ONCE to an empty live sheet (no-op when it already has
    rows, and a no-op for the always-seeded simulated adapter).
    """
    rows = adapter.ensure_seeded(default_seed_rows())
    return {
        "stages": list(STAGES),
        "rows": [r.model_dump(mode="json") for r in rows],
        "columns": _grouped(rows),
        "sync": _sync_block(settings),
    }


@router.post("/content/kanban", response_model=dict[str, object])
def upsert_kanban_card(
    row: ContentRow,
    adapter: SheetsDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> dict[str, object]:
    """Upsert one card by ``title`` and write it back to the sheet (a move or an add).

    The request body is a validated :class:`ContentRow` (an unknown ``stage`` is a
    422 at the model edge — fail-closed). Returns the stored row + the sync block so
    the UI can confirm whether the write landed on the live sheet or the simulated
    one.
    """
    stored = adapter.upsert_row(row)
    return {"row": stored.model_dump(mode="json"), "sync": _sync_block(settings)}

"""The Google-Sheets content-tracker adapter family (INV-8/9; ARCHITECTURE §7).

Simulated (v1 default) and Live (Sheets v4) impls behind one
:class:`~app.adapters.sheets.base.SheetsAdapter` interface, selected at startup by
config in :mod:`app.adapters.registry`. The Content router depends only on the
interface (NFR-8 seam).
"""

from __future__ import annotations

from app.adapters.sheets.base import (
    SHEET_COLUMNS,
    STAGES,
    ContentRow,
    ContentStage,
    SheetsAdapter,
    SheetsBudgetExceededError,
    default_seed_rows,
)
from app.adapters.sheets.live import LiveSheetsAdapter
from app.adapters.sheets.simulated import SimulatedSheetsAdapter

__all__ = [
    "SHEET_COLUMNS",
    "STAGES",
    "ContentRow",
    "ContentStage",
    "SheetsAdapter",
    "SheetsBudgetExceededError",
    "default_seed_rows",
    "LiveSheetsAdapter",
    "SimulatedSheetsAdapter",
]

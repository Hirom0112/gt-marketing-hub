"""Production SheetsAdapter — live Google Sheets v4 behind the INV-8 cap (S-Sheets).

The production half of the Sheets seam: it reads and writes the Content Owner's real
spreadsheet over the Google Sheets v4 API, behind a **per-run call budget** (guard,
INV-8) that mirrors :class:`app.adapters.payments.live.LivePaymentsAdapter`. The
simulated impl stays the v1 default; this one is selected only when
``SHEETS_MODE=live`` with a configured sheet id + a readable service-account key and
no kill switch (see :mod:`app.adapters.registry`). The Content router changes zero
lines — it depends on the :class:`~app.adapters.sheets.base.SheetsAdapter`
interface, not this class.

The Sheets **service** is INJECTED (the duck-typed object the registry builds via
``googleapiclient.discovery.build("sheets", "v4", …)``) so this module imports NO
``google`` SDK and opens NO socket in a test — a unit test passes a tiny fake
service exposing ``spreadsheets().values().{get,update,append}().execute()``. All
config (spreadsheet id, tab, cap) is constructor-injected by the registry; this
class reads no settings/params itself. Every logical Google call passes through the
budgeted :meth:`_execute`, so the (cap+1)th call raises
:class:`~app.adapters.sheets.base.SheetsBudgetExceededError` rather than overspend
the metered, account-shared Sheets quota (INV-8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.adapters.sheets.base import (
    SHEET_COLUMNS,
    ContentRow,
    SheetsAdapter,
    SheetsBudgetExceededError,
)


class LiveSheetsAdapter(SheetsAdapter):
    """Production ``SheetsAdapter`` — live Sheets v4 reads/writes behind the INV-8 cap.

    Args:
        service: An injected, duck-typed Google Sheets service (the object returned
            by ``googleapiclient.discovery.build("sheets", "v4", …)``). Tests pass a
            fake exposing the same ``spreadsheets().values()`` chain.
        spreadsheet_id: The target spreadsheet id (injected; never read here).
        tab: The worksheet/tab name the rows live on (e.g. ``"Sheet1"``).
        calls_per_run_cap: The per-run outbound Sheets call budget (INV-8 guard).
    """

    def __init__(
        self,
        *,
        service: Any,
        spreadsheet_id: str,
        tab: str,
        calls_per_run_cap: int,
    ) -> None:
        self._service = service
        self._spreadsheet_id = spreadsheet_id
        self._tab = tab
        self._cap = calls_per_run_cap
        self._calls_made = 0

    # ------------------------------------------------------------------ I/O
    @property
    def _range(self) -> str:
        """The full A1:G data range for the configured tab (header + rows)."""
        return f"{self._tab}!A1:G"

    def _values(self) -> Any:
        """The ``spreadsheets().values()`` resource (no API call — just a builder)."""
        return self._service.spreadsheets().values()

    def _execute(self, request: Any) -> dict[str, Any]:
        """Run ONE budgeted Sheets request — the guard (INV-8) trips on the (cap+1)th.

        The budget is checked BEFORE ``.execute()``, so an exhausted budget never
        reaches the network (fail closed). ``request`` is the prepared
        ``values().get/update/append(...)`` object; this charges one unit and runs it.
        """
        if self._calls_made >= self._cap:
            raise SheetsBudgetExceededError(
                f"Google Sheets per-run call budget exhausted ({self._cap}); degrade "
                "to simulated (INV-8) rather than overspend the metered, "
                "account-shared Sheets quota."
            )
        self._calls_made += 1
        response: dict[str, Any] = request.execute()
        return response

    # --------------------------------------------------------------- helpers
    def _raw_rows(self) -> list[list[str]]:
        """Fetch the raw cell grid for the tab (one budgeted ``values().get``)."""
        resp = self._execute(
            self._values().get(spreadsheetId=self._spreadsheet_id, range=self._range)
        )
        values = resp.get("values", [])
        return [[str(cell) for cell in row] for row in values]

    @staticmethod
    def _has_header(grid: Sequence[Sequence[str]]) -> bool:
        """True when the first grid row is our header row (first cell == ``title``)."""
        return bool(grid) and bool(grid[0]) and str(grid[0][0]).strip().lower() == "title"

    # --------------------------------------------------------------- interface
    def read_rows(self) -> list[ContentRow]:
        """Read the content rows (header skipped) over the budgeted Sheets API (INV-8)."""
        grid = self._raw_rows()
        start = 1 if self._has_header(grid) else 0
        rows: list[ContentRow] = []
        for cells in grid[start:]:
            if not any(str(c).strip() for c in cells):
                continue  # skip fully-blank trailing rows
            rows.append(ContentRow.from_cells(cells))
        return rows

    def upsert_row(self, row: ContentRow) -> ContentRow:
        """Insert ``row`` or update the existing same-``title`` row, writing back (INV-8).

        Reads the grid (one budgeted call) to locate any row with the same title; on
        a hit it overwrites that row in place (a budgeted ``values().update``), else
        it appends a new row (a budgeted ``values().append``). A move (same title,
        new stage) therefore rewrites one row rather than duplicating it.
        """
        grid = self._raw_rows()
        has_header = self._has_header(grid)
        data_start = 1 if has_header else 0
        cells = row.to_cells()

        for offset, existing in enumerate(grid[data_start:]):
            if existing and str(existing[0]).strip() == row.title:
                # Sheet rows are 1-indexed; this data row sits at grid index
                # (data_start + offset) ⇒ A1 row number (data_start + offset + 1).
                row_number = data_start + offset + 1
                target = f"{self._tab}!A{row_number}"
                self._execute(
                    self._values().update(
                        spreadsheetId=self._spreadsheet_id,
                        range=target,
                        valueInputOption="RAW",
                        body={"values": [cells]},
                    )
                )
                return row

        # No existing row with this title ⇒ append a fresh row at the table's end.
        self._execute(
            self._values().append(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._tab}!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [cells]},
            )
        )
        return row

    def ensure_seeded(self, seed: Sequence[ContentRow]) -> list[ContentRow]:
        """Write the header + ``seed`` ONCE to an empty sheet; else leave it untouched.

        The demo's "reset to a clean, known state": a fresh live sheet (no data rows)
        gets the header row and the seeded set written in a single budgeted
        ``values().update`` so the sheet and the cockpit start matching. A sheet that
        already holds rows is never clobbered — the operator's edits stand.
        """
        existing = self.read_rows()
        if existing:
            return existing
        body = {"values": [list(SHEET_COLUMNS)] + [r.to_cells() for r in seed]}
        self._execute(
            self._values().update(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._tab}!A1",
                valueInputOption="RAW",
                body=body,
            )
        )
        return list(seed)

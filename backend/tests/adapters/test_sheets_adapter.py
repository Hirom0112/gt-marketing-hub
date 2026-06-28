"""Sheets adapter — simulated rows + the live INV-8 cap/kill-switch (S-Sheets).

RED-first targets:

- the SIMULATED adapter reads its deterministic seed, upserts a kanban MOVE in
  place (same title, new stage), appends a new title, and seeds an empty store —
  all with NO network (INV-9, structural: the class holds no Google service);
- the ContentRow model rejects an unknown ``stage`` (fail-closed at the edge);
- ``effective_sheets_mode`` degrades live→simulate under the kill switch and the
  registry fails loud on a live-without-sheet-id misconfig (the CRM/Stripe
  precedent); and
- the LIVE adapter's (cap+1)th Google call raises ``SheetsBudgetExceededError``
  (INV-8) and its read/upsert/seed write the right cells — all against a FAKE
  service, no real Sheets call.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.adapters.registry import effective_sheets_mode, get_sheets_adapter
from app.adapters.sheets.base import (
    SHEET_COLUMNS,
    STAGES,
    ContentRow,
    SheetsBudgetExceededError,
    default_seed_rows,
)
from app.adapters.sheets.live import LiveSheetsAdapter
from app.adapters.sheets.simulated import SimulatedSheetsAdapter
from app.core.settings import Settings


# ----------------------------------------------------------------- simulated impl
def test_simulated_seeded_reads_deterministic_rows() -> None:
    """The seeded simulated adapter returns the deterministic demo rows (INV-9)."""
    adapter = SimulatedSheetsAdapter.seeded()
    rows = adapter.read_rows()
    assert rows == default_seed_rows()
    # Every seeded row carries a valid kanban stage.
    assert {r.stage for r in rows} <= set(STAGES)


def test_simulated_upsert_move_updates_in_place() -> None:
    """A same-title upsert (a kanban MOVE) rewrites the row without changing order."""
    adapter = SimulatedSheetsAdapter.seeded()
    before = adapter.read_rows()
    target = before[0]
    moved = target.model_copy(update={"stage": "Live"})

    returned = adapter.upsert_row(moved)
    assert returned.stage == "Live"

    after = adapter.read_rows()
    assert len(after) == len(before)  # a move never adds a row
    assert after[0].title == target.title  # order is stable
    assert after[0].stage == "Live"


def test_simulated_upsert_new_title_appends() -> None:
    """A new title appends a fresh row."""
    adapter = SimulatedSheetsAdapter.seeded()
    n = len(adapter.read_rows())
    fresh = ContentRow(
        title="Brand-new explainer",
        type="article",
        stage="Backlog",
        owner="the Content Owner",
        channel="Substack",
    )
    adapter.upsert_row(fresh)
    rows = adapter.read_rows()
    assert len(rows) == n + 1
    assert rows[-1].title == "Brand-new explainer"


def test_simulated_ensure_seeded_only_when_empty() -> None:
    """``ensure_seeded`` seeds a BARE store but never clobbers a populated one."""
    bare = SimulatedSheetsAdapter()
    assert bare.read_rows() == []
    seeded = bare.ensure_seeded(default_seed_rows())
    assert seeded == default_seed_rows()

    # Re-seeding (now populated) is a no-op that returns the SAME rows.
    again = bare.ensure_seeded([])
    assert again == default_seed_rows()


def test_content_row_rejects_unknown_stage() -> None:
    """An unknown ``stage`` is rejected at the model edge (fail-closed)."""
    with pytest.raises(ValidationError):
        ContentRow(title="x", type="article", stage="Published", owner="o", channel="X")  # type: ignore[arg-type]
    # ...and parsing a hand-edited sheet row with a bad stage also raises.
    with pytest.raises(ValidationError):
        ContentRow.from_cells(["x", "article", "Nonsense", "o", "X", "", ""])


# --------------------------------------------------------- registry precedence
def test_effective_sheets_mode_kill_switch_and_default() -> None:
    """Kill switch degrades live→simulate; default is simulate; live is live."""
    killed = Settings(sheets_mode="live", gsheets_sheet_id="sheet_x", sheets_kill_switch=True)
    assert effective_sheets_mode(killed) == "simulate"

    live = Settings(sheets_mode="live", gsheets_sheet_id="sheet_x", sheets_kill_switch=False)
    assert effective_sheets_mode(live) == "live"

    assert effective_sheets_mode(Settings(sheets_mode="simulate")) == "simulate"


def test_get_sheets_adapter_default_is_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no SHEETS_MODE the registry returns the seeded simulated adapter."""
    monkeypatch.delenv("SHEETS_MODE", raising=False)
    monkeypatch.delenv("GSHEETS_SHEET_ID", raising=False)
    adapter = get_sheets_adapter()
    assert isinstance(adapter, SimulatedSheetsAdapter)
    assert adapter.read_rows() == default_seed_rows()


def test_get_sheets_adapter_live_without_sheet_id_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SHEETS_MODE=live`` with no sheet id is a misconfig ⇒ RuntimeError (INV-9)."""
    monkeypatch.setenv("SHEETS_MODE", "live")
    monkeypatch.delenv("GSHEETS_SHEET_ID", raising=False)
    with pytest.raises(RuntimeError):
        get_sheets_adapter()


# --------------------------------------------------------------- live impl (fake)
class _Req:
    """A prepared Sheets request whose ``execute`` runs the captured thunk."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def execute(self) -> dict[str, Any]:
        return self._fn()


def _parse_start_row(a1: str) -> int:
    """Parse the 1-based start row out of an ``A1`` range like ``"Sheet1!A3"``."""
    cell = a1.split("!", 1)[1]
    return int("".join(ch for ch in cell if ch.isdigit()))


class _FakeValues:
    """An in-memory stand-in for ``spreadsheets().values()`` — records the grid."""

    def __init__(self, grid: list[list[str]]) -> None:
        self.grid = grid

    def get(self, *, spreadsheetId: str, range: str) -> _Req:  # noqa: A002 - mirror Google kwarg
        return _Req(lambda: {"values": [list(r) for r in self.grid]})

    def update(self, *, spreadsheetId: str, range: str, valueInputOption: str, body: dict) -> _Req:  # noqa: A002
        def do() -> dict[str, Any]:
            start = _parse_start_row(range)
            for i, row in enumerate(body["values"]):
                idx = start - 1 + i
                while len(self.grid) <= idx:
                    self.grid.append([])
                self.grid[idx] = list(row)
            return {"updatedRows": len(body["values"])}

        return _Req(do)

    def append(  # noqa: A002
        self,
        *,
        spreadsheetId: str,
        range: str,
        valueInputOption: str,
        insertDataOption: str,
        body: dict,
    ) -> _Req:
        def do() -> dict[str, Any]:
            for row in body["values"]:
                self.grid.append(list(row))
            return {"updates": {"updatedRows": len(body["values"])}}

        return _Req(do)


class _FakeService:
    """A duck-typed Sheets service exposing the ``spreadsheets().values()`` chain."""

    def __init__(self, grid: list[list[str]]) -> None:
        self._values = _FakeValues(grid)

    def spreadsheets(self) -> _FakeService:
        return self

    def values(self) -> _FakeValues:
        return self._values


def _live(grid: list[list[str]], *, cap: int = 50) -> LiveSheetsAdapter:
    return LiveSheetsAdapter(
        service=_FakeService(grid), spreadsheet_id="sheet_x", tab="Sheet1", calls_per_run_cap=cap
    )


def test_live_cap_trips_on_over_budget() -> None:
    """The (cap+1)th Google call raises SheetsBudgetExceededError (INV-8)."""
    adapter = _live([], cap=2)
    # ensure_seeded on an empty sheet = read (get) + write (update) = 2 calls (at cap).
    adapter.ensure_seeded(default_seed_rows())
    # A further read is the 3rd call ⇒ over budget ⇒ fail closed.
    with pytest.raises(SheetsBudgetExceededError):
        adapter.read_rows()


def test_live_ensure_seeded_writes_header_and_rows() -> None:
    """An empty live sheet gets the header + seed written once (the clean known state)."""
    grid: list[list[str]] = []
    adapter = _live(grid, cap=10)
    seeded = adapter.ensure_seeded(default_seed_rows())
    assert seeded == default_seed_rows()
    # The grid now starts with the header row, then one row per seed piece.
    assert grid[0] == list(SHEET_COLUMNS)
    assert len(grid) == 1 + len(default_seed_rows())


def test_live_read_and_upsert_writeback() -> None:
    """Live read skips the header; a move rewrites in place; a new title appends."""
    grid = [
        list(SHEET_COLUMNS),
        ["Advisor Series", "video", "Backlog", "the Content Owner", "YouTube", "u1", "Jul 18"],
        ["ESA thread", "social", "Backlog", "Pam", "X", "u2", "Jul 12"],
    ]
    adapter = _live(grid, cap=20)
    assert [r.title for r in adapter.read_rows()] == ["Advisor Series", "ESA thread"]

    # MOVE: same title, new stage ⇒ row 2 (grid index 1) is rewritten in place.
    adapter.upsert_row(
        ContentRow(
            title="Advisor Series",
            type="video",
            stage="Live",
            owner="the Content Owner",
            channel="YouTube",
            utm="u1",
            target_date="Jul 18",
        )
    )
    assert grid[1][2] == "Live"
    assert len(grid) == 3  # no new row added by a move

    # ADD: a new title appends a fresh row.
    adapter.upsert_row(
        ContentRow(title="Fresh", type="article", stage="Drafting", owner="o", channel="Substack")
    )
    assert len(grid) == 4
    assert grid[-1][0] == "Fresh"

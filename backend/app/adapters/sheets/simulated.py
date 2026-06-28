"""Simulated SheetsAdapter — in-memory deterministic rows, no network (INV-9).

The v1 default impl of the Sheets boundary. It holds the content rows in an
in-memory list seeded from :func:`app.adapters.sheets.base.default_seed_rows`, so
``read_rows`` / ``upsert_row`` behave exactly like the live impl WITHOUT a Google
client — "no network" is a structural property (this class holds no service and
imports no ``google`` SDK), provable from the source alone. This module touches no
``core/`` state and no ``anthropic`` client.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.adapters.sheets.base import ContentRow, SheetsAdapter, default_seed_rows


class SimulatedSheetsAdapter(SheetsAdapter):
    """In-memory content-row store — deterministic, no I/O (INV-9).

    Args:
        rows: The initial rows. Defaults to an empty store; use :meth:`seeded` for
            the demo's clean known state.
    """

    def __init__(self, rows: Sequence[ContentRow] | None = None) -> None:
        # Keyed by title (the natural upsert key) but order-preserving, so read_rows
        # returns a stable, deterministic order (seed order, then insertion order).
        self._rows: dict[str, ContentRow] = {}
        for row in rows or []:
            self._rows[row.title] = row

    @classmethod
    def seeded(cls) -> SimulatedSheetsAdapter:
        """A simulated adapter pre-loaded with the deterministic demo seed (INV-1)."""
        return cls(default_seed_rows())

    def read_rows(self) -> list[ContentRow]:
        """Return the in-memory rows in stable order (no network; INV-9)."""
        return list(self._rows.values())

    def upsert_row(self, row: ContentRow) -> ContentRow:
        """Insert ``row`` or update the row with the same ``title`` in place (INV-9).

        A same-title upsert (e.g. a kanban move that changed only ``stage``) replaces
        the stored row WITHOUT changing its position, so the board order is stable
        across moves. A new title appends.
        """
        self._rows[row.title] = row
        return row

    def ensure_seeded(self, seed: Sequence[ContentRow]) -> list[ContentRow]:
        """Seed only when EMPTY, then return current rows (mirrors the live impl).

        A normally-constructed simulated adapter is seeded at build time, so this is
        a no-op returning the current rows; a bare adapter (constructed with no rows)
        gets ``seed`` written once, matching the live "seed an empty sheet" behavior.
        """
        if not self._rows:
            for row in seed:
                self._rows[row.title] = row
        return self.read_rows()

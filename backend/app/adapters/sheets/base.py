"""The Google-Sheets content boundary — interface + the typed row (INV-8/9).

This is the abstract half of the §7-style ``SheetsAdapter`` seam — the Content
Owner's production tracker lives in a Google Sheet (their tool of record), and the
cockpit reads/writes it through this boundary. Two impls —
:class:`~app.adapters.sheets.simulated.SimulatedSheetsAdapter` (v1 default,
in-memory deterministic rows, no network; INV-9) and
:class:`~app.adapters.sheets.live.LiveSheetsAdapter` (real Sheets v4 behind the
INV-8 per-run call cap + the registry kill switch) — are selected at startup by
config in :mod:`app.adapters.registry`. The Content router depends only on this
interface, never a concrete impl.

The sheet is a flat grid: a fixed HEADER row (:data:`SHEET_COLUMNS`) then one
content piece per row. Rows are keyed for upsert by their ``title`` (a content
piece's natural key) — moving a kanban card rewrites that row's ``stage`` in place;
adding a card appends a new row. The five kanban stages are :data:`STAGES` (the
canonical backend order); a row with any other stage is rejected at the model edge
(fail-closed), so a hand-edit in the sheet that typos a stage never silently lands
in an unknown column.

No ``google`` SDK is imported here (the live impl takes an injected, duck-typed
Sheets service so this module — and the live adapter — stay importable and unit
testable with NO Google dependency and NO network; INV-9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The five kanban stages, in canonical left-to-right column order. A ContentRow's
# ``stage`` MUST be one of these (validated at the model edge) — the one home for
# the stage vocabulary the sheet, the API, and the UI all agree on.
ContentStage = Literal["Backlog", "Drafting", "Review", "Scheduled", "Live"]
STAGES: tuple[ContentStage, ...] = ("Backlog", "Drafting", "Review", "Scheduled", "Live")

# The sheet's fixed column order (the header row written on first seed). The
# adapters serialize/parse a ContentRow against THIS order, so the on-sheet layout
# and the typed row never drift. One canonical home (both impls import it).
SHEET_COLUMNS: tuple[str, ...] = (
    "title",
    "type",
    "stage",
    "owner",
    "channel",
    "utm",
    "target_date",
)


class SheetsBudgetExceededError(RuntimeError):
    """Guard (INV-8): the per-run outbound Google Sheets call budget was exhausted.

    Mirrors :class:`app.adapters.payments.base.PaymentsBudgetExceededError`: a
    breach fails closed here rather than silently hammering the metered Sheets API
    (whose quota is shared with the Content Owner's real spreadsheet). The registry
    kill switch is the coarser sibling (degrade live→simulate); this is the per-run
    ceiling.
    """


class ContentRow(BaseModel):
    """One content piece — a single row of the production tracker (INV-1 synthetic).

    Attributes:
        title: The piece's name — the natural upsert key (one row per title).
        type: The content type (e.g. ``"video"``, ``"article"``, ``"social"``).
        stage: The kanban column — one of :data:`STAGES` (validated; fail-closed).
        owner: Who owns the piece (a synthetic name / role label).
        channel: The publish channel (e.g. ``"Substack"``, ``"X"``).
        utm: The UTM campaign tag for the piece (free text; may be empty).
        target_date: The due / target publish date (free text, e.g. ``"Jul 18"``).
    """

    model_config = ConfigDict(frozen=True)

    title: str
    type: str
    stage: ContentStage
    owner: str
    channel: str
    utm: str = ""
    target_date: str = ""

    def to_cells(self) -> list[str]:
        """Serialize the row to a flat cell list in :data:`SHEET_COLUMNS` order."""
        return [
            self.title,
            self.type,
            self.stage,
            self.owner,
            self.channel,
            self.utm,
            self.target_date,
        ]

    @classmethod
    def from_cells(cls, cells: Sequence[str]) -> ContentRow:
        """Parse one sheet row (cells in :data:`SHEET_COLUMNS` order) into a ContentRow.

        Tolerant of short rows (trailing empty cells the Sheets API omits): missing
        cells default to ``""``. The ``stage`` is still validated against
        :data:`STAGES` by pydantic — a row carrying an unknown stage raises, so a
        bad hand-edit fails loud rather than landing in a phantom column.
        """
        padded = list(cells) + [""] * (len(SHEET_COLUMNS) - len(cells))
        return cls(
            title=padded[0],
            type=padded[1],
            stage=padded[2],  # type: ignore[arg-type]  # validated by the Literal
            owner=padded[3],
            channel=padded[4],
            utm=padded[5],
            target_date=padded[6],
        )


class SheetsAdapter(ABC):
    """The Google-Sheets content-tracker boundary (INV-8/9).

    Two impls — Simulated (v1 default) and Live (Sheets v4) — selected by config in
    :mod:`app.adapters.registry`. The Content router depends only on this interface.
    """

    @abstractmethod
    def read_rows(self) -> list[ContentRow]:
        """Read every content row from the sheet (excludes the header).

        The simulated impl returns its in-memory rows; the live impl reads the live
        range behind the INV-8 budget. An empty / header-only sheet yields ``[]``.
        """

    @abstractmethod
    def upsert_row(self, row: ContentRow) -> ContentRow:
        """Insert ``row`` or update the existing row with the same ``title``.

        Moving a kanban card (same title, new stage) updates in place; a new title
        appends. Returns the stored row. The live impl writes back to the sheet
        behind the INV-8 budget.
        """

    @abstractmethod
    def ensure_seeded(self, seed: Sequence[ContentRow]) -> list[ContentRow]:
        """Return current rows, seeding the sheet with ``seed`` only if it is EMPTY.

        This is the "reset to a clean, known state" the demo relies on: a fresh
        live sheet gets its header row + the seeded set written ONCE so the sheet
        and the cockpit start in the same state. A non-empty sheet is left untouched
        (the operator's edits are never clobbered). The simulated impl is seeded at
        construction, so this is a no-op that returns its current rows.
        """


def default_seed_rows() -> list[ContentRow]:
    """The deterministic seed set — the demo's clean, known starting kanban (INV-1).

    Synthetic content pieces shaped like the real tracker, one per stage spread, so
    a fresh sheet (or the simulated adapter) starts in a state that matches the
    cockpit. These are demo FIXTURES (like the synthetic cohort), not tunables — no
    eval threshold / weight / amount lives here (INV-11 governs those).
    """
    return [
        ContentRow(
            title="Advisor Series",
            type="video",
            stage="Backlog",
            owner="the Content Owner",
            channel="YouTube",
            utm="advisor_series",
            target_date="Jul 18",
        ),
        ContentRow(
            title="ESA explainer thread",
            type="social",
            stage="Backlog",
            owner="Pamela Hobart",
            channel="X",
            utm="esa_explainer",
            target_date="Jul 12",
        ),
        ContentRow(
            title="Thailand videographer shoot",
            type="video",
            stage="Drafting",
            owner="the Content Owner",
            channel="Instagram",
            utm="thailand_shoot",
            target_date="Jul 15",
        ),
        ContentRow(
            title="AGL podcast with Pam",
            type="podcast",
            stage="Drafting",
            owner="Pamela Hobart",
            channel="Podcast",
            utm="agl_podcast",
            target_date="Jul 11",
        ),
        ContentRow(
            title="Mastery-based learning op-ed",
            type="article",
            stage="Review",
            owner="Pamela Hobart",
            channel="Substack",
            utm="mastery_oped",
            target_date="Jul 10",
        ),
        ContentRow(
            title="K-2 sweet-spot carousel",
            type="social",
            stage="Scheduled",
            owner="the Content Owner",
            channel="Instagram",
            utm="k2_carousel",
            target_date="Jul 06",
        ),
        ContentRow(
            title="Alpha-X day-in-the-life",
            type="video",
            stage="Live",
            owner="the Content Owner",
            channel="YouTube",
            utm="alphax_ditl",
            target_date="Jul 02",
        ),
    ]

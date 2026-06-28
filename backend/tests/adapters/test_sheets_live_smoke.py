"""OPTIONAL live smoke — a READ-ONLY round-trip against the real Google Sheet.

Skipped automatically when the service-account key file is absent (CI / fresh
checkout), so the suite stays green offline. When the key IS present it builds the
REAL Sheets v4 service exactly as the registry does and performs a single READ
(``read_rows``) to prove the live path wires up end-to-end. It writes NOTHING — it
never leaves test data in the sheet (the brief's constraint).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The git-ignored service-account key (machine-local; never committed) and the
# verified target spreadsheet. backend/tests/adapters/ → parents[2] is backend/.
_KEY_PATH = Path(__file__).resolve().parents[2] / ".secrets" / "gsheets-sa.json"
_SHEET_ID = "1OTU5iMO9A3WYRuKCRjSr8ZcU1EFhAf6S8fGZCvFwPf8"
_TAB = "Sheet1"

pytestmark = pytest.mark.skipif(
    not _KEY_PATH.is_file(), reason="no Google service-account key present (offline / CI)"
)


def test_live_read_round_trips() -> None:
    """Build the real Sheets service and READ the tab (read-only; leaves no data)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    from app.adapters.sheets.live import LiveSheetsAdapter

    creds = service_account.Credentials.from_service_account_file(
        str(_KEY_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    adapter = LiveSheetsAdapter(
        service=service, spreadsheet_id=_SHEET_ID, tab=_TAB, calls_per_run_cap=5
    )

    rows = adapter.read_rows()
    # The read returns a list of typed rows (possibly empty) — the live path works.
    assert isinstance(rows, list)

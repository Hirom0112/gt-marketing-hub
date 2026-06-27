"""Open Data adapter family — Seeded default, Live REST, registry selector (E1).

The four RED-first targets (TODO_v2.md §E1) for the tryopendata.ai Texas-education
enrichment seam:

- ``test_seeded_district_query_returns_typed_rows`` — the v1-default
  :class:`SeededOpenDataAdapter` (INV-9; pure, no httpx) returns a typed
  :class:`DistrictEnrichment` for BOTH its A-rated and its D/F-rated fixture
  district, so the later decision-change core has both poles.
- ``test_live_adapter_maps_tea_columns`` — via ``httpx.MockTransport`` (no live
  call), a captured ``tea/*`` query-response row maps onto a
  :class:`DistrictEnrichment`; the POST hits ``/v1/query`` with the ``Bearer
  od_live_`` key and the column mapping is correct.
- ``test_open_data_cap_degrades_to_seeded`` — ``effective_open_data_mode`` degrades
  live→simulate under the kill switch (INV-8, NOT fail), and the live adapter's
  (cap+1)th query raises :class:`OpenDataBudgetExceededError` (the per-run guard).
- ``test_live_no_key_fails_loud`` — ``OPEN_DATA_MODE=live`` with NO key fails loud
  at the registry (INV-9 misconfig), mirroring ``get_crm_adapter`` /
  ``get_payments_adapter``.

All tests run offline — INV-1 synthetic, no real network, no live write.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.adapters.open_data.base import DistrictEnrichment, OpenDataBudgetExceededError
from app.adapters.open_data.live import LiveOpenDataAdapter
from app.adapters.open_data.seeded import SeededOpenDataAdapter
from app.adapters.registry import effective_open_data_mode, get_open_data_adapter
from app.core.params import OpenDataDatasets, load_params
from app.core.settings import Settings

_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _datasets() -> OpenDataDatasets:
    """The ``open_data.datasets`` params block the registry injects (INV-11)."""
    return load_params(_EXAMPLE_PARAMS).open_data.datasets


# A synthetic TEA district id (aggregate/district-level only — INV-1/INV-6).
_DISTRICT_ID = "101912"


def _tea_row_handler(request: httpx.Request) -> httpx.Response:
    """A scripted tryopendata.ai — returns one synthetic ``tea/*`` row for any query."""
    return httpx.Response(
        200,
        json={
            "rows": [
                {
                    "district_id": _DISTRICT_ID,
                    "rating": "A",
                    "staar_proficiency": 0.78,
                    "amount": 10500.0,
                    "enrollment": 1500,
                }
            ]
        },
    )


def _live_adapter(*, cap: int) -> LiveOpenDataAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(_tea_row_handler),
        base_url="https://api.tryopendata.ai",
    )
    return LiveOpenDataAdapter(
        client=client,
        api_key="od_live_synthetic",
        datasets=_datasets(),
        per_run_query_cap=cap,
    )


# ---------------------------------------------------------------------- seeded
def test_seeded_district_query_returns_typed_rows() -> None:
    """The seeded adapter returns a typed enrichment for both the A and D/F poles."""
    adapter = SeededOpenDataAdapter()

    a = adapter.district_enrichment(SeededOpenDataAdapter.A_RATED_DISTRICT)
    assert isinstance(a, DistrictEnrichment)
    assert a.district_id == SeededOpenDataAdapter.A_RATED_DISTRICT
    assert a.d_rating == "A"
    assert 0.0 <= a.staar_proficiency <= 1.0
    assert a.per_pupil_spend > 0
    assert a.enrollment >= 1

    low = adapter.district_enrichment(SeededOpenDataAdapter.LOW_RATED_DISTRICT)
    assert isinstance(low, DistrictEnrichment)
    assert low.district_id == SeededOpenDataAdapter.LOW_RATED_DISTRICT
    assert low.d_rating in {"D", "F"}
    assert 0.0 <= low.staar_proficiency <= 1.0

    # Deterministic — a re-read (and a fresh instance) gives the same enrichment.
    again = SeededOpenDataAdapter().district_enrichment(SeededOpenDataAdapter.A_RATED_DISTRICT)
    assert again == a


# ------------------------------------------------------------------------ live
def test_live_adapter_maps_tea_columns() -> None:
    """A captured ``tea/*`` row maps onto a DistrictEnrichment via /v1/query + Bearer."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return _tea_row_handler(request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.tryopendata.ai"
    )
    adapter = LiveOpenDataAdapter(
        client=client,
        api_key="od_live_synthetic",
        datasets=_datasets(),
        per_run_query_cap=25,
    )

    enrichment = adapter.district_enrichment(_DISTRICT_ID)

    # The POST hits /v1/query with the Bearer od_live_ key.
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/query")
    assert captured["auth"] == "Bearer od_live_synthetic"
    # The SQL body queries the tea/* datasets for the district.
    assert "tea/" in captured["body"]
    assert _DISTRICT_ID in captured["body"]

    # The tea/* columns map correctly onto the typed enrichment.
    assert isinstance(enrichment, DistrictEnrichment)
    assert enrichment.district_id == _DISTRICT_ID
    assert enrichment.d_rating == "A"
    assert enrichment.staar_proficiency == 0.78
    assert enrichment.per_pupil_spend == 10500.0
    assert enrichment.enrollment == 1500


# ----------------------------------------------------------- cap + degrade INV-8
def test_open_data_cap_degrades_to_seeded() -> None:
    """Kill switch degrades live→simulate (INV-8); the (cap+1)th query raises."""
    # Degrade: live + key + kill switch ⇒ effective simulate (NOT fail-loud).
    settings = Settings(
        open_data_mode="live",
        open_data_api_key="od_live_synthetic",
        open_data_kill_switch=True,
    )
    assert effective_open_data_mode(settings) == "simulate"

    # Guard: the per-run query budget trips on the (cap+1)th call (INV-8).
    adapter = _live_adapter(cap=1)
    adapter.district_enrichment(_DISTRICT_ID)  # 1st query — within budget
    with pytest.raises(OpenDataBudgetExceededError):
        adapter.district_enrichment(_DISTRICT_ID)  # 2nd query — over budget


# ----------------------------------------------------------- live, no key, loud
def test_live_no_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPEN_DATA_MODE=live with no key is a misconfig — the registry fails loud (INV-9)."""
    monkeypatch.setenv("OPEN_DATA_MODE", "live")
    monkeypatch.delenv("OPEN_DATA_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_open_data_adapter()

    # And the pure precedence reports the live INTENT (not a false "simulate").
    settings = Settings(open_data_mode="live", open_data_api_key=None)
    assert effective_open_data_mode(settings) == "live"


# --------------------------------------------------------------- registry default
def test_registry_defaults_to_seeded() -> None:
    """The v1 default (OPEN_DATA_MODE unset ⇒ simulate) selects the seeded adapter (INV-9)."""
    settings = Settings()
    assert effective_open_data_mode(settings) == "simulate"
    assert isinstance(get_open_data_adapter(), SeededOpenDataAdapter)

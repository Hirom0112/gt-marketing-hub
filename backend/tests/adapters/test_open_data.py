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


def _scripted(value: object) -> httpx.Response:
    """A tryopendata.ai ``/v1/query`` result: ``{columns, rows}`` with one scalar."""
    return httpx.Response(200, json={"columns": ["v"], "rows": [[value]]})


def _tea_row_handler(request: httpx.Request) -> httpx.Response:
    """A scripted tryopendata.ai — routes by the SQL to the right ``{columns,rows}``.

    The live adapter issues FOUR district-level queries (rating, enrollment, finance,
    STAAR); this returns synthetic values chosen so the mapped enrichment is exactly
    ``A / enrollment 1500 / per-pupil $10,500 / STAAR 0.78`` (finance SUM 15,750,000
    ÷ 1500 = 10,500; STAAR 78% → 0.78). INV-1 synthetic, offline.
    """
    import json as _json

    sql = _json.loads(request.content.decode("utf-8"))["sql"]
    if "d_rating" in sql:
        return _scripted("A")
    if "SUM(CAST(enrollment" in sql:
        return _scripted(1500)
    if "object_code BETWEEN" in sql:
        return _scripted(15_750_000.0)
    if "metric_code LIKE" in sql:
        return _scripted(78.0)
    return _scripted(None)


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
    """The four live ``tea/*`` queries map onto a DistrictEnrichment via /v1/query + Bearer."""
    import json as _json

    captured: dict[str, object] = {"bodies": [], "urls": [], "methods": [], "auths": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["urls"].append(str(request.url))  # type: ignore[union-attr]
        captured["methods"].append(request.method)  # type: ignore[union-attr]
        captured["auths"].append(request.headers.get("Authorization", ""))  # type: ignore[union-attr]
        captured["bodies"].append(request.content.decode("utf-8"))  # type: ignore[union-attr]
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

    # Every POST hits /v1/query with the Bearer od_live_ key and the {"sql": …} body.
    assert captured["methods"] == ["POST"] * 4
    assert all(u.endswith("/v1/query") for u in captured["urls"])  # type: ignore[union-attr]
    assert set(captured["auths"]) == {"Bearer od_live_synthetic"}  # type: ignore[arg-type]
    bodies = captured["bodies"]
    assert all("sql" in _json.loads(b) and "query" not in _json.loads(b) for b in bodies)  # type: ignore[union-attr]
    # The SQL queries the tea/* datasets for the district (both id forms appear).
    joined = " ".join(bodies)  # type: ignore[arg-type]
    assert "tea/" in joined and _DISTRICT_ID in joined

    # The four queries map correctly onto the typed enrichment.
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

    # Guard: the per-run query budget trips on the (cap+1)th /v1/query (INV-8). One
    # enrichment now issues FOUR queries, so cap=4 lets exactly one enrichment
    # through; the next enrichment's first query (the 5th) is over budget.
    adapter = _live_adapter(cap=4)
    adapter.district_enrichment(_DISTRICT_ID)  # queries 1–4 — within budget
    with pytest.raises(OpenDataBudgetExceededError):
        adapter.district_enrichment(_DISTRICT_ID)  # query 5 — over budget


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

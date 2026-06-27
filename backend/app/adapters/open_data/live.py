"""Production OpenDataAdapter — live tryopendata.ai over httpx (E1; INV-1/8/9).

The production half of the Open Data seam: it queries the **real**
``tryopendata.ai`` Texas-education datasets over the documented REST endpoint
``POST https://api.tryopendata.ai/v1/query`` (SQL) with an ``Authorization: Bearer
od_live_<key>`` header (RESEARCH_v2 §II.3 — VERIFIED; keyless ``/v1/query`` 401s
with ``auth_required``). It maps the returned aggregate ``tea/*`` columns (the A–F
accountability rating, STAAR proficiency, PEIMS per-pupil ``amount``, enrollment)
into a typed :class:`DistrictEnrichment`. District-level only — no child-keyed row
ever crosses this boundary (INV-1/INV-6).

**Dependency-budget decision (ASSUMPTIONS A-41).** This deliberately does NOT use
the ``tryopendata`` PyPI SDK (the ``opendata`` package's ``OpenData`` client). The
backend's runtime dep budget is a hard ≤15 (currently 7/15; TECH_STACK §4.1) and the
platform API is v0.1.0-maturity, so — EXACTLY mirroring
:class:`app.adapters.hubspot.live_adapter.LiveHubSpotCRMAdapter` and
:class:`app.adapters.payments.live.LivePaymentsAdapter` — outbound calls are plain
``httpx`` requests over an **injected** ``httpx.Client`` (tests pass an
``httpx.MockTransport``; no real socket, no live read). Zero new dependencies. The
functional contract (live Texas-district query → typed enrichment, INV-8 cap) is
identical to the SDK path.

INV-8: every call goes through ONE budgeted :meth:`_request`; the (cap+1)th query
raises :class:`OpenDataBudgetExceededError` (the per-run guard, mirroring HubSpot's
``_request``). All config (key, datasets, cap) is constructor-injected by the
registry — this class reads no settings/params itself.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.open_data.base import (
    DistrictEnrichment,
    OpenDataAdapter,
    OpenDataBudgetExceededError,
)
from app.core.params import OpenDataDatasets

# The tryopendata.ai REST query route (the live API surface, not a tunable — the
# product's own fixed path; INV-11 governs OUR knobs, not a third party's URLs).
_QUERY_PATH = "/v1/query"


class LiveOpenDataAdapter(OpenDataAdapter):
    """Production ``OpenDataAdapter`` — live tryopendata.ai reads behind the cap (E1).

    Args:
        client: An injected ``httpx.Client`` (tests pass one wired to a
            ``httpx.MockTransport``). Its ``base_url`` should be
            ``https://api.tryopendata.ai``.
        api_key: The free ``od_live_…`` Bearer key data access requires (a keyless
            ``/v1/query`` 401s with ``auth_required``; RESEARCH_v2 §II.3).
        datasets: The injected ``open_data.datasets`` params block — the ``tea/*``
            dataset slugs the SQL queries (INV-11; never a code literal).
        per_run_query_cap: The per-run query budget (INV-8 guard); the (cap+1)th
            query raises :class:`OpenDataBudgetExceededError`.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: str,
        datasets: OpenDataDatasets,
        per_run_query_cap: int,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._datasets = datasets
        self._cap = per_run_query_cap
        self._queries_made = 0
        # Bearer auth — the od_live_ key data access requires (RESEARCH_v2 §II.3).
        self._client.headers.update({"Authorization": f"Bearer {api_key}"})

    # ------------------------------------------------------------------ I/O
    def _request(self, sql: str) -> dict[str, Any]:
        """One budgeted ``/v1/query`` call — the guard (INV-8) trips on the (cap+1)th.

        The budget is checked BEFORE the call, so an exhausted budget never reaches
        the network (fail closed), mirroring HubSpot's / Stripe's ``_request``. A
        non-2xx response raises via ``raise_for_status``.
        """
        if self._queries_made >= self._cap:
            raise OpenDataBudgetExceededError(
                f"Open Data per-run query budget exhausted ({self._cap}); degrade to "
                f"seeded (INV-8) rather than overspend the metered tryopendata.ai API."
            )
        self._queries_made += 1
        response = self._client.post(_QUERY_PATH, json={"query": sql})
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    # --------------------------------------------------------------- interface
    def district_enrichment(self, district_id: str) -> DistrictEnrichment:
        """Query tryopendata.ai for ``district_id`` and map the ``tea/*`` row (E1).

        Issues a SQL ``POST /v1/query`` against the ``tea/*`` datasets (the A–F
        accountability rating, STAAR proficiency, PEIMS per-pupil ``amount``, and
        enrollment) and maps the returned aggregate columns onto a typed
        :class:`DistrictEnrichment`. District-level only (INV-1/INV-6).
        """
        sql = (
            "SELECT rating, staar_proficiency, amount, enrollment "
            f'FROM "{self._datasets.accountability_ratings}" '
            f'JOIN "{self._datasets.staar}" USING (district_id) '
            f'JOIN "{self._datasets.peims_finance}" USING (district_id) '
            f"WHERE district_id = '{district_id}'"
        )
        body = self._request(sql)
        rows = body.get("rows") or body.get("data") or []
        if not rows:
            raise RuntimeError(
                f"tryopendata.ai returned no tea/* row for district {district_id!r}; "
                "cannot map a DistrictEnrichment (fail loud, never fabricate)."
            )
        row: dict[str, Any] = rows[0]
        # Map the aggregate tea/* columns onto the typed model. PEIMS per-pupil is
        # carried as the `amount` column (RESEARCH_v2 §II.3).
        return DistrictEnrichment(
            district_id=str(row.get("district_id", district_id)),
            d_rating=str(row["rating"]),
            staar_proficiency=float(row["staar_proficiency"]),
            per_pupil_spend=float(row["amount"]),
            enrollment=int(row["enrollment"]),
        )

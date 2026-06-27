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

# --- TEA dataset encoding (the platform's own schema/coding — the A-39/A-41
# third-party-surface carve-out to INV-11; these describe TEA's data shape, not a
# GT tunable, and are all verified live against api.tryopendata.ai). One home. ---
# Modern A–F accountability grades (pre-2018 years store legacy "Met Standard"
# strings; we skip those and take the latest real A–F grade).
_AF_GRADES = ("A", "B", "C", "D", "F")
# PEIMS General Fund (Maintenance & Operations) — the standard per-pupil basis.
_GENERAL_FUND = 199
# PEIMS expenditure object codes 6100–6499 (payroll / contracted services /
# supplies / other operating). Excludes 5xxx REVENUE and 65xx debt service, so the
# total is operating EXPENDITURE — a naive SUM(amount) double-counts revenue+spend.
_EXP_OBJECT_LO, _EXP_OBJECT_HI = 6100, 6499
# STAAR proficiency basis. TEA TAPR encodes performance in `metric_code`; for the
# All-Students series the `…A001{level}{YY}{D|N|R}` codes carry TEA's own precomputed
# RATE (suffix R, a percent). Level digit 2 = "Approaches Grade Level or Above" —
# TEA's headline, most-inclusive standard. `DD000A0012__R` selects that rate for any
# 2-digit year; we average it across the STAAR subject groups + latest year, /100.
_STAAR_APPROACHES_RATE_LIKE = "DD000A0012__R"


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
        # The tryopendata.ai /v1/query contract: POST {"sql": "<SQL>"} → a result
        # object with `columns` (names) + `rows` (arrays of values) — NOT keyed dicts.
        response = self._client.post(_QUERY_PATH, json={"sql": sql})
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def _scalar(self, body: dict[str, Any], *, what: str, district_id: str) -> Any:
        """The single (row 0, col 0) value of a one-column result; fail loud if absent.

        The /v1/query response is ``{"columns": [...], "rows": [[v, ...], ...]}``. A
        ``SUM``/``AVG`` over no matching rows still returns one row whose value is
        ``None`` — that (or no row at all) is missing data: raise rather than
        fabricate a zero (the demo must degrade to seeded, never invent a statistic).
        """
        rows = body.get("rows") or []
        value = rows[0][0] if rows and rows[0] else None
        if value is None:
            raise RuntimeError(
                f"tryopendata.ai returned no {what} for district {district_id!r}; "
                "cannot map a DistrictEnrichment (fail loud, never fabricate)."
            )
        return value

    # --------------------------------------------------------------- interface
    def district_enrichment(self, district_id: str) -> DistrictEnrichment:
        """Query tryopendata.ai for ``district_id`` and map the live TEA data (E1).

        Four district-level ``POST /v1/query`` reads against the real ``tea/*``
        datasets (INV-1/INV-6 — no child-keyed row ever crosses this boundary), each
        taking the LATEST available year:

        * **A–F rating** — ``accountability-ratings.d_rating`` (latest A–F year).
        * **enrollment** — ``SUM(student-enrollment.enrollment)`` over ethnicities.
        * **per-pupil spend** — ``SUM`` of PEIMS General-Fund operating expenditures
          (fund 199, object 6100–6499, actuals) ÷ enrollment.
        * **STAAR proficiency** — TEA's precomputed "Approaches Grade Level or Above"
          All-Students rate, averaged across subjects, as a fraction.

        The TEA district key has two forms: the accountability/STAAR datasets use the
        zero-padded 6-char string (``"031903"``); enrollment/finance use the integer
        (``31903``). Both are derived from ``district_id`` here.
        """
        padded = district_id.strip().zfill(6)
        try:
            numeric = int(district_id)
        except ValueError as exc:
            raise RuntimeError(
                f"district_id {district_id!r} is not a TEA numeric id — cannot query."
            ) from exc

        ratings = self._datasets.accountability_ratings
        enroll = self._datasets.student_enrollment
        finance = self._datasets.peims_finance
        staar = self._datasets.staar
        grades = ", ".join(f"'{g}'" for g in _AF_GRADES)

        # 1) Latest A–F accountability grade.
        rating = self._scalar(
            self._request(
                f'SELECT d_rating FROM "{ratings}" '
                f"WHERE district = '{padded}' AND d_rating IN ({grades}) "
                "ORDER BY school_year DESC LIMIT 1"
            ),
            what="A–F rating",
            district_id=district_id,
        )

        # 2) Total enrollment (sum over the per-ethnicity rows) for the latest year.
        enrollment = int(
            self._scalar(
                self._request(
                    f'SELECT SUM(CAST(enrollment AS BIGINT)) FROM "{enroll}" '
                    f"WHERE district = {numeric} AND year = "
                    f'(SELECT MAX(year) FROM "{enroll}" WHERE district = {numeric})'
                ),
                what="enrollment",
                district_id=district_id,
            )
        )

        # 3) General-Fund operating expenditure (actuals, latest fundyear).
        total_expenditure = float(
            self._scalar(
                self._request(
                    f'SELECT SUM(amount) FROM "{finance}" '
                    f"WHERE district = {numeric} AND report_type = 'actual' "
                    f"AND fund = {_GENERAL_FUND} "
                    f"AND object_code BETWEEN {_EXP_OBJECT_LO} AND {_EXP_OBJECT_HI} "
                    f'AND fundyear = (SELECT MAX(fundyear) FROM "{finance}" '
                    f"WHERE district = {numeric} AND report_type = 'actual' "
                    f"AND fund = {_GENERAL_FUND})"
                ),
                what="PEIMS finance",
                district_id=district_id,
            )
        )

        # 4) STAAR "Approaches Grade Level or Above" rate (percent → fraction).
        staar_pct = float(
            self._scalar(
                self._request(
                    f'SELECT AVG(CAST(value AS DOUBLE)) FROM "{staar}" '
                    f"WHERE district = '{padded}' "
                    f"AND metric_code LIKE '{_STAAR_APPROACHES_RATE_LIKE}' "
                    "AND CAST(value AS DOUBLE) BETWEEN 0 AND 100 "
                    f'AND school_year = (SELECT MAX(school_year) FROM "{staar}" '
                    f"WHERE district = '{padded}' "
                    f"AND metric_code LIKE '{_STAAR_APPROACHES_RATE_LIKE}' "
                    "AND CAST(value AS DOUBLE) >= 0)"
                ),
                what="STAAR proficiency",
                district_id=district_id,
            )
        )

        per_pupil = total_expenditure / enrollment if enrollment else 0.0
        return DistrictEnrichment(
            district_id=district_id,
            d_rating=str(rating),
            staar_proficiency=round(staar_pct / 100.0, 4),
            per_pupil_spend=round(per_pupil, 2),
            enrollment=enrollment,
        )

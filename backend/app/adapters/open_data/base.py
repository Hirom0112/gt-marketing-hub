"""The Open Data enrichment boundary — interface + typed model (E1; INV-1/9).

The abstract half of the ``OpenDataAdapter`` seam for tryopendata.ai's
Texas-education data (RESEARCH_v2 §II.3). Two impls —
:class:`~app.adapters.open_data.seeded.SeededOpenDataAdapter` (v1 default; pure,
offline, INV-9) and :class:`~app.adapters.open_data.live.LiveOpenDataAdapter`
(production REST over ``httpx`` against ``POST /v1/query`` with a ``Bearer
od_live_`` key, behind the INV-8 per-run query cap) — are selected at startup by
config in :mod:`app.adapters.registry`. The later decision-change core depends
only on this interface and the :class:`DistrictEnrichment` model.

INV-1/INV-6: the enrichment is **aggregate, district-level** only — an A–F
accountability rating, a STAAR proficiency fraction, PEIMS per-pupil spend, and an
enrollment count. There is no child-keyed field by construction (no minor
targeting, no student-level row ever crosses this boundary).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class OpenDataBudgetExceededError(RuntimeError):
    """Guard (INV-8): the per-run Open Data query budget was exhausted.

    Mirrors :class:`app.adapters.hubspot.live_adapter.HubSpotBudgetExceededError`
    and :class:`app.adapters.payments.base.PaymentsBudgetExceededError`: the
    metered tryopendata.ai ``/v1/query`` endpoint has a hard per-run ceiling, so
    the (cap+1)th query fails closed here rather than silently overspending. The
    registry's kill switch / degrade-to-seeded is the coarser sibling (INV-8).
    """


class DistrictEnrichment(BaseModel):
    """An aggregate, district-level enrichment row (E1; INV-1/INV-6).

    The typed shape the decision-change core reads. Frozen: a read of an
    enrichment is immutable. Every field is aggregate/district-level — NO
    child-keyed data ever crosses this boundary (INV-1/INV-6).

    Attributes:
        district_id: The TEA district identifier this row describes (a district,
            never a student).
        d_rating: The A–F accountability rating (e.g. ``"A"`` / ``"D"`` / ``"F"``).
        staar_proficiency: The aggregate STAAR proficiency, a fraction in [0, 1].
        per_pupil_spend: The PEIMS per-pupil spend (whole USD) for the district.
        enrollment: The aggregate district enrollment count.
    """

    model_config = ConfigDict(frozen=True)

    district_id: str
    d_rating: str
    staar_proficiency: float
    per_pupil_spend: float
    enrollment: int


class OpenDataAdapter(ABC):
    """The Open Data enrichment external boundary (E1; RESEARCH_v2 §II.3).

    Two impls — Seeded (v1 default, INV-9) and Live (go-live) — selected by config
    in :mod:`app.adapters.registry`. The decision-change core depends only on this
    interface and never knows whether a real tryopendata.ai query or the seeded
    fixture is behind it.
    """

    @abstractmethod
    def district_enrichment(self, district_id: str) -> DistrictEnrichment:
        """Return the aggregate, district-level enrichment for ``district_id`` (INV-1/6)."""

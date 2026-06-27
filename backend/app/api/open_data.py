"""Open Data enrichment endpoint — the query that CHANGES a decision (E1; INV-2/8/11).

The brief's headline composed end-to-end: ``POST /open-data/enrich`` runs a
Texas-district Open Data query through the ``OpenDataAdapter`` seam (seeded v1 /
live go-live, selected by the §7 registry), applies the PURE ``enrich_decision``
core, and — when the recommendation actually MOVES — feeds the changed rec into the
B2 Decision Queue (via the shared ``flag_decision`` feeder) as a card carrying full
PROVENANCE + the data SOURCE (live OpenData vs the seeded fallback).

This module is a thin composition root: it owns no rule numbers (every threshold
lives in ``params.open_data.decision_change``, INV-11) and makes no state write of
its own beyond enqueuing one human-review card (INV-2). It composes already-built
pieces and modifies none of them:

* :func:`app.adapters.registry.effective_open_data_mode` — the canonical seam-state
  source for the SOURCE badge (``"simulate"`` ⇒ ``"seeded"`` / ``"live"`` ⇒ ``"live"``).
* :meth:`app.adapters.open_data.base.OpenDataAdapter.district_enrichment` — the query.
* :func:`app.core.decision_change.enrich_decision` — the deterministic rule.
* :func:`app.api.decisions.flag_decision` — the B2 Decision-Queue feeder.

Honest by construction: a rec that does NOT change enqueues NOTHING — only a real
change lands a card. The response always carries the change + the source so the UI
can render both even when no card was enqueued.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.adapters.open_data.base import DistrictEnrichment, OpenDataAdapter
from app.adapters.registry import effective_open_data_mode
from app.api.decisions import flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_decisions_store,
    get_open_data_adapter_dep,
    get_params,
    get_principal,
    get_settings_dep,
)
from app.core.decision_change import DecisionRec, enrich_decision
from app.core.params import Params
from app.core.program import Program
from app.core.settings import Settings
from app.data.decisions_store import DecisionsStore

router = APIRouter(tags=["open_data"])

# The B2 feeder source tag for an enrichment-driven card — a stable token so the
# Decision Queue can group/route these (named, not an inline literal).
ENRICHMENT_SOURCE = "open_data_enrichment"

# The default base priority for a freshly-queried district when the caller supplies
# none — a documented, neutral floor (the rec starts un-prioritized; the boost is
# the whole signal). Named, not a magic literal in the handler (INV-11 spirit).
_DEFAULT_BASE_PRIORITY = 0

# Map the effective seam mode onto the leader-facing SOURCE badge: the v1 default
# (and any kill-switched live) reads as the seeded fallback; a true live query reads
# as live OpenData. This is what makes the demo honest — the leader sees WHETHER the
# rec moved on live data or the seeded source.
_SOURCE_BADGE: dict[str, str] = {"simulate": "seeded", "live": "live"}

# Dependency aliases (Annotated keeps the call in the type — ruff B008).
AdapterDep = Annotated[OpenDataAdapter, Depends(get_open_data_adapter_dep)]
StoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class EnrichRequest(BaseModel):
    """Body for ``POST /open-data/enrich`` — the district to query + an optional base."""

    district_id: str = Field(min_length=1)
    base_priority: int | None = None


class RecommendationChange(BaseModel):
    """The before/after of the rec's priority (so the UI can render the move)."""

    base_priority: int
    new_priority: int
    delta: int


class ProvenanceResponse(BaseModel):
    """The auditable WHY of the change — the reason token + the tripped signals."""

    reason: str
    signals: list[str]


class EnrichResponse(BaseModel):
    """The enrichment result — the change + the source, queued-or-not."""

    district_id: str
    enrichment: DistrictEnrichment
    recommendation_changed: bool
    new_priority: int
    provenance: ProvenanceResponse
    data_source: str


@router.post("/open-data/enrich", response_model=EnrichResponse)
def enrich(
    body: EnrichRequest,
    adapter: AdapterDep,
    store: StoreDep,
    program: ProgramDep,
    params: ParamsDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> EnrichResponse:
    """Query a district, apply the rule, and feed a CHANGED rec into the Decision Queue.

    Open to any authenticated principal (``Depends(get_principal)``). Steps:

    1. Query the district's aggregate enrichment through the ``OpenDataAdapter`` seam.
    2. Build the current rec at ``base_priority`` (the documented default when absent).
    3. Apply the pure :func:`enrich_decision` (every threshold from params; INV-11).
    4. Resolve the SOURCE badge from the effective seam mode (seeded vs live).
    5. If the recommendation CHANGED, enqueue EXACTLY ONE open Decision-Queue card via
       the B2 feeder, carrying the typed enrichment, the before/after recommendation,
       the provenance, and the data source — so the leader sees WHY + WHENCE. If it did
       NOT change, enqueue nothing (honest: only a real change feeds the queue).
    6. Return the change + the source either way (the UI renders both regardless).
    """
    enrichment = adapter.district_enrichment(body.district_id)

    base_priority = body.base_priority if body.base_priority is not None else _DEFAULT_BASE_PRIORITY
    rec = DecisionRec(priority=base_priority, payload={"district_id": body.district_id})
    result = enrich_decision(rec, enrichment, params=params)

    data_source = _SOURCE_BADGE[effective_open_data_mode(settings)]
    provenance = ProvenanceResponse(
        reason=result.provenance.reason,
        signals=list(result.provenance.signals),
    )
    recommendation = RecommendationChange(
        base_priority=base_priority,
        new_priority=result.priority,
        delta=result.provenance.delta,
    )

    if result.provenance.changed:
        # The rec MOVED ⇒ feed it into the B2 Decision Queue with full provenance +
        # the source, so a leader can act on the change knowing WHY and WHENCE.
        card_payload: dict[str, Any] = {
            "district_id": body.district_id,
            "enrichment": enrichment.model_dump(),
            "recommendation": recommendation.model_dump(),
            "provenance": provenance.model_dump(),
            "data_source": data_source,
        }
        flag_decision(store, program, source=ENRICHMENT_SOURCE, payload=card_payload)

    return EnrichResponse(
        district_id=body.district_id,
        enrichment=enrichment,
        recommendation_changed=result.provenance.changed,
        new_priority=result.priority,
        provenance=provenance,
        data_source=data_source,
    )

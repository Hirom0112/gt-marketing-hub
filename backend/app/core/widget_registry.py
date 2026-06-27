"""The canonical Home widget catalog — the single id home (TODO_v2 §B3; INV-11).

The composable Home (B3) lets each operator arrange dashboard widgets on a
react-grid-layout. Every widget the UI can render has exactly ONE canonical id,
and this module is that id's home: a pure, stdlib-only vocabulary (the same kind
of module as :mod:`app.core.program`) listing every valid widget, the functional
group it belongs to, and whether it is part of the default *starter pack* a new
(or stripped-down) user receives.

The backend feeds these id sets into the pure
:func:`app.core.dashboard_layout.merge_starter_pack` reconcile (it drops saved
placements whose id is no longer in :data:`REGISTRY_IDS` and re-hydrates any
missing :data:`STARTER_IDS`). The frontend (U4) maps each id → a React component;
that map and this catalog must agree, so this is the ONE place the id vocabulary
is defined — neither side invents ids of its own (INV-11).

Each :data:`WIDGET_REGISTRY` id is drawn from an EXISTING cockpit surface (an api
router / a workspace metric), so a Home widget always has a real backend to read
from. Purity: no ``app.ai`` / ``app.adapters`` import (the core-purity test guards
this) — a plain enumerated vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WidgetSpec:
    """One catalog entry: a widget's canonical id, its group, and starter membership.

    ``id`` is the canonical react-grid-layout placement key (the ``"i"`` the
    frontend maps to a component). ``group`` is the functional surface it belongs
    to (used by the UI to section the "add widget" palette). ``in_starter_pack``
    marks the default new-user set re-hydrated by ``merge_starter_pack``.
    """

    id: str
    group: str
    in_starter_pack: bool = False


# The full ≥30-widget catalog. Each id maps to an EXISTING cockpit surface (the
# router / workspace it reads from is noted), so a placed widget always has a real
# backend. The ~8 starter-pack widgets span Enrollment / Leadership / CRM / Security
# so a brand-new operator lands on a legible, cross-surface default Home.
WIDGET_REGISTRY: tuple[WidgetSpec, ...] = (
    # ----------------------------------------------------------- Enrollment
    WidgetSpec("kpi_strip", "Enrollment", in_starter_pack=True),  # /pipeline rollup
    WidgetSpec("work_queue", "Enrollment", in_starter_pack=True),  # /work-queue
    WidgetSpec("pipeline_board", "Enrollment", in_starter_pack=True),  # /pipeline
    WidgetSpec("deal_view", "Enrollment"),  # /families/{id}
    WidgetSpec("funding_tracker", "Enrollment"),  # /families/{id}/funding
    WidgetSpec("seam_reconcile", "Enrollment"),  # /seam
    WidgetSpec("assignment_board", "Enrollment"),  # /assign
    WidgetSpec("sla_sweep", "Enrollment"),  # SLA sweep
    WidgetSpec("contact_outcomes", "Enrollment"),  # /families/{id}/contact-outcome
    WidgetSpec("merge_queue", "Enrollment"),  # /merge-queue
    WidgetSpec("notes_timeline", "Enrollment"),  # /families/{id}/notes
    # ------------------------------------------------------------- Marketing
    WidgetSpec("content_library", "Marketing"),  # /content/library
    WidgetSpec("geo_module", "Marketing"),  # /geo
    WidgetSpec("brand_memory", "Marketing"),  # brand-memory store
    WidgetSpec("scheduler", "Marketing"),  # /content/schedule
    WidgetSpec("sentiment", "Marketing"),  # /sentiment
    WidgetSpec("creator_intel", "Marketing"),  # /creators
    WidgetSpec("recipes", "Marketing"),  # /recipes
    WidgetSpec("publish_monitor", "Marketing"),  # /publish/monitor
    WidgetSpec("campaign_analytics", "Marketing"),  # HubSpot campaign rollup
    # ------------------------------------------------------------ Leadership
    WidgetSpec("scoreboard", "Leadership", in_starter_pack=True),  # /scoreboard
    WidgetSpec("decision_queue", "Leadership", in_starter_pack=True),  # /decisions
    WidgetSpec("budget_tracker", "Leadership"),  # cost-cap rollup
    WidgetSpec("kpi_scorecard", "Leadership"),  # /kpi
    WidgetSpec("eval_scoreboard", "Leadership"),  # /evals
    WidgetSpec("agent_rollup", "Leadership"),  # /agents rollup
    # ------------------------------------------------------------------- CRM
    WidgetSpec("crm_status", "CRM", in_starter_pack=True),  # /crm/status
    WidgetSpec("data_confidence", "CRM", in_starter_pack=True),  # data-confidence rollup
    WidgetSpec("sync_parity", "CRM"),  # /crm/sync/status
    WidgetSpec("crm_poll_status", "CRM"),  # /crm/sync poll state
    # -------------------------------------------------------------- Security
    WidgetSpec("rls_posture", "Security", in_starter_pack=True),  # /security/posture
    WidgetSpec("security_feed", "Security"),  # /security/events
    # --------------------------------------------------------------- Funding
    WidgetSpec("tefa_tracker", "Funding"),  # TEFA award math
    WidgetSpec("payments_ledger", "Funding"),  # /payments ledger
    # --------------------------------------------------------- Observability
    WidgetSpec("audit_timeline", "Observability"),  # NFR-6 audit spine
    WidgetSpec("proposal_log", "Observability"),  # /proposals
)


# Derived id sets fed into ``merge_starter_pack`` (the pure reconcile). Frozen so a
# caller cannot mutate the catalog's vocabulary at runtime.
REGISTRY_IDS: frozenset[str] = frozenset(spec.id for spec in WIDGET_REGISTRY)
STARTER_IDS: frozenset[str] = frozenset(spec.id for spec in WIDGET_REGISTRY if spec.in_starter_pack)

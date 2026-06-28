"""Per-KPI provenance descriptors — where every scorecard number comes from (B6).

The weekly scorecard (``GET /scorecard/weekly``) renders the nine business KPIs the
product spec lists. A number with no source is a number no one can trust, so every
KPI carries a :class:`MetricProvenance` descriptor: which system it comes from, the
exact ``table.column`` / function that produced it, whether it is a LIVE read, a read
of OUR db, a labeled STOOD-IN, a DERIVED rollup, or genuinely UNINSTRUMENTED, and a
one-line human formula. The UI shows this verbatim so a leader can see, per row,
"this is real / this is a placeholder / this isn't wired yet" — the brief's honesty
posture (surface what's broken rather than faking green).

This module is the ONE canonical home (INV-11 spirit) mapping a KPI key →
its provenance: source strings live here, never scattered across the API layer.

Purity (CLAUDE.md §3, INV-2): a frozen descriptor + a static registry — stdlib only,
no repository / adapter / ``httpx`` import and no clock. The :data:`PROVENANCE` map is
a pure constant; the API layer attaches it to each computed metric row.
"""

from __future__ import annotations

from dataclasses import dataclass

# The provenance KIND vocabulary — what TRUST class a number is (named constants, not
# bare strings scattered at the call sites; INV-11 spirit). The UI keys its badge off
# these tokens:
#   * live          — a fresh read of a live external system.
#   * our_db        — a read of our own persisted ledger/table (populated upstream).
#   * derived       — a deterministic rollup the core computes from our_db facts.
#   * stood_in      — a LABELED placeholder: the real source isn't wired, the number
#                     is an honest proxy (INV-9 simulated-but-labeled posture).
#   * uninstrumented— no source exists at all; the value is a gap, not a measurement.
KIND_LIVE = "live"
KIND_OUR_DB = "our_db"
KIND_DERIVED = "derived"
KIND_STOOD_IN = "stood_in"
KIND_UNINSTRUMENTED = "uninstrumented"


@dataclass(frozen=True, slots=True)
class MetricProvenance:
    """Where one KPI's number comes from (a frozen, UI-facing descriptor).

    Attributes:
        system: The owning system label (e.g. ``"Supabase"``, ``"HubSpot"``,
            ``"Stripe"``, ``"Grassroots"``, ``"Derived"``, or ``"—"`` for none).
        locator: The exact ``table.column`` or function that produced the number
            (e.g. ``"app_form.funnel_stage"``, ``"core.lead_routing.is_sla_breached"``).
        kind: One of the ``KIND_*`` tokens above — the number's trust class.
        compute: A one-line human formula (e.g. ``"enrolled / total for the top
            attribution_source"``).
        last_sync: ISO timestamp of the source's last sync, or ``None`` when not yet
            wired to a watermark (null for every row in v1 — a later phase wires it).
    """

    system: str
    locator: str
    kind: str
    compute: str
    last_sync: str | None = None


# The nine business KPIs' provenance — the SINGLE canonical key → source map (INV-11).
# The API layer computes each value from the named source and attaches the matching
# descriptor here; the keys/order mirror the API-layer KPI registry exactly (a test
# pins that the two key sets agree, so neither can drift).
PROVENANCE: dict[str, MetricProvenance] = {
    # 1. Applicants (total) — a real count of our application spine.
    "applicants": MetricProvenance(
        system="Supabase",
        locator="family_record.current_stage (app_form funnel)",
        kind=KIND_OUR_DB,
        compute="sum of families across all funnel stages (pipeline_counts)",
    ),
    # 2. Deposits vs Fall goal — Stripe webhooks land in our payment ledger.
    "deposits": MetricProvenance(
        system="Stripe",
        locator="payment (Stripe webhook → ledger)",
        kind=KIND_OUR_DB,
        compute="count of payment-ledger rows (deposits) for the active program",
    ),
    # 3. Conversion · top channel — derived from our attribution column.
    "conversion_top_channel": MetricProvenance(
        system="Supabase",
        locator="family_record.attribution_source",
        kind=KIND_DERIVED,
        compute="enrolled / total for the top attribution_source",
    ),
    # 4. Engagement-tier mix (clicked) — HubSpot click tier is not wired; honest proxy.
    "engagement_clicked": MetricProvenance(
        system="HubSpot",
        locator="community_profile.engagement_signals",
        kind=KIND_STOOD_IN,
        compute="share of families with any email engagement (HubSpot click tier stood-in)",
    ),
    # 5. 24-hr follow-up SLA — derived from the assignment + contact-log spine.
    "followup_sla": MetricProvenance(
        system="HubSpot",
        locator="core.lead_routing.is_sla_breached + core.contact_log.last_contact_at",
        kind=KIND_DERIVED,
        compute="share of assigned leads worked within the SLA window (not breached / assigned)",
    ),
    # 6. Objections logged — no source yet; honest stood-in.
    "objections": MetricProvenance(
        system="HubSpot",
        locator="—",
        kind=KIND_STOOD_IN,
        compute="objections recorded in HubSpot conversations (not yet wired)",
    ),
    # 7. Ambassador-influenced enrollments — enrollment attribution untracked; roster proxy.
    "ambassador_enrollments": MetricProvenance(
        system="Grassroots",
        locator="core.ambassador_reconcile.reconcile_ambassadors",
        kind=KIND_STOOD_IN,
        compute="reconciled ambassador roster size (enrollment attribution not yet tracked)",
    ),
    # 8. Marketing → onboarding handoffs — derived from the funnel stage boundary.
    "handoffs": MetricProvenance(
        system="HubSpot",
        locator="family_record.current_stage (enroll/tuition boundary)",
        kind=KIND_DERIVED,
        compute="families that reached the enroll/onboarding stage (pipeline_counts boundary)",
    ),
    # 9. Event-to-consult conversion — explicitly not instrumented.
    "event_to_consult": MetricProvenance(
        system="—",
        locator="—",
        kind=KIND_UNINSTRUMENTED,
        compute="no event or consult source instrumented yet",
    ),
}

"""GET /crm/ops — the CRM/Marketing-Operations data-quality view (TODO_v2 §C1).

The read-only window onto the cockpit's CRM-Ops data quality: A4 sync-parity, the
auto data-quality queue, per-entity UTM-health, and the honest field-reliability
flags — with the cross-module data-confidence banner when parity drops below the
floor. This module is a COMPOSITION ROOT: it COMPOSES the committed C1 cores and
NEVER re-implements them (and does NOT fork A4's parity).

  ``GET /crm/ops``
    Gated only by ``Depends(get_principal)`` (any authenticated seat — the
    identical view for everyone, exactly like ``GET /crm/status``; no role gate).
    Over the active-program cohort (the SAME ``(record, mirror)`` pairing the §4.7
    seam endpoints use — ``repository.list_families`` + the seam CRM adapter's
    ``read_mirror``) it returns:

    * ``parity_overall`` / ``parity_by_field`` — A4's
      :func:`app.core.parity.compute_parity` over the cohort (REUSED, not forked);
    * ``data_confidence_banner`` — raised when ``parity_overall`` drops below
      ``params.crm_ops.parity_floor`` (INV-11 — the single threshold home);
    * ``dq_queue`` — :func:`app.core.data_quality.build_dq_queue` over one
      :class:`app.core.data_quality.DqRow` per family, severity-ordered
      (``conflict`` first);
    * ``utm_health`` — an ok/broken aggregate of
      :func:`app.core.utm_health.check_utm` over each family's UTM, with the broken
      entities' offending keys + reasons;
    * ``field_flags`` — :func:`app.core.field_reliability.field_flag` over
      ``params.crm_ops.unreliable_fields`` (the honest low-trust field list).

UTM sourcing (honesty mandate). The per-family UTM is sourced from the genuinely
present ``FamilyRecord.attribution_utm`` (FR-1.4 — the lead's raw utm/click-id
blob: ``utm_source``/``utm_medium``/``utm_campaign``), coerced to the str-keyed
mapping ``check_utm`` reads; non-str values are dropped, an empty/absent blob is
``None``. No UTM is fabricated.

``present_fields`` (honesty note). The data-quality queue's ``unreliable_field``
issue keys off each row's ``present_fields``. The synthetic ``FamilyRecord`` does
NOT carry the configured low-trust fields as stored per-row columns:
``household_income`` is deliberately EXCLUDED (INV-1 — only the ``income_tier``
BUCKET exists, never the raw figure), ``tefa_amount`` is params-derived per tier
(never a per-family column), and the lead's source channel lives under
``attribution_source`` (not ``lead_source``). Rather than fabricate a populated
low-trust value the model doesn't carry, ``present_fields`` is empty and the
low-trust field list is surfaced honestly via ``field_flags`` instead. (The
``unreliable_field`` issue kind itself is exercised by the data_quality core's own
unit tests.)

Read-only by design (INV-2/INV-9): no state write, no live call — the seam CRM
adapter's mirror is the seeded simulated one in v1, the live portal mirror under
``CRM_MODE=live``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.decisions import DecisionResponse, _actor_token, flag_decision
from app.api.deps import (
    Principal,
    get_active_program,
    get_crm_adapter_dep,
    get_crm_ops_store,
    get_decisions_store,
    get_params,
    get_principal,
    get_repository,
    get_seam_crm_adapter_dep,
    require_role,
)
from app.core.data_quality import DqKind, DqRow, build_dq_queue
from app.core.field_reliability import field_flag
from app.core.params import Params
from app.core.parity import compute_parity
from app.core.program import Program
from app.core.utm_health import check_utm
from app.data.crm_ops_store import (
    CATEGORIES,
    SEVERITIES,
    CrmFixLogEntry,
    CrmOpsIssue,
    CrmOpsStore,
)
from app.data.decisions_store import PRIORITIES, PRIORITY_NORMAL, DecisionsStore
from app.data.models import FamilyRecord
from app.data.repository import FamilyRepository

router = APIRouter(tags=["crm"])

# ---------------------------------------------------------------------------
# Module-7 constants (named wire tokens, INV-11 carve-out like other modules).
# ---------------------------------------------------------------------------
# The workstream the CRM-Ops owner must own to FILE a manual data-quality issue; also the
# Decision-Queue lane a scoring-model change lands on (one of decisions_store.WORKSTREAMS).
CRM_OPS_OWNER_WORKSTREAM = "crm"
# The Decision-Queue source tag a scoring-model-change approval carries.
SCORING_CHANGE_SOURCE = "scoring_model_change"

# Honest per-response source labels (so the UI never implies a read it didn't make).
SOURCE_LEAD_SCORE = "crm_aggregate"  # LIVE HubSpot aggregate gt_lead_score COUNTs (INV-6).
SOURCE_PARITY = "supabase⇄hubspot"  # A4 parity over the (record, mirror) cohort.
SOURCE_CORRELATION = "derived_synthetic"  # the score→conversion table is DERIVED, not live.
SOURCE_UTM = "supabase_attribution_utm"  # per-param resolution from the stored UTM blob.
SOURCE_SYNTHETIC = "synthetic"  # synthesized connector last-sync timestamps.

# The always-on rule-of-truth string the 5d sync-parity view surfaces.
RULE_OF_TRUTH = "Supabase app_form is the source of truth for funnel/TEFA/income"

# The connectors the overview reports a last-sync for (synthesized timestamps; INV-11
# carve-out — fixed wire labels). The real watermark lives behind GET /crm/sync/status.
_CONNECTORS: tuple[str, ...] = ("hubspot_contacts", "hubspot_deals", "supabase_app_form")

# DqKind → persisted issue category (one of CATEGORIES). The single mapping home.
_KIND_CATEGORY: dict[DqKind, str] = {
    "conflict": "sync",
    "utm_broken": "utm",
    "unreliable_field": "other",
    "mojibake": "tracking",
    "missing_field": "tracking",
}
# DqKind → persisted severity label (one of SEVERITIES).
_KIND_SEVERITY: dict[DqKind, str] = {
    "conflict": "high",
    "utm_broken": "high",
    "unreliable_field": "low",
    "mojibake": "medium",
    "missing_field": "medium",
}

# The lead-scoring model description the 5c view renders (with the params threshold).
_SCORING_MODEL_DESCRIPTION = (
    "Lead score is HubSpot's gt_lead_score (0–100), read aggregate-only and DISPLAY-only. "
    "A lead qualifies at/above the configured threshold; the cockpit never edits the score."
)

# The leader/admin gate (leadership input) for the queue triage + scoring-change paths.
_LEADER_OR_ADMIN = require_role("leader", "admin")
LeaderAdminDep = Annotated[Principal, Depends(_LEADER_OR_ADMIN)]

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/crm_status.py + app/api/scorecard.py).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_seam_crm_adapter_dep)]
# The LIVE aggregate adapter (live HubSpot under CRM_MODE=live; simulated offline) — the
# lead-score read seam, distinct from the seeded seam mirror parity/scan read from.
LiveCRMAdapterDep = Annotated[CRMAdapter, Depends(get_crm_adapter_dep)]
ParamsDep = Annotated[Params, Depends(get_params)]
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]
ProgramDep = Annotated[Program, Depends(get_active_program)]
CrmOpsStoreDep = Annotated[CrmOpsStore, Depends(get_crm_ops_store)]
DecisionsStoreDep = Annotated[DecisionsStore, Depends(get_decisions_store)]


class DqIssueOut(BaseModel):
    """One serialized data-quality issue (the frozen :class:`DqIssue` as JSON)."""

    entity_id: str
    kind: str
    severity: int
    detail: str


class UtmEntityOut(BaseModel):
    """One broken-UTM entity's verdict (the offending keys + human reasons)."""

    entity_id: str
    offending_keys: list[str]
    reasons: list[str]


class UtmHealthOut(BaseModel):
    """The cohort UTM-health aggregate — ok/broken counts + the broken entities."""

    ok: int
    broken: int
    broken_entities: list[UtmEntityOut]


class FieldFlagOut(BaseModel):
    """One serialized field-reliability flag (the frozen :class:`FieldReliability`)."""

    field: str
    status: str
    reason: str | None


class CrmOpsView(BaseModel):
    """The CRM-Ops data-quality view (C1) — the composed cores, serialized."""

    parity_overall: float
    parity_by_field: dict[str, float]
    data_confidence_banner: bool
    dq_queue: list[DqIssueOut]
    utm_health: UtmHealthOut
    field_flags: list[FieldFlagOut]


def _record_utm(record: FamilyRecord) -> dict[str, str] | None:
    """The family's UTM as the str-keyed mapping ``check_utm`` reads, or ``None``.

    Sourced honestly from the genuinely-present ``attribution_utm`` (FR-1.4). The
    stored blob is ``dict[str, object]`` (it also carries an opaque ``click_id``),
    so non-str values are dropped; an empty/absent blob is ``None`` (⇒ every
    required key missing, the documented ``check_utm`` contract). Nothing is
    fabricated.
    """
    raw: Mapping[str, object] = record.attribution_utm
    if not raw:
        return None
    utm = {key: value for key, value in raw.items() if isinstance(value, str)}
    return utm or None


@router.get("/crm/ops", response_model=CrmOpsView)
def get_crm_ops(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    params: ParamsDep,
    program: ProgramDep,
) -> CrmOpsView:
    """Surface the CRM-Ops data-quality view over the active-program cohort (C1).

    COMPOSES the committed C1 cores (no re-implementation, no parity fork): A4
    sync-parity over the SAME ``(record, mirror)`` pairing the §4.7 seam endpoints
    use, the auto data-quality queue, the per-entity UTM-health aggregate, and the
    honest field-reliability flags. The data-confidence banner is raised when
    overall parity drops below ``params.crm_ops.parity_floor`` (INV-11).

    Read-only (INV-2): no state write, no live call (INV-9 — the seam CRM adapter's
    mirror is the seeded simulated one in v1). ``principal``/``program`` are resolved
    for the authenticated-seat gate + program scoping (the cohort is already
    program-scoped at the repo layer, A1); they are otherwise unused here.
    """
    families = list(repository.list_families())
    # The A4 pairing (REUSED): each program-scoped family paired with its CRM mirror.
    pairs = [(record, crm_adapter.read_mirror(record.family_id)) for record in families]
    parity = compute_parity(pairs)
    banner = parity.overall < params.crm_ops.parity_floor

    # The auto data-quality queue: one DqRow per family (conflict + UTM dimensions
    # are genuine; present_fields is empty per the module honesty note).
    rows = [
        DqRow(
            entity_id=str(record.family_id),
            record=record,
            mirror=mirror,
            utm=_record_utm(record),
            present_fields=(),
        )
        for record, mirror in pairs
    ]
    issues = build_dq_queue(rows, params=params)

    # Per-entity UTM-health, surfaced as an ok/broken aggregate (REUSED check_utm).
    ok = 0
    broken_entities: list[UtmEntityOut] = []
    for record in families:
        health = check_utm(_record_utm(record), params=params)
        if health.status == "ok":
            ok += 1
        else:
            broken_entities.append(
                UtmEntityOut(
                    entity_id=str(record.family_id),
                    offending_keys=list(health.offending_keys),
                    reasons=list(health.reasons),
                )
            )

    # The honest low-trust field list (REUSED field_flag).
    flags = [field_flag(name, params=params) for name in params.crm_ops.unreliable_fields]

    return CrmOpsView(
        parity_overall=parity.overall,
        parity_by_field=parity.by_field,
        data_confidence_banner=banner,
        dq_queue=[
            DqIssueOut(
                entity_id=issue.entity_id,
                kind=issue.kind,
                severity=issue.severity,
                detail=issue.detail,
            )
            for issue in issues
        ],
        utm_health=UtmHealthOut(
            ok=ok, broken=len(broken_entities), broken_entities=broken_entities
        ),
        field_flags=[
            FieldFlagOut(field=flag.field, status=flag.status, reason=flag.reason) for flag in flags
        ],
    )


# ===========================================================================
# Module-7 helpers (pure-ish composition; no live call in the simulated path).
# ===========================================================================
def _cohort_pairs(
    repository: FamilyRepository, crm_adapter: CRMAdapter
) -> list[tuple[FamilyRecord, object]]:
    """The A4 (record, mirror) pairing over the active-program cohort (REUSED)."""
    return [(r, crm_adapter.read_mirror(r.family_id)) for r in repository.list_families()]


def _owner_gate_crm_ops(principal: Principal) -> None:
    """OWNER gate for a manual data-quality FILE — 403 on a deny (mirrors field_events).

    A LEADER/ADMIN may file any. An OPERATOR may file ONLY when they own the ``crm``
    workstream; the demo operator owns a different workstream, so a foreign operator is
    403. The verified ROLE decides — never a client claim (the IDOR/spoof posture).
    """
    if principal.role in ("admin", "leader"):
        return
    raise HTTPException(
        status_code=403,
        detail=f"operator does not own the {CRM_OPS_OWNER_WORKSTREAM!r} workstream",
    )


def _lead_distribution(
    repository: FamilyRepository, crm_adapter: CRMAdapter, params: Params
) -> LeadScoreDistributionOut:
    """The LIVE aggregate lead-score histogram + tier breakdown (INV-6; DISPLAY-only)."""
    family_ids = [r.family_id for r in repository.list_families()]
    cfg = params.crm_ops.lead_score
    dist = crm_adapter.read_lead_score_distribution(family_ids, band_edges=cfg.bands)
    cold = warm = hot = 0
    for band in dist.bands:
        if band.low < cfg.tiers.warm_min:
            cold += band.count
        elif band.low < cfg.tiers.hot_min:
            warm += band.count
        else:
            hot += band.count
    return LeadScoreDistributionOut(
        bands=[
            LeadScoreBandOut(label=b.label, low=b.low, high=b.high, count=b.count)
            for b in dist.bands
        ],
        total=dist.total,
        mean=round(dist.mean, 1),
        threshold=cfg.threshold,
        tiers=LeadScoreTierBreakdown(cold=cold, warm=warm, hot=hot),
        source=SOURCE_LEAD_SCORE,
    )


def _broken_utm_entities(repository: FamilyRepository, params: Params) -> list[UtmEntityOut]:
    """The cohort's broken-UTM entities (REUSED check_utm) — the drill-in list."""
    broken: list[UtmEntityOut] = []
    for record in repository.list_families():
        health = check_utm(_record_utm(record), params=params)
        if health.status != "ok":
            broken.append(
                UtmEntityOut(
                    entity_id=str(record.family_id),
                    offending_keys=list(health.offending_keys),
                    reasons=list(health.reasons),
                )
            )
    return broken


def _scan_and_upsert(
    repository: FamilyRepository,
    crm_adapter: CRMAdapter,
    store: CrmOpsStore,
    params: Params,
    program: Program,
) -> ScanResult:
    """Auto-detect over the live cohort → UPSERT one issue per detection (idempotent).

    Sweeps :func:`build_dq_queue` over the SAME (record, mirror)+UTM cohort the §C1 view
    derives, maps each :class:`DqIssue` to a category/severity, computes a deterministic
    ``signature`` (``entity_ref:kind``), and UPSERTS it — so a rescan dedups (never dups)
    and existing acknowledged/resolved rows keep their status. "Auto-detect creates queue
    items automatically".
    """
    families = list(repository.list_families())
    rows = [
        DqRow(
            entity_id=str(r.family_id),
            record=r,
            mirror=crm_adapter.read_mirror(r.family_id),
            utm=_record_utm(r),
            present_fields=(),
        )
        for r in families
    ]
    issues = build_dq_queue(rows, params=params)
    for issue in issues:
        store.upsert_issue(
            program,
            signature=f"{issue.entity_id}:{issue.kind}",
            category=_KIND_CATEGORY[issue.kind],
            kind=issue.kind,
            severity=_KIND_SEVERITY[issue.kind],
            description=issue.detail,
            entity_ref=issue.entity_id,
            source="auto",
        )
    return ScanResult(
        scanned=len(families),
        detected=len(issues),
        open_dq_count=len(store.list_issues(program, status="open")),
    )


# ===========================================================================
# Wire models (5a–5e + the write bodies/results).
# ===========================================================================
class LeadScoreBandOut(BaseModel):
    """One lead-score histogram band (aggregate count only — INV-6)."""

    label: str
    low: int
    high: int
    count: int


class LeadScoreTierBreakdown(BaseModel):
    """The cold/warm/hot tier counts derived from the histogram (Module 7)."""

    cold: int
    warm: int
    hot: int


class LeadScoreDistributionOut(BaseModel):
    """The LIVE aggregate lead-score histogram + tier breakdown (INV-6; DISPLAY-only)."""

    bands: list[LeadScoreBandOut]
    total: int
    mean: float
    threshold: int
    tiers: LeadScoreTierBreakdown
    source: str


class ConnectorSync(BaseModel):
    """One connector's last-sync timestamp (synthesized; the real one is /crm/sync/status)."""

    connector: str
    last_sync: str
    source: str


class OverviewResponse(BaseModel):
    """The 5a CRM-Ops overview rollup."""

    parity_overall: float
    data_confidence_banner: bool
    utm_ok: int
    utm_broken: int
    lead_score_distribution: LeadScoreDistributionOut
    open_dq_count: int
    last_sync: list[ConnectorSync]
    field_flags: list[FieldFlagOut]


class UtmParamResolution(BaseModel):
    """One UTM param's resolved share across the cohort (the 5b per-param table)."""

    param: str
    resolved: int
    total: int
    resolved_pct: float


class AttributionChainStep(BaseModel):
    """One attribution-chain step (form → Supabase → HubSpot) + its per-step status."""

    step: int
    label: str
    status: str


class FixLogOut(BaseModel):
    """One applied-fix log entry (the 5b UTM fix log / 5c scoring change log)."""

    fix_id: UUID
    kind: str
    summary: str
    actor: str
    applied_at: datetime


class SourceTrackingResponse(BaseModel):
    """The 5b source-tracking view — per-param resolution + chain + broken drill-in."""

    params: list[UtmParamResolution]
    broken_utm: list[UtmEntityOut]
    attribution_chain: list[AttributionChainStep]
    fix_log: list[FixLogOut]
    source: str


class CorrelationRow(BaseModel):
    """One score-band → conversion-rate row (DERIVED, not a live join — labeled honestly)."""

    band: str
    conversion_pct: float


class LeadScoringResponse(BaseModel):
    """The 5c lead-scoring view — LIVE histogram + tiers + a DERIVED correlation table."""

    distribution: LeadScoreDistributionOut
    correlation: list[CorrelationRow]
    correlation_source: str
    model_description: str
    threshold: int
    change_log: list[FixLogOut]


class DriftAlert(BaseModel):
    """One field-level sync-parity drift alert (parity below the drift floor)."""

    field: str
    parity: float
    floor: float


class SyncParityResponse(BaseModel):
    """The 5d sync-parity view — overall + field-level parity, flags, drift, rule-of-truth."""

    parity_overall: float
    parity_by_field: dict[str, float]
    field_flags: list[FieldFlagOut]
    drift_alerts: list[DriftAlert]
    rule_of_truth: str
    source: str


class IssueOut(BaseModel):
    """One persisted data-quality issue over the wire (the 5e queue/resolution row)."""

    issue_id: UUID
    signature: str
    category: str
    kind: str
    severity: str
    description: str
    owner: str
    status: str
    source: str
    entity_ref: str
    priority: str
    created_at: datetime
    resolved_at: datetime | None
    resolution: str
    resolved_by: str


class DataQualityResponse(BaseModel):
    """The 5e data-quality view — open issues + the resolution log (resolved issues)."""

    open_issues: list[IssueOut]
    resolution_log: list[IssueOut]


class ScanResult(BaseModel):
    """The POST /crm/ops/scan outcome — what the auto-detect sweep found + upserted."""

    scanned: int
    detected: int
    open_dq_count: int


class FileIssueRequest(BaseModel):
    """Body for POST /crm/ops/data-quality — file a MANUAL data-quality issue.

    There is DELIBERATELY no ``owner`` field: the row is stamped with the CRM-Ops owner
    token; writes are owner-gated by the verified principal (INV-1).
    """

    category: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    severity: str = "medium"
    description: str = ""
    entity_ref: str = ""
    priority: str = PRIORITY_NORMAL


class UpdateIssueRequest(BaseModel):
    """Body for PATCH /crm/ops/data-quality/{id} — acknowledge / prioritize / resolve."""

    status: str | None = None
    priority: str | None = None
    resolution: str | None = None


class ScoringChangeRequest(BaseModel):
    """Body for POST /crm/ops/scoring-change — approve a scoring-model change."""

    summary: str = Field(min_length=1)
    recommendation: str = ""
    priority: str = PRIORITY_NORMAL


class ScoringChangeResponse(BaseModel):
    """The scoring-change outcome — the queued leadership decision + the logged fix."""

    decision: DecisionResponse
    fix: FixLogOut


def _issue_out(i: CrmOpsIssue) -> IssueOut:
    """Project a store :class:`CrmOpsIssue` onto the wire :class:`IssueOut`."""
    return IssueOut(
        issue_id=i.issue_id,
        signature=i.signature,
        category=i.category,
        kind=i.kind,
        severity=i.severity,
        description=i.description,
        owner=i.owner,
        status=i.status,
        source=i.source,
        entity_ref=i.entity_ref,
        priority=i.priority,
        created_at=i.created_at,
        resolved_at=i.resolved_at,
        resolution=i.resolution,
        resolved_by=i.resolved_by,
    )


def _fix_out(f: CrmFixLogEntry) -> FixLogOut:
    """Project a store :class:`CrmFixLogEntry` onto the wire :class:`FixLogOut`."""
    return FixLogOut(
        fix_id=f.fix_id, kind=f.kind, summary=f.summary, actor=f.actor, applied_at=f.applied_at
    )


def _synthetic_last_sync() -> list[ConnectorSync]:
    """A synthesized per-connector last-sync (labeled honestly; real one = /crm/sync/status)."""
    now = datetime.now(UTC)
    out: list[ConnectorSync] = []
    for i, connector in enumerate(_CONNECTORS):
        out.append(
            ConnectorSync(
                connector=connector,
                last_sync=(now - timedelta(minutes=(i + 1) * 5)).isoformat(),
                source=SOURCE_SYNTHETIC,
            )
        )
    return out


# ===========================================================================
# READ endpoints — the 5 tab views (any authenticated seat).
# ===========================================================================
@router.get("/crm/ops/overview", response_model=OverviewResponse)
def get_overview(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    live_adapter: LiveCRMAdapterDep,
    store: CrmOpsStoreDep,
    params: ParamsDep,
    program: ProgramDep,
) -> OverviewResponse:
    """5a — parity, UTM health, LIVE lead-score distribution, open DQ count, last-sync, flags."""
    pairs = _cohort_pairs(repository, crm_adapter)
    parity = compute_parity(pairs)  # type: ignore[arg-type]
    broken = _broken_utm_entities(repository, params)
    flags = [field_flag(name, params=params) for name in params.crm_ops.unreliable_fields]
    return OverviewResponse(
        parity_overall=parity.overall,
        data_confidence_banner=parity.overall < params.crm_ops.parity_floor,
        utm_ok=len(list(repository.list_families())) - len(broken),
        utm_broken=len(broken),
        lead_score_distribution=_lead_distribution(repository, live_adapter, params),
        open_dq_count=len(store.list_issues(program, status="open")),
        last_sync=_synthetic_last_sync(),
        field_flags=[FieldFlagOut(field=f.field, status=f.status, reason=f.reason) for f in flags],
    )


@router.get("/crm/ops/source-tracking", response_model=SourceTrackingResponse)
def get_source_tracking(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    store: CrmOpsStoreDep,
    params: ParamsDep,
    program: ProgramDep,
) -> SourceTrackingResponse:
    """5b — per-UTM-param resolution, broken-UTM drill-in, attribution chain, UTM fix log."""
    families = list(repository.list_families())
    total = len(families)
    utms = [_record_utm(r) for r in families]
    # source/medium/campaign (the required keys) + content (an extra, often unresolved).
    tracked: Sequence[str] = [*params.crm_ops.utm.required_keys, "utm_content"]
    rows: list[UtmParamResolution] = []
    for key in tracked:
        resolved = sum(1 for u in utms if u is not None and (u.get(key) or "").strip())
        rows.append(
            UtmParamResolution(
                param=key,
                resolved=resolved,
                total=total,
                resolved_pct=round(100.0 * resolved / total, 1) if total else 0.0,
            )
        )
    chain = [
        AttributionChainStep(step=i + 1, label=label, status="ok")
        for i, label in enumerate(params.crm_ops.attribution_chain_steps)
    ]
    return SourceTrackingResponse(
        params=rows,
        broken_utm=_broken_utm_entities(repository, params),
        attribution_chain=chain,
        fix_log=[_fix_out(f) for f in store.list_fix_log(program, kind="utm_fix")],
        source=SOURCE_UTM,
    )


@router.get("/crm/ops/lead-scoring", response_model=LeadScoringResponse)
def get_lead_scoring(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    live_adapter: LiveCRMAdapterDep,
    store: CrmOpsStoreDep,
    params: ParamsDep,
    program: ProgramDep,
) -> LeadScoringResponse:
    """5c — LIVE histogram + tiers + a DERIVED score→conversion table + model + change log.

    HONESTY: the score→conversion correlation is DERIVED deterministically from the band
    edges (a true per-contact→deal-stage join is not an aggregate read; INV-6) and is
    labeled ``derived_synthetic`` — never claimed live. The histogram itself IS live.
    """
    dist = _lead_distribution(repository, live_adapter, params)
    top_edge = dist.bands[-1].high if dist.bands else 1
    correlation = [
        CorrelationRow(
            band=b.label,
            # Higher score band ⇒ higher conversion (normalized by the top edge; no magic
            # constant — purely the band edge over the histogram's top edge).
            conversion_pct=round(100.0 * b.low / top_edge, 1) if top_edge else 0.0,
        )
        for b in dist.bands
    ]
    return LeadScoringResponse(
        distribution=dist,
        correlation=correlation,
        correlation_source=SOURCE_CORRELATION,
        model_description=_SCORING_MODEL_DESCRIPTION,
        threshold=params.crm_ops.lead_score.threshold,
        change_log=[_fix_out(f) for f in store.list_fix_log(program, kind="scoring_change")],
    )


@router.get("/crm/ops/sync-parity", response_model=SyncParityResponse)
def get_sync_parity(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    params: ParamsDep,
    program: ProgramDep,
) -> SyncParityResponse:
    """5d — overall + field-level parity, unreliable-field flags, drift alerts, rule-of-truth."""
    pairs = _cohort_pairs(repository, crm_adapter)
    parity = compute_parity(pairs)  # type: ignore[arg-type]
    floor = params.crm_ops.drift_alert_floor
    flags = [field_flag(name, params=params) for name in params.crm_ops.unreliable_fields]
    drift = [
        DriftAlert(field=field_name, parity=value, floor=floor)
        for field_name, value in parity.by_field.items()
        if value < floor
    ]
    return SyncParityResponse(
        parity_overall=parity.overall,
        parity_by_field=parity.by_field,
        field_flags=[FieldFlagOut(field=f.field, status=f.status, reason=f.reason) for f in flags],
        drift_alerts=drift,
        rule_of_truth=RULE_OF_TRUTH,
        source=SOURCE_PARITY,
    )


@router.get("/crm/ops/data-quality", response_model=DataQualityResponse)
def get_data_quality(
    principal: AnyPrincipalDep,
    store: CrmOpsStoreDep,
    program: ProgramDep,
) -> DataQualityResponse:
    """5e — open issues (severity/owner/category/created) + the resolution log (resolved)."""
    return DataQualityResponse(
        open_issues=[_issue_out(i) for i in store.list_issues(program, status="open")],
        resolution_log=[_issue_out(i) for i in store.list_issues(program, status="resolved")],
    )


# ===========================================================================
# WRITE endpoints.
# ===========================================================================
@router.post("/crm/ops/scan", response_model=ScanResult)
def scan_data_quality(
    principal: AnyPrincipalDep,
    repository: RepositoryDep,
    crm_adapter: CRMAdapterDep,
    store: CrmOpsStoreDep,
    params: ParamsDep,
    program: ProgramDep,
) -> ScanResult:
    """Auto-detect over the live cohort → UPSERT queue items (any authenticated seat).

    Only refreshes derived state (it dedups on a deterministic signature, never dups), so
    it needs no role gate — any authenticated seat may trigger a rescan.
    """
    return _scan_and_upsert(repository, crm_adapter, store, params, program)


@router.post("/crm/ops/data-quality", response_model=IssueOut)
def file_issue(
    body: FileIssueRequest,
    principal: AnyPrincipalDep,
    store: CrmOpsStoreDep,
    program: ProgramDep,
) -> IssueOut:
    """File a MANUAL data-quality issue — owner-gated (CRM-Ops owns the ``crm`` workstream).

    ``category`` must be one of :data:`CATEGORIES` and ``severity`` one of
    :data:`SEVERITIES` / ``priority`` one of :data:`PRIORITIES` — an unknown value is a
    clean 422 (fail-closed, INV-2). ``owner`` is stamped server-side (never the body).
    """
    _owner_gate_crm_ops(principal)
    if body.category not in CATEGORIES:
        raise HTTPException(
            status_code=422, detail=f"category must be one of {CATEGORIES}, got {body.category!r}"
        )
    if body.severity not in SEVERITIES:
        raise HTTPException(
            status_code=422, detail=f"severity must be one of {SEVERITIES}, got {body.severity!r}"
        )
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422, detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}"
        )
    issue = store.file_issue(
        program,
        category=body.category,
        kind=body.kind,
        severity=body.severity,
        description=body.description,
        owner=CRM_OPS_OWNER_WORKSTREAM,
        entity_ref=body.entity_ref,
        priority=body.priority,
    )
    return _issue_out(issue)


@router.patch("/crm/ops/data-quality/{issue_id}", response_model=IssueOut)
def update_issue(
    issue_id: UUID,
    body: UpdateIssueRequest,
    store: CrmOpsStoreDep,
    program: ProgramDep,
    principal: LeaderAdminDep,
) -> IssueOut:
    """Acknowledge / prioritize / resolve an issue — LEADER or admin (leadership input).

    A provided ``status`` must be open/acknowledged/resolved and ``priority`` one of
    :data:`PRIORITIES` (clean 422 otherwise; INV-2). Resolving stamps ``resolved_by`` from
    the VERIFIED principal (never the body) + the resolution instant. 404 on an unknown id.
    """
    if body.status is not None and body.status not in ("open", "acknowledged", "resolved"):
        raise HTTPException(status_code=422, detail=f"unknown status {body.status!r}")
    if body.priority is not None and body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422, detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}"
        )
    changes: dict[str, object] = {}
    if body.status is not None:
        changes["status"] = body.status
    if body.priority is not None:
        changes["priority"] = body.priority
    if body.resolution is not None:
        changes["resolution"] = body.resolution
    if body.status == "resolved":
        changes["resolved_by"] = _actor_token(principal)
    if not changes:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        updated = store.update_issue(program, issue_id, **changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data-quality issue not found") from exc
    return _issue_out(updated)


@router.post("/crm/ops/scoring-change", response_model=ScoringChangeResponse)
def approve_scoring_change(
    body: ScoringChangeRequest,
    decisions_store: DecisionsStoreDep,
    store: CrmOpsStoreDep,
    program: ProgramDep,
    principal: LeaderAdminDep,
) -> ScoringChangeResponse:
    """Approve a scoring-model change — LEADER or admin (leadership input).

    Lands an OPEN leadership decision on the ``crm`` workstream (the B2 feeder) AND appends
    a ``scoring_change`` entry to the CRM fix log (the 5c change log). ``raised_by`` /
    ``actor`` are STAMPED from the verified principal — never the body (INV-1). ``priority``
    must be one of :data:`PRIORITIES` (clean 422; INV-2).
    """
    if body.priority not in PRIORITIES:
        raise HTTPException(
            status_code=422, detail=f"priority must be one of {PRIORITIES}, got {body.priority!r}"
        )
    actor = _actor_token(principal)
    decision = flag_decision(
        decisions_store,
        program,
        source=SCORING_CHANGE_SOURCE,
        payload={"summary": body.summary},
        question=f"Approve scoring-model change: {body.summary}",
        raised_by=actor,
        workstream=CRM_OPS_OWNER_WORKSTREAM,
        recommendation=body.recommendation,
        priority=body.priority,
    )
    fix = store.append_fix_log(program, kind="scoring_change", summary=body.summary, actor=actor)
    return ScoringChangeResponse(decision=DecisionResponse.of(decision), fix=_fix_out(fix))

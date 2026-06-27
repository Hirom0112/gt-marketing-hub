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

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.api.deps import (
    Principal,
    get_active_program,
    get_params,
    get_principal,
    get_repository,
    get_seam_crm_adapter_dep,
)
from app.core.data_quality import DqRow, build_dq_queue
from app.core.field_reliability import field_flag
from app.core.params import Params
from app.core.parity import compute_parity
from app.core.program import Program
from app.core.utm_health import check_utm
from app.data.models import FamilyRecord
from app.data.repository import FamilyRepository

router = APIRouter(tags=["crm"])

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the
# idiomatic FastAPI style matching app/api/crm_status.py + app/api/scorecard.py).
RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
CRMAdapterDep = Annotated[CRMAdapter, Depends(get_seam_crm_adapter_dep)]
ParamsDep = Annotated[Params, Depends(get_params)]
AnyPrincipalDep = Annotated[Principal, Depends(get_principal)]
ProgramDep = Annotated[Program, Depends(get_active_program)]


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

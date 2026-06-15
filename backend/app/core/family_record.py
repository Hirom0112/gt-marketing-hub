"""Deal-view projection — the FR-2.2 operator view over a Family Record (§6).

`GET /families/{family_id}` returns the *deal view*: a flat, operator-facing
projection that pulls the fields an enrollment operator needs onto one shape —
who the family is, why they're stalled, how they're funded, where they came
from, their academic signals, and whether the CRM seam reflects local state.

This is a **pure projection** (CLAUDE.md §3, INV-2): a deterministic function of
a :class:`JoinedFamily` (the spine joined to its four source rows) plus an
optional simulated mirror. It does **no** data access of its own — the
repository (the store seam) already performed the join — and it imports nothing
from `app.ai` / `app.adapters` (INV-2 read-only; the core-purity test guards it).

`crm_seam_status` is **derived** here via the §4.7 seam deriver rather than read
off the seeded spine column, so the deal view and the seam logic never drift:
the deriver is the single source of truth for the seam's three states.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.seam import MirrorState, derive_seam_status
from app.data.models import (
    FundingType,
    SeamStatus,
    StallReason,
)
from app.data.repository import JoinedFamily


class DealView(BaseModel):
    """The FR-2.2 deal-view projection — one flat shape per family (§6).

    Every field is sourced from the joined rows: identity + contact from the
    spine, the deterministic ``stall_reason`` / ``funding_type`` columns, the
    FR-1.4 attribution pair, the §4.3 academic signals from ``app_form``, and the
    §4.7-derived ``crm_seam_status``. Academic fields are nullable because an
    ``interest``-stage family has no ``app_form`` yet.
    """

    model_config = ConfigDict(frozen=True)

    # Profile (spine).
    family_id: UUID
    display_name: str
    primary_contact_synthetic_email: str

    # Deterministic spine columns (§4.1, §4.8).
    stall_reason: StallReason | None
    funding_type: FundingType | None

    # Attribution pair (FR-1.4).
    attribution_source: str
    attribution_utm: dict[str, object]

    # Academic signals (§4.3 app_form) — null until an application exists.
    map_score: float | None
    academic_signals: dict[str, object]

    # CRM seam, DERIVED via the §4.7 deriver (not the seeded column).
    crm_seam_status: SeamStatus


def _default_mirror(joined: JoinedFamily) -> MirrorState:
    """A mirror that agrees on the tracked field, so the seam reduces to timestamps.

    The simulated HubSpot mirror is not fetched in v1 (§7.1, OUT-3); when no
    mirror is supplied the deal view assumes no tracked-field divergence — the
    seam status then follows purely from ``crm_synced_at`` vs ``updated_at``.
    """
    return MirrorState(
        stage=joined.family.current_stage,
        mirror_updated_at=joined.family.updated_at,
    )


def assemble_deal_view(
    joined: JoinedFamily,
    *,
    mirror: MirrorState | None = None,
) -> DealView:
    """Project a :class:`JoinedFamily` into the FR-2.2 :class:`DealView`.

    Pure: a deterministic function of ``joined`` (and the optional ``mirror``)
    alone. Performs no data access — the join is already done — and derives
    ``crm_seam_status`` through the §4.7 seam deriver so the deal view and the
    seam logic share one source of truth.

    Args:
        joined: The spine row joined to its four source rows.
        mirror: The simulated HubSpot mirror's view of this family's tracked
            fields. Defaults to a mirror that agrees on stage (no divergence),
            so the seam status follows the timestamp rule.

    Returns:
        The assembled :class:`DealView`.
    """
    family = joined.family
    app_form = joined.app_form

    effective_mirror = mirror if mirror is not None else _default_mirror(joined)

    return DealView(
        family_id=family.family_id,
        display_name=family.display_name,
        primary_contact_synthetic_email=family.primary_contact_synthetic_email,
        stall_reason=family.stall_reason,
        funding_type=family.funding_type,
        attribution_source=family.attribution_source,
        attribution_utm=family.attribution_utm,
        map_score=app_form.map_score if app_form is not None else None,
        academic_signals=app_form.academic_signals if app_form is not None else {},
        crm_seam_status=derive_seam_status(family, effective_mirror),
    )

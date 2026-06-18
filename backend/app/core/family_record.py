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

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.contact_status import ContactStatus
from app.core.recovery_state import RecoveryState
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

    # Household contacts (A-36; redesign panel §1–2) — BOTH parents on the ONE
    # household. The PRIMARY parent's name + phone come off the lead row (the
    # top-of-funnel synthetic contact), falling back to the household display name
    # when there is no lead. The SECONDARY guardian (name/email/phone/relationship)
    # and BOTH guardian relationships come off the family spine. All synthetic
    # (INV-1); each is None when not on file. Household-grained — never a child key.
    primary_contact_name: str | None = None
    primary_contact_synthetic_phone: str | None = None
    guardian_1_relationship: str | None = None
    secondary_contact_name: str | None = None
    secondary_contact_synthetic_email: str | None = None
    secondary_contact_synthetic_phone: str | None = None
    guardian_2_relationship: str | None = None

    # Location (redesign panel §3) — AGGREGATE labels only, never precise geo of a
    # minor (INV-6): the lead's neighborhood/region area labels + the household's
    # coarse US state code. None when not on file.
    neighborhood: str | None = None
    region: str | None = None
    state: str | None = None

    # Deterministic spine columns (§4.1, §4.8).
    stall_reason: StallReason | None
    funding_type: FundingType | None

    # Attribution pair (FR-1.4).
    attribution_source: str
    attribution_utm: dict[str, object]

    # Academic signals (§4.3 app_form) — null until an application exists.
    # ``map_score`` is RETAINED on the projection (DH-2 / the AI draft pack still
    # read it) but the DEAL VIEW now presents conversion likelihood instead (DH-1).
    map_score: float | None
    academic_signals: dict[str, object]

    # Conversion-likelihood signal (DH-1) — the deal view's "who is most likely to
    # enroll + the top contributing factor", REPLACING the meaningless MAP signal.
    # Composed in the API layer (NOT here): the ``depth`` dimension reuses the
    # ``recoverability`` term, whose ``now`` the pure core never touches. So
    # ``assemble_deal_view`` leaves these None; ``api/families.py`` fills them via
    # ``model_copy`` from the family's signals + params (mirrors contact_status).
    conversion_score: float | None = None
    conversion_band: str | None = None
    conversion_top_factor: str | None = None
    conversion_top_factor_label: str | None = None

    # Drop-off signal (S9 W2; FR-2.2) — PURE, projected from the source rows:
    # how far the application got (``completion_pct``), the six-form gauntlet
    # progress (``forms_signed`` / ``forms_total``), and the first unsigned form
    # (``next_unsigned_form`` — the "stuck on <name>" signal, None when all are
    # signed or there are no forms). ``apply_date`` is the application instant
    # (``app_form.submitted_at``), falling back to the spine ``created_at``.
    completion_pct: float | None = None
    forms_signed: int | None = None
    forms_total: int | None = None
    next_unsigned_form: str | None = None
    apply_date: datetime | None = None

    # Contact-recency (S9 W2; A-14) — composed in the API layer, NOT here: the
    # deriver needs ``now`` + the audit log, which the pure core never touches.
    # ``assemble_deal_view`` leaves these None; ``api/families.py`` fills them via
    # ``model_copy`` so the projection stays a pure function of its rows (INV-2).
    contact_status: ContactStatus | None = None
    last_contact_at: datetime | None = None

    # Recovery state (S12 W1; A-19) — also composed in the API layer, NOT here:
    # the deriver needs ``now`` + the audit log (the dismiss + last-contact facts),
    # which the pure core never touches. ``assemble_deal_view`` leaves this None;
    # ``api/families.py`` fills it via ``model_copy``. {stalled,working,recovered,
    # dismissed}.
    recovery_state: RecoveryState | None = None

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


def _next_unsigned_form(forms_status: list[dict[str, object]]) -> str | None:
    """First form whose ``signed_at`` is None — the "stuck on <name>" signal (FR-2.2).

    Pure scan over the per-form ``{name, signed_at|null}`` rows in document order
    (§4.4). Returns the first unsigned form's ``name``, or None when every form is
    signed (or there are no forms). A missing ``name`` falls back to None so the
    projection never raises on a malformed row.
    """
    for form in forms_status:
        if form.get("signed_at") is None:
            name = form.get("name")
            return name if isinstance(name, str) else None
    return None


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
    lead = joined.lead
    app_form = joined.app_form
    enrollment_forms = joined.enrollment_forms

    effective_mirror = mirror if mirror is not None else _default_mirror(joined)

    # §1–2 primary parent: name + phone off the lead, falling back to the household
    # display name when no lead row exists (an interest-stage / marketing row).
    primary_contact_name = (
        f"{lead.synthetic_first_name} {lead.synthetic_last_name}"
        if lead is not None
        else family.display_name
    )

    # Drop-off projection (S9 W2; FR-2.2) — pure, straight off the source rows.
    # apply_date prefers the application instant, else the spine created_at.
    apply_date = app_form.submitted_at if app_form is not None else None
    if apply_date is None:
        apply_date = family.created_at

    return DealView(
        family_id=family.family_id,
        display_name=family.display_name,
        primary_contact_synthetic_email=family.primary_contact_synthetic_email,
        # Household contacts (§1–2) — primary off the lead, secondary off the spine.
        primary_contact_name=primary_contact_name,
        primary_contact_synthetic_phone=lead.synthetic_phone if lead is not None else None,
        guardian_1_relationship=family.guardian_1_relationship,
        secondary_contact_name=family.secondary_contact_name,
        secondary_contact_synthetic_email=family.secondary_contact_synthetic_email,
        secondary_contact_synthetic_phone=family.secondary_contact_synthetic_phone,
        guardian_2_relationship=family.guardian_2_relationship,
        # Location (§3) — aggregate labels only (INV-6).
        neighborhood=lead.neighborhood if lead is not None else None,
        region=lead.region if lead is not None else None,
        state=family.state,
        stall_reason=family.stall_reason,
        funding_type=family.funding_type,
        attribution_source=family.attribution_source,
        attribution_utm=family.attribution_utm,
        map_score=app_form.map_score if app_form is not None else None,
        academic_signals=app_form.academic_signals if app_form is not None else {},
        completion_pct=app_form.completion_pct if app_form is not None else None,
        forms_signed=enrollment_forms.forms_signed if enrollment_forms is not None else None,
        forms_total=enrollment_forms.forms_total if enrollment_forms is not None else None,
        next_unsigned_form=(
            _next_unsigned_form(enrollment_forms.forms_status)
            if enrollment_forms is not None
            else None
        ),
        apply_date=apply_date,
        # contact_status / last_contact_at stay None here — composed in api/ (A-14).
        crm_seam_status=derive_seam_status(family, effective_mirror),
    )

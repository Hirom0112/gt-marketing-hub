"""Ambassador dual-source reconciler (Grassroots; INV-2 / INV-6 / INV-11).

The Grassroots ambassador roster is **two sources, one truth**: the HubSpot
ambassador-tracking property and the ``community.gt.school`` community export.
Neither is authoritative on its own — an ambassador can be onboarded in the
community portal before the rep tags them in HubSpot, or tracked in HubSpot
while absent from the community roll. This module is the **deterministic core**
that resolves the two into

1. the reconciled **union** with a per-row provenance (``both`` /
   ``hubspot-only`` / ``community-only``), and
2. a list of **conflicts** — rows present in both sources whose tracked
   attributes (e.g. ``status``) disagree — surfaced for a human, never silently
   resolved.

It follows the shape of the other reconcilers in this package
(:mod:`app.core.identity`, :mod:`app.core.sis_reconcile`,
:mod:`app.core.seam`): a PURE function of (source_a, source_b) → result, with
no I/O, no LLM, no adapter imports, and **no threshold** — matching is a
deterministic exact-match on a normalized identity key (a normalized email, or
``name + segment`` when an email is absent), so there is no magic number to
externalize (INV-11). Like ``identity.propose_merge`` it is fail-closed: a
divergence is *flagged*, never auto-picked (INV-2/INV-4 posture).

Aggregate-only and adult-only (INV-6): ambassadors are adults, the segment /
region labels are aggregate community categories, and no child-keyed field ever
enters here. All names/emails are synthetic (INV-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Input — the core-local, source-agnostic view of one ambassador row.
# Both the HubSpot side and the community.gt.school side are converted to this
# (the synthetic generator emits it directly), so the pure core never imports a
# source/adapter module (the sis_reconcile.SisRosterRow pattern).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AmbassadorRecord:
    """One ambassador as seen by a single source (synthetic; INV-1, INV-6).

    Attributes:
        synthetic_name: The ambassador's display name (synthetic — an adult, never
            a child; INV-1/INV-6).
        synthetic_email: The contact email (synthetic ``@example.invalid`` sink),
            the primary match key.
        segment: The aggregate community segment label (e.g. "Robotics parents"),
            part of the fallback match key — aggregate only (INV-6).
        region: The aggregate region label (e.g. "Austin metro") — aggregate only.
        status: The pipeline status the source holds (e.g. "Active"/"Champion"),
            the tracked attribute reconciliation compares for conflicts.
        intros: Warm intros the source attributes to this ambassador.
        p2p: Peer-to-peer calls the source logged for this ambassador.
        last_touch: A short human last-touch label (e.g. "2d"), display only.
    """

    synthetic_name: str
    synthetic_email: str
    segment: str
    region: str
    status: str
    intros: int = 0
    p2p: int = 0
    last_touch: str = ""


# ---------------------------------------------------------------------------
# Normalization — structural, deterministic, no tunables (INV-11). Mirrors
# identity._normalize_email: casefold + strip is structural, not a threshold.
# ---------------------------------------------------------------------------


def _norm(value: str) -> str:
    """Casefold + trim a label to a stable comparison token."""
    return value.strip().casefold()


def match_key(record: AmbassadorRecord) -> str:
    """A stable, deterministic identity key for one ambassador row.

    Exact-match rule, no threshold (INV-11): the normalized email is the primary
    key; when a source row carries no email it falls back to
    ``name + segment`` — namespaced (``email:`` / ``ns:``) so the two key spaces
    can never collide. Equal keys ⇒ the same ambassador across sources.
    """
    email = _norm(record.synthetic_email)
    if email:
        return f"email:{email}"
    return f"ns:{_norm(record.synthetic_name)}|{_norm(record.segment)}"


# The tracked attributes compared for a cross-source CONFLICT. A structural
# policy (like seam._TRACKED_FIELDS), not a numeric tunable — INV-11 governs
# numbers, not field definitions. ``status`` is the human-meaningful pipeline
# stage the two sources can legitimately disagree on; the merge/identity keys
# (email/segment) are NOT compared here (a disagreement there is a different
# ambassador, not a conflict).
_CONFLICT_FIELDS: tuple[str, ...] = ("status",)


class AmbassadorProvenance(StrEnum):
    """Where a reconciled union row came from."""

    BOTH = "both"
    HUBSPOT_ONLY = "hubspot-only"
    COMMUNITY_ONLY = "community-only"


class ReconciledAmbassador(BaseModel):
    """One row of the reconciled union (frozen — an immutable artifact).

    On a ``both`` row the displayed scalar fields take the HubSpot side by
    convention; ``has_conflict`` / ``conflicting_fields`` flag that a tracked
    attribute disagrees, but the row is NEVER silently auto-resolved — the full
    divergence is carried in :class:`AmbassadorConflict` for a human (INV-2/INV-4
    fail-closed posture).
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    synthetic_name: str
    synthetic_email: str
    segment: str
    region: str
    status: str
    intros: int
    p2p: int
    last_touch: str
    provenance: AmbassadorProvenance
    has_conflict: bool = False
    conflicting_fields: tuple[str, ...] = ()


class AmbassadorConflict(BaseModel):
    """A surfaced cross-source disagreement on a tracked attribute (frozen).

    Both sources agree this is the SAME ambassador (the match key is identical),
    but a tracked field differs. The conflict is flagged for human review, not
    resolved here (fail-closed).
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    synthetic_name: str
    synthetic_email: str
    field: str
    hubspot_value: str
    community_value: str
    summary: str


class AmbassadorReconcileResult(BaseModel):
    """The reconciled union + conflicts + counts (frozen).

    ``union`` preserves a stable, deterministic order: every HubSpot-side row in
    its source order (``both`` then ``hubspot-only`` as encountered), then the
    ``community-only`` rows in their source order. Counts are derived from the
    union so they can never disagree with it.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    union: tuple[ReconciledAmbassador, ...]
    conflicts: tuple[AmbassadorConflict, ...]
    union_count: int
    matched_count: int
    hubspot_only_count: int
    community_only_count: int
    conflict_count: int


def _conflicting_fields(a: AmbassadorRecord, b: AmbassadorRecord) -> tuple[str, ...]:
    """The tracked fields whose values differ between two matched source rows."""
    diffs: list[str] = []
    for field in _CONFLICT_FIELDS:
        if _norm(str(getattr(a, field))) != _norm(str(getattr(b, field))):
            diffs.append(field)
    return tuple(diffs)


def reconcile_ambassadors(
    hubspot: list[AmbassadorRecord],
    community: list[AmbassadorRecord],
) -> AmbassadorReconcileResult:
    """Reconcile the two ambassador sources into a deduped union + conflicts.

    Deterministic exact-match on :func:`match_key` (no threshold; INV-11):

    - a key in BOTH sources ⇒ one ``both`` union row; if a tracked attribute
      (``status``) disagrees it is flagged (``has_conflict``) and an
      :class:`AmbassadorConflict` is emitted — surfaced, never auto-resolved
      (fail-closed);
    - a key only in HubSpot ⇒ a ``hubspot-only`` row;
    - a key only in the community export ⇒ a ``community-only`` row.

    Args:
        hubspot: The HubSpot ambassador-tracking rows.
        community: The community.gt.school export rows.

    Returns:
        An :class:`AmbassadorReconcileResult` (union order is stable/deterministic).
    """
    community_by_key = {match_key(rec): rec for rec in community}
    seen_hubspot_keys: set[str] = set()

    union: list[ReconciledAmbassador] = []
    conflicts: list[AmbassadorConflict] = []
    matched = 0

    # HubSpot side first (both + hubspot-only), in source order.
    for hs in hubspot:
        key = match_key(hs)
        seen_hubspot_keys.add(key)
        community_match = community_by_key.get(key)
        if community_match is None:
            union.append(
                ReconciledAmbassador(
                    synthetic_name=hs.synthetic_name,
                    synthetic_email=hs.synthetic_email,
                    segment=hs.segment,
                    region=hs.region,
                    status=hs.status,
                    intros=hs.intros,
                    p2p=hs.p2p,
                    last_touch=hs.last_touch,
                    provenance=AmbassadorProvenance.HUBSPOT_ONLY,
                )
            )
            continue

        matched += 1
        diffs = _conflicting_fields(hs, community_match)
        for field in diffs:
            conflicts.append(
                AmbassadorConflict(
                    synthetic_name=hs.synthetic_name,
                    synthetic_email=hs.synthetic_email,
                    field=field,
                    hubspot_value=str(getattr(hs, field)),
                    community_value=str(getattr(community_match, field)),
                    summary=(
                        f"{hs.synthetic_name}: HubSpot says {field} "
                        f"'{getattr(hs, field)}', community.gt.school says "
                        f"'{getattr(community_match, field)}' — flagged for review, "
                        "not auto-resolved."
                    ),
                )
            )
        # Displayed scalars take the HubSpot side by convention; the row is
        # FLAGGED (not silently merged) when a tracked field diverges.
        union.append(
            ReconciledAmbassador(
                synthetic_name=hs.synthetic_name,
                synthetic_email=hs.synthetic_email,
                segment=hs.segment,
                region=hs.region,
                status=hs.status,
                intros=hs.intros,
                p2p=hs.p2p,
                last_touch=hs.last_touch,
                provenance=AmbassadorProvenance.BOTH,
                has_conflict=bool(diffs),
                conflicting_fields=diffs,
            )
        )

    # Community-only rows, in source order.
    community_only = 0
    for cm in community:
        key = match_key(cm)
        if key in seen_hubspot_keys:
            continue
        community_only += 1
        union.append(
            ReconciledAmbassador(
                synthetic_name=cm.synthetic_name,
                synthetic_email=cm.synthetic_email,
                segment=cm.segment,
                region=cm.region,
                status=cm.status,
                intros=cm.intros,
                p2p=cm.p2p,
                last_touch=cm.last_touch,
                provenance=AmbassadorProvenance.COMMUNITY_ONLY,
            )
        )

    hubspot_only = len(union) - matched - community_only
    return AmbassadorReconcileResult(
        union=tuple(union),
        conflicts=tuple(conflicts),
        union_count=len(union),
        matched_count=matched,
        hubspot_only_count=hubspot_only,
        community_only_count=community_only,
        conflict_count=len(conflicts),
    )

"""Decision-change core — Open Data enrichment that CHANGES a decision (E1; INV-2/INV-11).

The brief's headline: an external (Open Data, Texas-district) signal that
deterministically MOVES a Decision-Queue recommendation. :func:`enrich_decision`
takes a current rec (its priority + payload) + a district enrichment + params, and
BOOSTS the rec's priority iff the district is genuinely under-served — a low A–F
accountability rating AND STAAR proficiency below the floor AND enrollment at/above
the minimum. A healthy / A-rated district trips none of those and is left UNCHANGED.
A :class:`Provenance` record always says what (if anything) changed it, so the move
is auditable (NFR-6).

Every threshold reads from ``params.open_data.decision_change`` (INV-11) — there is
no numeric literal in the rule; a param drift fails the build's tests.

This module is part of the deterministic core and stays PURE: it imports only
typed params (core→core is fine), the stdlib, and ``typing`` — and CRUCIALLY does
NOT import ``app.adapters`` or ``app.ai``. The enrichment is consumed structurally
via the :class:`EnrichmentLike` ``Protocol``, so the adapter's
``DistrictEnrichment`` can be passed directly WITHOUT importing it (the core-purity
test guards this; INV-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.params import Params

# Stable signal tokens recorded in provenance — the three causes of a boost. Named
# constants (not literals) so the audit trail and any consumer share one vocabulary.
_SIGNAL_LOW_RATING = "low_rating"
_SIGNAL_STAAR_BELOW_FLOOR = "staar_below_floor"
_SIGNAL_ENROLLMENT_AT_MIN = "enrollment_at_min"

_REASON_UNDER_SERVED = "under_served_district"


class EnrichmentLike(Protocol):
    """The aggregate, district-level enrichment shape the core reads (E1).

    A structural ``Protocol`` so the adapter's ``app.adapters.open_data.base``
    ``DistrictEnrichment`` satisfies it WITHOUT an import — keeping the core pure
    (INV-2; the core-purity test forbids ``app.adapters`` in ``app.core``). Every
    field is aggregate/district-level only (INV-1/INV-6).
    """

    d_rating: str
    staar_proficiency: float
    enrollment: int
    per_pupil_spend: float


@dataclass(frozen=True)
class DecisionRec:
    """A minimal Decision-Queue recommendation: a ``priority`` + an opaque payload.

    Frozen — a rec is immutable; enrichment returns a NEW result rather than
    mutating it. The payload is carried through untouched (the core does not
    interpret it; downstream owns its shape).
    """

    priority: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Provenance:
    """Why (or why not) a rec's priority moved — the auditable record (NFR-6).

    Attributes:
        changed: Whether the rec was boosted.
        reason: A stable token naming the cause (empty when unchanged).
        signals: The tripped signal tokens (empty when unchanged).
        delta: The priority change applied (``0`` when unchanged).
    """

    changed: bool
    reason: str
    signals: tuple[str, ...]
    delta: int


@dataclass(frozen=True)
class EnrichedDecision:
    """The result of enriching a rec: the (possibly raised) priority + provenance."""

    priority: int
    provenance: Provenance


def enrich_decision(
    rec: DecisionRec, enrichment: EnrichmentLike, *, params: Params
) -> EnrichedDecision:
    """Boost ``rec``'s priority iff ``enrichment`` shows a genuinely under-served district.

    Deterministic and pure (no clock, no randomness). The rec is boosted iff ALL of
    the following hold, every threshold read from ``params.open_data.decision_change``
    (INV-11):

    * ``enrichment.d_rating`` is one of ``low_rating_grades``, AND
    * ``enrichment.staar_proficiency`` is strictly BELOW ``staar_proficiency_floor``,
      AND
    * ``enrichment.enrollment`` is at/above ``min_enrollment``.

    When boosted, the new priority is ``rec.priority + priority_boost`` and the
    provenance records the three signals + the delta. Otherwise the priority is
    UNCHANGED and provenance is ``changed=False`` (no signals, zero delta).

    Args:
        rec: The current Decision-Queue recommendation.
        enrichment: The aggregate, district-level Open Data enrichment (structural;
            the adapter's ``DistrictEnrichment`` satisfies it).
        params: Loaded params (§8); supplies ``open_data.decision_change`` (INV-11).

    Returns:
        An :class:`EnrichedDecision`: the (possibly raised) priority + provenance.
    """
    rules = params.open_data.decision_change

    low_rating = enrichment.d_rating in rules.low_rating_grades
    staar_below_floor = enrichment.staar_proficiency < rules.staar_proficiency_floor
    enrollment_at_min = enrichment.enrollment >= rules.min_enrollment

    boosted = low_rating and staar_below_floor and enrollment_at_min
    if boosted:
        new_priority = rec.priority + rules.priority_boost
        provenance = Provenance(
            changed=True,
            reason=_REASON_UNDER_SERVED,
            signals=(
                _SIGNAL_LOW_RATING,
                _SIGNAL_STAAR_BELOW_FLOOR,
                _SIGNAL_ENROLLMENT_AT_MIN,
            ),
            delta=new_priority - rec.priority,
        )
    else:
        # Not under-served: the rec is left exactly as it came in.
        new_priority = rec.priority
        provenance = Provenance(
            changed=False, reason="", signals=(), delta=new_priority - rec.priority
        )

    return EnrichedDecision(priority=new_priority, provenance=provenance)

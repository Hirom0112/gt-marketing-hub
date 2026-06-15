"""Creator fit/authenticity scorer tests — FR-3.8 (LOCKED formula, INV-6/INV-11).

The scorer turns raw creator SIGNALS into the `fit_score` / `authenticity_score`
that a `CreatorRecord` carries. Two pinned properties (CLAUDE.md §4.1):

* **Params-derived (INV-11).** Every expected value is computed by reading the
  sub-weights from `load_params()` — never a baked-in literal — so the test
  FAILS if a weight in `params.yaml` drifts.
* **Aggregate-only / adults-only (INV-6).** `surface` must never emit a minor or
  a `live_scrape` record. The schema already forbids both at parse time; these
  tests document the invariant defensively and assert the stable ordering.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.ai.schemas.content import Channel, GeneratedBy, Provenance
from app.core.params import Params, load_params
from app.marketing.creator_scoring import (
    CreatorSignals,
    authenticity_score,
    fit_score,
    surface,
)
from app.marketing.schemas.discovery import (
    AudienceSegment,
    CreatorDataMode,
    CreatorRecord,
)

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[4] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-06-14T00:00:00Z")


def _record(
    *,
    id: UUID,
    fit: float,
    authenticity: float,
    data_mode: CreatorDataMode = CreatorDataMode.SYNTHETIC,
) -> CreatorRecord:
    """A valid, adults-only CreatorRecord carrying precomputed scores."""
    return CreatorRecord(
        id=id,
        display_handle="synthetic_handle",
        channel=Channel.INSTAGRAM,
        audience_segment=AudienceSegment.PARENTS,
        fit_score=fit,
        authenticity_score=authenticity,
        data_mode=data_mode,
        is_minor=False,
        provenance=_provenance(),
    )


# Stable UUIDs so the surface tiebreak (by id) is assertable.
ID_A = UUID("00000000-0000-0000-0000-0000000000aa")
ID_B = UUID("00000000-0000-0000-0000-0000000000bb")
ID_C = UUID("00000000-0000-0000-0000-0000000000cc")
ID_D = UUID("00000000-0000-0000-0000-0000000000dd")


# --------------------------------------------------------------------------- #
# Fit scorer — params-derived (INV-11).
# --------------------------------------------------------------------------- #


def test_creator_fit_score_matches_fixture() -> None:
    """fit_score equals topic*w_t + audience*w_a + brand*w_b, weights FROM params."""
    params = _params()
    fit = params.creator_scoring.fit
    signals = CreatorSignals(
        topic_match=0.8,
        audience_match=0.6,
        brand_alignment=0.4,
        follower_authenticity=0.0,
        engagement_consistency=0.0,
        spam_signal=0.0,
    )

    expected = (
        0.8 * fit.topic_match_weight
        + 0.6 * fit.audience_match_weight
        + 0.4 * fit.brand_alignment_weight
    )

    assert fit_score(signals, params=params) == expected
    assert 0.0 <= fit_score(signals, params=params) <= 1.0


def test_creator_fit_score_is_deterministic() -> None:
    """Repeated calls on the same signals return the identical float."""
    params = _params()
    signals = CreatorSignals(
        topic_match=0.3,
        audience_match=0.9,
        brand_alignment=0.5,
        follower_authenticity=0.0,
        engagement_consistency=0.0,
        spam_signal=0.0,
    )
    assert fit_score(signals, params=params) == fit_score(signals, params=params)


# --------------------------------------------------------------------------- #
# Authenticity scorer — params-derived, spam applied as a PENALTY (INV-11).
# --------------------------------------------------------------------------- #


def test_creator_authenticity_score_matches_fixture() -> None:
    """authenticity = follower*w1 + consistency*w2 + (1-spam)*w3, weights FROM params."""
    params = _params()
    auth = params.creator_scoring.authenticity
    signals = CreatorSignals(
        topic_match=0.0,
        audience_match=0.0,
        brand_alignment=0.0,
        follower_authenticity=0.9,
        engagement_consistency=0.7,
        spam_signal=0.25,
    )

    expected = (
        0.9 * auth.follower_authenticity_weight
        + 0.7 * auth.engagement_consistency_weight
        + (1.0 - 0.25) * auth.spam_signal_weight
    )

    assert authenticity_score(signals, params=params) == expected
    assert 0.0 <= authenticity_score(signals, params=params) <= 1.0


def test_authenticity_spam_is_a_penalty() -> None:
    """Higher spam_signal LOWERS authenticity (all else equal)."""
    params = _params()
    base = CreatorSignals(
        topic_match=0.0,
        audience_match=0.0,
        brand_alignment=0.0,
        follower_authenticity=0.8,
        engagement_consistency=0.8,
        spam_signal=0.0,
    )
    spammy = base.model_copy(update={"spam_signal": 1.0})
    assert authenticity_score(spammy, params=params) < authenticity_score(base, params=params)


# --------------------------------------------------------------------------- #
# surface — threshold filter + stable total order (INV-6 / FR-3.8).
# --------------------------------------------------------------------------- #


def test_surface_filters_below_threshold_and_orders_stably() -> None:
    """Only fit >= surface_threshold surfaces, sorted fit desc, auth desc, id."""
    params = _params()
    threshold = params.creator_scoring.surface_threshold  # 0.6

    below = _record(id=ID_A, fit=threshold - 0.1, authenticity=0.9)
    # Two records tie on fit -> break by authenticity desc.
    tie_hi_auth = _record(id=ID_C, fit=0.8, authenticity=0.9)
    tie_lo_auth = _record(id=ID_B, fit=0.8, authenticity=0.4)
    top = _record(id=ID_D, fit=0.95, authenticity=0.1)

    result = surface([below, tie_lo_auth, top, tie_hi_auth], params=params)

    # `below` is filtered out; the rest are ordered fit desc, then auth desc.
    assert [r.id for r in result] == [ID_D, ID_C, ID_B]
    assert all(r.fit_score >= threshold for r in result)


def test_surface_ordering_is_deterministic_across_calls() -> None:
    """Repeated calls on the same input yield an identical, total order."""
    params = _params()
    # Full tie on fit AND authenticity -> id is the final, total tiebreak.
    r1 = _record(id=ID_C, fit=0.7, authenticity=0.7)
    r2 = _record(id=ID_A, fit=0.7, authenticity=0.7)
    r3 = _record(id=ID_B, fit=0.7, authenticity=0.7)

    first = [r.id for r in surface([r1, r2, r3], params=params)]
    second = [r.id for r in surface([r3, r1, r2], params=params)]

    assert first == second == [ID_A, ID_B, ID_C]


def test_surface_emits_only_adults_only_aggregate_records() -> None:
    """INV-6: surfaced records are never minors and never live-scraped.

    The schema forbids `is_minor=True` and `live_scrape` at parse time, so a
    surfaced record can only be adults-only and synthetic/aggregate. A light
    assertion documents the invariant the scorer path must preserve.
    """
    params = _params()
    agg = _record(id=ID_A, fit=0.9, authenticity=0.8, data_mode=CreatorDataMode.AGGREGATE)
    syn = _record(id=ID_B, fit=0.7, authenticity=0.6, data_mode=CreatorDataMode.SYNTHETIC)

    result = surface([agg, syn], params=params)

    assert result, "expected both records to surface"
    for record in result:
        assert record.is_minor is False
        assert record.data_mode in (CreatorDataMode.SYNTHETIC, CreatorDataMode.AGGREGATE)
        assert record.audience_segment in (
            AudienceSegment.PARENTS,
            AudienceSegment.EDUCATORS,
            AudienceSegment.GENERAL,
        )

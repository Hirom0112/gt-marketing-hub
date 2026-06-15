"""Creator fit/authenticity scorer — FR-3.8 (LOCKED formula, INV-6/INV-11).

A `CreatorRecord` (`app.marketing.schemas.discovery`) CARRIES the result scores;
this module COMPUTES them from raw signals. The formula is params-driven — every
weight is read from `params.creator_scoring` (INV-11), never hardcoded — so a
drifted weight changes the score and the pinned tests fail.

* `fit_score` = topic·w_topic + audience·w_audience + brand·w_brand.
* `authenticity_score` = follower·w_follower + consistency·w_consistency +
  (1 - spam)·w_spam — the spam sub-factor is applied as a PENALTY (higher spam
  signal LOWERS authenticity).
* `surface` keeps creators with `fit_score >= surface_threshold`, ordered by a
  total, deterministic key (fit desc, authenticity desc, id) so the discovery
  list is stable across calls.

INV-6 (no child-keyed targeting / scraping of minors): the `CreatorRecord`
schema already rejects `is_minor=True` and has no `live_scrape` data mode at
parse time, so a minor / live-scrape record is not representable. `surface`
defensively filters on those invariants anyway — fail closed.

Pure (CLAUDE.md §3): imports only `app.core.params` and
`app.marketing.schemas.discovery` — no `anthropic` / `langgraph` / I/O /
`datetime`.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.params import Params
from app.marketing.schemas.discovery import CreatorDataMode, CreatorRecord


class CreatorSignals(BaseModel):
    """Raw creator sub-factors feeding the fit/authenticity scorers (FR-3.8).

    Frozen so a signal set is an immutable input. Each sub-factor is normalized
    to [0,1]. The fit signals (`topic_match`, `audience_match`, `brand_alignment`)
    measure alignment; the authenticity signals (`follower_authenticity`,
    `engagement_consistency`) measure trust, while `spam_signal` is INVERTED in
    the score — higher = more spam = lower authenticity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic_match: float = Field(ge=0.0, le=1.0)
    audience_match: float = Field(ge=0.0, le=1.0)
    brand_alignment: float = Field(ge=0.0, le=1.0)
    follower_authenticity: float = Field(ge=0.0, le=1.0)
    engagement_consistency: float = Field(ge=0.0, le=1.0)
    spam_signal: float = Field(ge=0.0, le=1.0)


def fit_score(signals: CreatorSignals, *, params: Params) -> float:
    """Weighted fit score in [0,1], weights read FROM params (INV-11).

    `topic_match·topic_match_weight + audience_match·audience_match_weight +
    brand_alignment·brand_alignment_weight`. The params loader guarantees the
    three weights partition to 1.0, so the result stays within [0,1].
    """
    fit = params.creator_scoring.fit
    return (
        signals.topic_match * fit.topic_match_weight
        + signals.audience_match * fit.audience_match_weight
        + signals.brand_alignment * fit.brand_alignment_weight
    )


def authenticity_score(signals: CreatorSignals, *, params: Params) -> float:
    """Weighted authenticity score in [0,1], weights read FROM params (INV-11).

    `follower_authenticity·w_follower + engagement_consistency·w_consistency +
    (1 - spam_signal)·w_spam`. The spam sub-factor is applied as a PENALTY: a
    perfectly spam-free creator (`spam_signal=0`) earns the full spam-weight,
    while a maximally spammy one (`spam_signal=1`) earns none. The params loader
    guarantees the three weights partition to 1.0, so the result stays in [0,1].
    """
    auth = params.creator_scoring.authenticity
    return (
        signals.follower_authenticity * auth.follower_authenticity_weight
        + signals.engagement_consistency * auth.engagement_consistency_weight
        + (1.0 - signals.spam_signal) * auth.spam_signal_weight
    )


def surface(creators: Sequence[CreatorRecord], *, params: Params) -> list[CreatorRecord]:
    """Surface creators at/above the fit threshold, in a stable total order.

    Keeps only records with `fit_score >= surface_threshold` (from params), then
    sorts by a total, deterministic key — `fit_score` desc, `authenticity_score`
    desc, then `id` — so the discovery list is identical across repeated calls
    regardless of input order.

    INV-6 (defensive, fail-closed): even though the `CreatorRecord` schema makes
    a minor / `live_scrape` record unrepresentable, `surface` filters those out
    so the scoring path can never emit a child-keyed or scraped creator.
    """
    threshold = params.creator_scoring.surface_threshold

    eligible = [
        creator
        for creator in creators
        if not creator.is_minor
        and creator.data_mode in (CreatorDataMode.SYNTHETIC, CreatorDataMode.AGGREGATE)
        and creator.fit_score >= threshold
    ]

    def _order_key(creator: CreatorRecord) -> tuple[float, float, UUID]:
        # Negate the descending fields; `id` ascending gives a total order.
        return (-creator.fit_score, -creator.authenticity_score, creator.id)

    return sorted(eligible, key=_order_key)

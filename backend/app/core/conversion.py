"""Deterministic conversion-likelihood scorer — the deal-view "who closes" signal (DH-1).

This replaces the meaningless "MAP signal" (an academic ``map_score`` with no data
behind it for the demo) with a real, params-weighted **conversion likelihood**:
how likely a family is to enroll, plus the single top contributing factor — built
from signals we already carry, "to use it to close".

The score is a weighted blend over five [0,1] dimensions, each weight read from
``params.conversion`` (INV-11 — nothing here is a code literal):

    score = w_affluence·affluence + w_income·income + w_children·children
          + w_funding·funding + w_depth·depth                       ∈ [0, 1]

* **affluence** — ``neighborhood`` is a coarse AGGREGATE area LABEL (a district
  name, e.g. "Highland Park"); it maps through the ``neighborhood_affluence``
  params table into [0,1] (richer area ⇒ likelier to afford tuition). NEVER precise
  minor geo (P-4 / INV-6) — a label only, with a documented default for an unknown
  label.
* **income** — the family's self-reported income (whole USD) normalized by
  ``income_reference`` and clamped to [0,1]. A ``None`` income is UNKNOWN, NOT low:
  it contributes the neutral ``income_neutral`` value (documented rule), never 0.
* **children** — ``num_children`` normalized by ``num_children_cap`` and clamped
  (more children ⇒ more commitment/value ⇒ higher).
* **funding** — the funding-type token mapped through ``funding_affinity`` into
  [0,1] (a funded voucher path ⇒ money is lined up ⇒ likelier to close).
* **depth** — the REUSED ``recoverability`` term from ``core/work_queue.py``
  (funnel depth); it is passed IN as a precomputed [0,1] float, NOT recomputed and
  NOT a second/new funnel score.

The coarse band ("High"/"Med"/"Low") comes from ``band_high_cutoff`` /
``band_med_cutoff``; the **top contributing factor** is the dimension with the
largest WEIGHTED contribution (ties broken deterministically by declaration order),
surfaced as a human-readable label for the UI tile.

This is part of the deterministic core and stays pure: a function of its typed
inputs + params alone — no clock, no random, no IO — and it imports nothing from
``app.ai`` / ``app.adapters`` (the core-purity test guards this).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.params import Params

# The five scored dimensions, in declaration order. The order is the deterministic
# tie-break for the top-contributing factor (an earlier dimension wins an exact
# tie), and it pins the human-readable labels for the UI tile.
_DIMENSIONS: tuple[str, ...] = ("affluence", "income", "children", "funding", "depth")

# Human-readable factor labels for the deal-view tile (DH-1). The scorer returns
# both the machine ``top_factor`` key and this label so the UI shows e.g.
# "Funding lined up" rather than the raw "funding" token.
_FACTOR_LABELS: dict[str, str] = {
    "affluence": "Neighborhood affluence",
    "income": "Family income",
    "children": "Number of children",
    "funding": "Funding lined up",
    "depth": "Application progress",
}


class ConversionSignals(BaseModel):
    """The raw inputs the conversion-likelihood scorer reads — its pure input (DH-1).

    A small typed projection of the family's already-present signals. Frozen so a
    scored family cannot mutate mid-scoring.

    Attributes:
        neighborhood: The coarse AGGREGATE area LABEL (a district name; e.g.
            "Highland Park"). Mapped to affluence via the params table — never
            precise minor geo (P-4 / INV-6). An unmapped label uses the documented
            default affluence.
        self_reported_income: The family's self-reported household income in whole
            USD, or ``None`` when not yet provided. ``None`` is UNKNOWN (contributes
            the neutral value), NEVER treated as low/zero.
        num_children: The number of children on the lead (>= 1). More children ⇒
            higher value/commitment. Clamped to ``[0, cap]`` (via the [0,1] norm).
        funding_type: The funding-type token (the ``FundingType`` enum VALUE, e.g.
            ``"tefa_standard"``) or ``None`` when not yet known. A funded voucher
            path scores higher (money lined up). An unknown / ``None`` token uses
            the documented default affinity.
        depth: The application-depth term in [0,1] — the REUSED ``recoverability``
            value (funnel depth) computed by ``core/work_queue.py`` and passed in.
            NOT recomputed here and NOT a new funnel score. Clamped defensively.
    """

    model_config = ConfigDict(frozen=True)

    neighborhood: str
    self_reported_income: int | None
    num_children: int
    funding_type: str | None
    depth: float


class ConversionScore(BaseModel):
    """The conversion-likelihood result — what the deal view surfaces (DH-1).

    Attributes:
        score: The conversion likelihood in [0,1].
        band: The coarse band — "High" / "Med" / "Low" — from the params cutoffs.
        top_factor: The machine key of the dimension that contributed the most
            (one of :data:`_DIMENSIONS`); the deterministic tie-break is declaration
            order.
        top_factor_label: The human-readable label for ``top_factor`` (for the UI
            tile, e.g. "Funding lined up").
        contributions: The per-dimension WEIGHTED contribution (each ``weight ×
            dimension``), so the UI/observability can show the full breakdown, not
            just the winner. Sums to ``score``.
    """

    model_config = ConfigDict(frozen=True)

    score: float
    band: str
    top_factor: str
    top_factor_label: str
    contributions: dict[str, float]


def _clamp01(value: float) -> float:
    """Clamp a value into [0,1] so every dimension sub-score stays normalized."""
    return max(0.0, min(1.0, value))


def _affluence_subscore(signals: ConversionSignals, params: Params) -> float:
    """Neighborhood-affluence sub-score ∈ [0,1] from the params area-label table.

    The ``neighborhood`` is a coarse AGGREGATE area LABEL (P-4 / INV-6); an unknown
    / unmapped label falls back to the documented ``neighborhood_affluence_default``
    (neutral, never penalized).
    """
    cfg = params.conversion
    return cfg.neighborhood_affluence.get(signals.neighborhood, cfg.neighborhood_affluence_default)


def _income_subscore(signals: ConversionSignals, params: Params) -> float:
    """Self-reported-income sub-score ∈ [0,1] — ``income / reference`` clamped.

    A ``None`` income is UNKNOWN, not low: it contributes the neutral
    ``income_neutral`` value (the documented rule), never 0.
    """
    cfg = params.conversion
    if signals.self_reported_income is None:
        return _clamp01(cfg.income_neutral)
    return _clamp01(signals.self_reported_income / cfg.income_reference)


def _children_subscore(signals: ConversionSignals, params: Params) -> float:
    """Child-count sub-score ∈ [0,1] — ``num_children / cap`` clamped (more ⇒ higher)."""
    cfg = params.conversion
    return _clamp01(signals.num_children / cfg.num_children_cap)


def _funding_subscore(signals: ConversionSignals, params: Params) -> float:
    """Funding-type sub-score ∈ [0,1] from the params affinity table.

    A funded voucher path scores higher (money lined up). An unknown / ``None``
    funding token uses the documented ``funding_affinity_default``.
    """
    cfg = params.conversion
    if signals.funding_type is None:
        return cfg.funding_affinity_default
    return cfg.funding_affinity.get(signals.funding_type, cfg.funding_affinity_default)


def _band_for(score: float, params: Params) -> str:
    """Map a [0,1] score to the coarse band from the params cutoffs."""
    cfg = params.conversion
    if score >= cfg.band_high_cutoff:
        return "High"
    if score >= cfg.band_med_cutoff:
        return "Med"
    return "Low"


def conversion_likelihood(signals: ConversionSignals, params: Params) -> ConversionScore:
    """Score a family's conversion likelihood (DH-1).

    Pure: a deterministic function of ``signals`` + ``params`` alone (no clock,
    random, or IO). Computes each [0,1] dimension sub-score, scales it by its
    params weight (the weights partition to 1.0, so the score stays in [0,1]), sums
    them, derives the coarse band, and picks the top WEIGHTED contributor (ties
    broken by declaration order). Every weight, cutoff, and table entry is read
    from ``params.conversion`` — nothing here is a code literal (INV-11).

    Args:
        signals: The family's raw conversion inputs (neighborhood label, income,
            child count, funding token, and the reused recoverability depth).
        params: Loaded params (§8); supplies the five weights, band cutoffs, the
            neighborhood→affluence table + default, the income reference + neutral,
            the child-count cap, and the funding-affinity table + default.

    Returns:
        The :class:`ConversionScore` — score in [0,1], band, top factor (+ label),
        and the full per-dimension weighted-contribution breakdown.
    """
    weights = params.conversion.weights
    subscores = {
        "affluence": _affluence_subscore(signals, params),
        "income": _income_subscore(signals, params),
        "children": _children_subscore(signals, params),
        "funding": _funding_subscore(signals, params),
        "depth": _clamp01(signals.depth),
    }
    contributions = {
        dimension: getattr(weights, dimension) * subscores[dimension] for dimension in _DIMENSIONS
    }
    score = sum(contributions.values())

    # Top factor = largest weighted contribution; ties broken by declaration order
    # (an earlier dimension wins), so the result is fully deterministic.
    top_factor = max(
        _DIMENSIONS,
        key=lambda dimension: (contributions[dimension], -_DIMENSIONS.index(dimension)),
    )

    return ConversionScore(
        score=score,
        band=_band_for(score, params),
        top_factor=top_factor,
        top_factor_label=_FACTOR_LABELS[top_factor],
        contributions=contributions,
    )

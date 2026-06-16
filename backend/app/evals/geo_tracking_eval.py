"""GEO tracking eval — coverage vs the 0% baseline, by repeated sampling (FR-4.4).

CONTENT_SPEC §7.4 (LOCKED) / RESEARCH Q5 ("Don't Measure Once") / ARCH §9: GEO
coverage is **stochastic** — identical prompts yield different citations, so a
single snapshot is invalid. This eval therefore measures coverage by **repeated
sampling with variance reported**, against the **0% baseline**
(`params.geo.baseline_coverage`), and reports the **lift** (coverage − baseline).

Fail-closed (INV-3): when fewer than `min_samples_per_prompt` runs were taken a
point estimate CANNOT be asserted (ARCH §9 failure table: "report with widened
CI + variance flag; do not assert a point estimate"). The result then carries
`insufficient_samples=True` and `enabled=False`, so the UI disables the
generate-to-win GEO action — a red eval disables the action, never softens it.

INV-11: the `min_samples_per_prompt` threshold has its single home in
`params/params.yaml` and is read here from the passed-in `Params`; it is NEVER
hardcoded. `report_variance` (also a param) gates whether variance is surfaced.

Purity: this is deterministic logic over passed-in observations + params. It
imports only the pure metrics layer, the `GeoObservation` type, and the `Params`
type — no `anthropic`/`langgraph`, no network, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.geo_sampling.base import GeoObservation
from app.core.params import Params
from app.evals.geo_metrics import citation_share, coverage, sample_stats

# GT's own domain — the brand whose citation share is the GEO leadership metric
# (GT ≈ low vs competitors ≈ high; growth-strategy.md Bet 3). The simulated corpus
# cites these domains; the share is computed over the sampled `cited_domains`.
_GT_DOMAIN = "gtschool.com"
_COMPETITOR_DOMAINS: tuple[str, ...] = (
    "joinprisma.com",
    "fusionacademy.com",
    "davidsononline.org",
    "k12.com",
    "niche.com",
)


class GeoTrackingResult(BaseModel):
    """Repeated-sampling GEO coverage result (FR-4.4; ARCH §9; INV-3).

    Frozen: an immutable verdict over one batch of observations.

    Attributes:
        coverage_mean: Mean per-run coverage (fraction of runs GT was cited in),
            in [0.0, 1.0]. Mean of the per-run `coverage()` values.
        baseline: The 0% baseline GT starts from (`params.geo.baseline_coverage`).
        lift: `coverage_mean - baseline` — the coverage gained over the baseline.
        variance: Population variance of the per-run coverages, surfaced only when
            `params.eval_thresholds.geo_tracking.report_variance` is True (else
            0.0). The variance flag is honored, not assumed.
        sample_count: Number of distinct sampling runs (distinct `run_index`),
            compared against `min_samples_per_prompt`.
        insufficient_samples: True when `sample_count < min_samples_per_prompt` —
            too few runs to trust a point estimate (ARCH §9).
        enabled: False whenever `insufficient_samples` is True — fail-closed
            (INV-3); the GEO action is disabled/red when samples are insufficient.
        gt_citation_share: GT's share of ALL citation slots across the sampled
            observations (`citation_share`, FR-3.7) — the ~3% leadership figure
            (growth-strategy.md Bet 3). In [0.0, 1.0].
        competitor_citation_share: Per-competitor citation share over the same
            slots — the ~50%-each figure GT is measured against. The shares of GT
            + the competitors over distinct domains sum to ≤ 1.0.
    """

    model_config = ConfigDict(frozen=True)

    coverage_mean: float
    baseline: float
    lift: float
    variance: float
    sample_count: int
    insufficient_samples: bool
    enabled: bool
    gt_citation_share: float = 0.0
    competitor_citation_share: dict[str, float] = Field(default_factory=dict)


def evaluate_geo_tracking(
    observations: Sequence[GeoObservation],
    *,
    params: Params,
) -> GeoTrackingResult:
    """Evaluate GEO coverage over `observations` vs the 0% baseline (FR-4.4).

    Groups observations into sampling runs by `run_index` (each run is one
    sampling pass over the prompt set), computes per-run coverage from each run's
    `brand_cited` booleans, then summarizes mean/variance and the
    insufficient-samples flag via `sample_stats`, with the threshold read from
    `params` (INV-11). Reports coverage vs the 0% baseline and the lift, and
    fails closed (`enabled=False`) when samples are insufficient (INV-3).

    Args:
        observations: The sampled GEO observations (offline/simulated in v1).
        params: The validated params; supplies `min_samples_per_prompt`,
            `report_variance`, and the 0% `geo.baseline_coverage`.

    Returns:
        A `GeoTrackingResult` carrying coverage mean, baseline, lift, variance
        (surfaced per the `report_variance` param), the run count, the
        insufficient-samples flag, and the fail-closed `enabled` verdict.
    """
    geo_cfg = params.eval_thresholds.geo_tracking
    min_samples = geo_cfg.min_samples_per_prompt
    baseline = params.geo.baseline_coverage

    # Group observations into sampling runs by run_index. Each run is one
    # sampling pass over the prompt set; its coverage is the fraction of that
    # pass's prompts in which GT's domain was cited.
    runs: dict[int, list[bool]] = {}
    for obs in observations:
        runs.setdefault(obs.run_index, []).append(obs.brand_cited)

    per_run_coverages = [coverage(runs[run_index]) for run_index in sorted(runs)]
    sample_count = len(per_run_coverages)

    stats = sample_stats(per_run_coverages, min_samples_per_prompt=min_samples)

    # Honor the report_variance param: surface variance only when configured to.
    variance = stats.variance if geo_cfg.report_variance else 0.0

    # GT-vs-competitor citation share (FR-3.7) over EVERY citation slot across all
    # observations — the ~3%-GT-vs-~50%-competitor leadership view (growth-strategy
    # Bet 3) the `citation_share` metric exists to surface. Flatten the per-run
    # `cited_domains` into one slot stream, then take each brand's share.
    slots: list[str] = []
    for obs in observations:
        slots.extend(obs.cited_domains)
    gt_share = citation_share(slots, _GT_DOMAIN)
    competitor_share = {domain: citation_share(slots, domain) for domain in _COMPETITOR_DOMAINS}

    return GeoTrackingResult(
        coverage_mean=stats.mean,
        baseline=baseline,
        lift=stats.mean - baseline,
        variance=variance,
        sample_count=sample_count,
        insufficient_samples=stats.insufficient_samples,
        # Fail-closed (INV-3): insufficient samples ⇒ the GEO action is disabled.
        enabled=not stats.insufficient_samples,
        gt_citation_share=gt_share,
        competitor_citation_share=competitor_share,
    )

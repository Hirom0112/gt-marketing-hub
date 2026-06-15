"""Pure GEO metrics — coverage, citation share, repeated-sampling stats.

FR-3.7 / FR-4.4; CONTENT_SPEC §7.4 (LOCKED); RESEARCH Q5 ("Don't Measure Once").

GEO coverage is stochastic: identical prompts yield different citations, so a
single-snapshot coverage claim is invalid. Coverage MUST be measured by repeated
sampling with variance reported. The GEO baseline is 0%
(`params.geo.baseline_coverage = 0.0`).

This module is PURE: stdlib + typing only — no I/O, no network, no clock, no
params import. The `min_samples_per_prompt` threshold has its single home in
`params/params.yaml` (INV-11) and is passed in by the caller (the eval that
reads params), never hardcoded here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


def coverage(samples: Sequence[bool]) -> float:
    """Fraction of citation slots in which the brand was cited.

    `samples` is one boolean per slot (True = brand cited in that slot). Returns
    cited / total. Empty input ⇒ 0.0 (defined; never raises) — there is no
    coverage to report when nothing was sampled.
    """
    if not samples:
        return 0.0
    cited = sum(1 for s in samples if s)
    return cited / len(samples)


def citation_share(samples: Sequence[str], brand: str) -> float:
    """Share of citation slots whose cited domain equals `brand`.

    `samples` is one cited-domain string per slot. Returns
    occurrences-of-`brand` / total-slots, so a single brand's share is always in
    [0.0, 1.0] and the shares of all distinct brands sum to ≤ 1.0. Empty ⇒ 0.0.
    """
    if not samples:
        return 0.0
    hits = sum(1 for s in samples if s == brand)
    return hits / len(samples)


class SampleStats(NamedTuple):
    """Repeated-sampling result over per-run coverages (FR-4.4).

    `mean` and `variance` (population variance) summarize the coverage
    distribution; `insufficient_samples` is True when too few runs were taken to
    trust the estimate, per the caller-supplied threshold.
    """

    mean: float
    variance: float
    insufficient_samples: bool


def sample_stats(
    per_run_coverages: Sequence[float],
    *,
    min_samples_per_prompt: int,
) -> SampleStats:
    """Mean, population variance, and insufficient-samples flag across runs.

    `per_run_coverages` is one `coverage()` value per sampling run. Variance is
    the *population* variance (mean of squared deviations, no Bessel
    correction). `insufficient_samples` is True when
    `len(per_run_coverages) < min_samples_per_prompt`. Empty input ⇒ mean 0.0,
    variance 0.0, and insufficient_samples per the threshold.
    """
    n = len(per_run_coverages)
    insufficient = n < min_samples_per_prompt
    if n == 0:
        return SampleStats(mean=0.0, variance=0.0, insufficient_samples=insufficient)
    mean = sum(per_run_coverages) / n
    variance = sum((x - mean) ** 2 for x in per_run_coverages) / n
    return SampleStats(mean=mean, variance=variance, insufficient_samples=insufficient)

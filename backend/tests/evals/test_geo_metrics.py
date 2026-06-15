"""Pure GEO metrics tests — FR-3.7 / FR-4.4 (CONTENT_SPEC §7.4, RESEARCH Q5).

GEO coverage is *stochastic*: identical prompts yield different citations, so a
single-snapshot claim is invalid (CONTENT_SPEC §7.4, LOCKED). Coverage MUST be
measured by repeated sampling with variance reported ("Don't Measure Once").
The GEO baseline is 0% (`params.geo.baseline_coverage = 0.0`).

`app/evals/geo_metrics.py` is a PURE math module: stdlib + typing only, no I/O,
no params import. The `min_samples_per_prompt` threshold lives once in params
(INV-11) and is passed in by the caller — never hardcoded here.
"""

from __future__ import annotations

import pytest

from app.evals.geo_metrics import SampleStats, citation_share, coverage, sample_stats


def test_coverage_zero_baseline() -> None:
    """N=10, K=0 cited ⇒ coverage == 0.0 (the 0%-baseline first red)."""
    samples = [False] * 10
    assert coverage(samples) == 0.0


def test_coverage_fraction() -> None:
    """N=20 with K=5 cited ⇒ 5/20 == 0.25."""
    samples = [True] * 5 + [False] * 15
    assert coverage(samples) == pytest.approx(0.25)


def test_coverage_empty_is_zero() -> None:
    """Empty input ⇒ 0.0 (defined, never raises)."""
    assert coverage([]) == 0.0


def test_citation_share() -> None:
    """GT cited in 3/12 slots ⇒ 0.25; any single brand's share ≤ 1.0."""
    samples = (
        ["gtschool.com"] * 3
        + ["competitor-a.com"] * 4
        + ["competitor-b.com"] * 5
    )
    share = citation_share(samples, "gtschool.com")
    assert share == pytest.approx(0.25)
    # A single brand cannot exceed all slots.
    assert share <= 1.0
    # Sum of every distinct brand's share is ≤ 1.0.
    brands = set(samples)
    total = sum(citation_share(samples, b) for b in brands)
    assert total <= 1.0 + 1e-9


def test_citation_share_empty_is_zero() -> None:
    """Empty slot list ⇒ 0.0."""
    assert citation_share([], "gtschool.com") == 0.0


def test_citation_share_full_is_one() -> None:
    """Brand in every slot ⇒ exactly 1.0 (the ≤ 1.0 ceiling is reachable)."""
    assert citation_share(["gtschool.com"] * 4, "gtschool.com") == pytest.approx(1.0)


def test_variance_across_samples() -> None:
    """coverages [0.0,0.2,0.4,0.2,0.2] ⇒ mean 0.2, population variance 0.016."""
    runs = [0.0, 0.2, 0.4, 0.2, 0.2]
    stats = sample_stats(runs, min_samples_per_prompt=5)
    assert isinstance(stats, SampleStats)
    assert stats.mean == pytest.approx(0.2)
    assert round(stats.variance, 4) == 0.016
    # 5 runs meets the threshold ⇒ sufficient.
    assert stats.insufficient_samples is False


def test_insufficient_samples_flag() -> None:
    """Fewer than `min_samples_per_prompt` runs ⇒ insufficient_samples True."""
    stats = sample_stats([0.0, 0.2, 0.4, 0.2], min_samples_per_prompt=5)
    assert stats.insufficient_samples is True


def test_insufficient_samples_threshold_is_a_parameter() -> None:
    """The threshold is a passed-in int (INV-11), not hardcoded 5."""
    runs = [0.1, 0.2]
    assert sample_stats(runs, min_samples_per_prompt=2).insufficient_samples is False
    assert sample_stats(runs, min_samples_per_prompt=3).insufficient_samples is True

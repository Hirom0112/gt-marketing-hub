"""Conversion-likelihood scorer tests (DH-1; ARCHITECTURE.md §8, CLAUDE.md §4.1).

The deterministic conversion-likelihood signal (``app/core/conversion.py``)
replaces the meaningless "MAP signal" in the deal view: a params-weighted blend
over five dimensions — neighborhood affluence, self-reported income, child count,
funding type, and application depth (the REUSED ``recoverability`` term, NOT a new
funnel score) — yielding a [0,1] score, a coarse band (High/Med/Low), and the
single top contributing factor for the operator tile.

These tests pin the expected score/band/top-factor for the curated demo cohort
(MULTI_AGENT_COCKPIT §10.1) and DERIVE the expected value FROM the loaded params
(INV-11): a drifted weight, cutoff, or table entry flips a test red. They also
pin the documented neutral handling of a ``None`` self-reported income. Strict
TDD (CLAUDE.md §4.1): written to fail before the impl exists, pass after.

Deterministic without a local ``params/params.yaml`` (gitignored, not created):
the committed ``params/params.example.yaml`` is passed explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.conversion import (
    ConversionScore,
    ConversionSignals,
    conversion_likelihood,
)
from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _expected(signals: ConversionSignals, params: object) -> tuple[float, str, str]:
    """Recompute the expected (score, band, top_factor) FROM the loaded params.

    A pure mirror of the scorer's formula that reads the SAME params (INV-11), so
    if a weight / cutoff / table entry drifts in the YAML this expectation moves
    with it and the assertion below catches the drift rather than masking it.
    """
    cfg = params.conversion  # type: ignore[attr-defined]
    w = cfg.weights

    affluence = cfg.neighborhood_affluence.get(
        signals.neighborhood, cfg.neighborhood_affluence_default
    )
    if signals.self_reported_income is None:
        income = cfg.income_neutral
    else:
        income = _clamp01(signals.self_reported_income / cfg.income_reference)
    children = _clamp01(signals.num_children / cfg.num_children_cap)
    funding = (
        cfg.funding_affinity_default
        if signals.funding_type is None
        else cfg.funding_affinity.get(signals.funding_type, cfg.funding_affinity_default)
    )
    depth = _clamp01(signals.depth)

    contributions = {
        "affluence": w.affluence * affluence,
        "income": w.income * income,
        "children": w.children * children,
        "funding": w.funding * funding,
        "depth": w.depth * depth,
    }
    score = sum(contributions.values())
    # Deterministic tie-break: first dimension (declaration order) wins a tie.
    top = max(contributions, key=lambda k: (contributions[k], -list(contributions).index(k)))
    band = (
        "High"
        if score >= cfg.band_high_cutoff
        else ("Med" if score >= cfg.band_med_cutoff else "Low")
    )
    return score, band, top


# The six curated demo households (MULTI_AGENT_COCKPIT §10.1), with the SAME raw
# inputs the synthetic generator seeds onto the cohort. ``depth`` is the REUSED
# stage-proximity recoverability term (Interest 0.0 → Apply 1/3 → Enroll 2/3 →
# Tuition 1.0) — NOT a new funnel score (DH-1 reuse requirement).
_RIVERA = ConversionSignals(
    neighborhood="Highland Park",
    self_reported_income=185_000,
    num_children=2,
    funding_type="tefa_standard",
    depth=2 / 3,
)
_OKAFOR = ConversionSignals(
    neighborhood="Highland Park",
    self_reported_income=240_000,
    num_children=1,
    funding_type="tefa_standard",
    depth=1.0,
)
_NGUYEN = ConversionSignals(
    neighborhood="Riverside",
    self_reported_income=95_000,
    num_children=1,
    funding_type="self_pay",
    depth=1.0,
)
_PATEL = ConversionSignals(
    neighborhood="Eastgate",
    self_reported_income=52_000,
    num_children=1,
    funding_type="tefa_disability",
    depth=1.0,
)
_KIM = ConversionSignals(
    neighborhood="Lakeview",
    self_reported_income=78_000,
    num_children=1,
    funding_type="self_pay",
    depth=1 / 3,
)
_SILVA = ConversionSignals(
    neighborhood="Eastgate",
    self_reported_income=None,  # not yet provided — the neutral-handling case.
    num_children=1,
    funding_type="tefa_homeschool",
    depth=0.0,
)


@pytest.mark.parametrize(
    ("signals", "expected_band"),
    [
        (_OKAFOR, "High"),
        (_RIVERA, "High"),
        (_NGUYEN, "Med"),
        (_KIM, "Med"),
        (_PATEL, "Med"),
        (_SILVA, "Low"),
    ],
    ids=["okafor", "rivera", "nguyen", "kim", "patel", "silva"],
)
def test_demo_family_score_band_and_top_factor(
    signals: ConversionSignals, expected_band: str
) -> None:
    """The scorer equals the params-derived score + band + top factor per family.

    The expected score is recomputed FROM the loaded params (``_expected``), so a
    weight/cutoff/table drift moves the expectation and the equality below stays
    honest (INV-11). Score asserted to 4 dp; band + top-factor asserted exactly.
    """
    params = load_params(EXAMPLE_PARAMS)
    exp_score, exp_band, exp_top = _expected(signals, params)

    result = conversion_likelihood(signals, params)

    assert isinstance(result, ConversionScore)
    assert result.score == pytest.approx(exp_score, abs=1e-9)
    assert round(result.score, 4) == round(exp_score, 4)
    assert result.band == exp_band == expected_band
    assert result.top_factor == exp_top
    assert result.top_factor_label  # a human-readable, non-empty UI label.
    assert 0.0 <= result.score <= 1.0


def test_score_reads_params_not_a_hardcoded_literal() -> None:
    """A pinned literal AND its param source — the test fails if the weight drifts.

    Okafor (the prime case) scores 0.8450 against the COMMITTED example weights.
    We assert BOTH the literal and the params it derives from, so retuning a
    weight without updating this expectation flips it red (CLAUDE.md §4.1).
    """
    params = load_params(EXAMPLE_PARAMS)
    w = params.conversion.weights
    # Pin the committed weights so the literal below is anchored to params.
    assert (w.affluence, w.income, w.children, w.funding, w.depth) == (
        0.20,
        0.20,
        0.15,
        0.25,
        0.20,
    )
    result = conversion_likelihood(_OKAFOR, params)
    assert round(result.score, 4) == 0.8450


def test_none_income_uses_the_neutral_value_not_zero() -> None:
    """A ``None`` self-reported income contributes the NEUTRAL value, not zero.

    Documented rule: missing income is UNKNOWN, never treated as low/zero. Two
    otherwise-identical signals — one with ``income=None``, one with an income at
    exactly ``income_reference·income_neutral`` — must score IDENTICALLY, proving
    the neutral substitution (and that None is not floored to 0).
    """
    params = load_params(EXAMPLE_PARAMS)
    cfg = params.conversion
    base = ConversionSignals(
        neighborhood="Riverside",
        self_reported_income=None,
        num_children=1,
        funding_type="self_pay",
        depth=0.5,
    )
    equiv_income = int(round(cfg.income_reference * cfg.income_neutral))
    equiv = ConversionSignals(
        neighborhood="Riverside",
        self_reported_income=equiv_income,
        num_children=1,
        funding_type="self_pay",
        depth=0.5,
    )
    none_score = conversion_likelihood(base, params).score
    equiv_score = conversion_likelihood(equiv, params).score
    assert none_score == pytest.approx(equiv_score, abs=1e-9)

    # And a ZERO income scores STRICTLY LOWER than the None/neutral case — proving
    # None is not silently mapped to the low end.
    zero = base.model_copy(update={"self_reported_income": 0})
    assert conversion_likelihood(zero, params).score < none_score


def test_unknown_neighborhood_uses_the_documented_default() -> None:
    """An unrecognized neighborhood label maps to the documented default affluence."""
    params = load_params(EXAMPLE_PARAMS)
    unknown = ConversionSignals(
        neighborhood="Nowhere District",
        self_reported_income=100_000,
        num_children=1,
        funding_type="self_pay",
        depth=0.5,
    )
    # The label is genuinely absent from the table, so the default must apply.
    assert "Nowhere District" not in params.conversion.neighborhood_affluence
    # The params mirror (_expected) applies the same default fallback, so the
    # scorer must agree — proving the unknown label routes to the documented default.
    result = conversion_likelihood(unknown, params)
    exp_score, _, _ = _expected(unknown, params)
    assert result.score == pytest.approx(exp_score, abs=1e-9)

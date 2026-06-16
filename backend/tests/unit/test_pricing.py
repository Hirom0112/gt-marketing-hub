"""Token→USD pricing helper tests (INV-8, INV-11; TECH_STACK §6.1).

The per-run and cross-run USD caps (`cost_caps.anthropic_per_run_usd`,
`COST_DAILY_USD_CAP`) only bite if a live call charges REAL dollars. The pricing
helper (`app/ai/pricing.py::usd_for`) converts the model's reported input/output
token counts into the USD cost from the canonical per-MTok rates — which live in
`params/params.yaml` (`anthropic_pricing` block), never a code literal (INV-11).

usd = (input_tokens * input_$/MTok + output_tokens * output_$/MTok) / 1_000_000

These are the RED tests:

1. exact USD for known token counts per model (Opus 1M+1M = $30.00; mixed counts);
2. rates come from params — a drifted param changes the result (INV-11);
3. an unpriced model id fails loud (KeyError), never a silent $0 (config gap).

The committed `params/params.example.yaml` is the authoritative params source,
mirroring the cost-cap suite (`params/params.yaml` is gitignored/absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.pricing import usd_for
from app.core.params import load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params():
    return load_params(EXAMPLE_PARAMS)


# --------------------------------------------------------------------------- #
# 1. Exact USD for known token counts per model (TECH_STACK §6.1 rates).
# --------------------------------------------------------------------------- #
def test_opus_one_mtok_each_direction() -> None:
    """Opus: 1M input ($5) + 1M output ($25) = $30.00 exactly."""
    usd = usd_for(
        model="claude-opus-4-8",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        params=_params(),
    )
    assert usd == pytest.approx(30.00)


def test_sonnet_mixed_small_counts() -> None:
    """Sonnet ($3 in / $15 out): 10_000 in + 2_000 out = 0.03 + 0.03 = $0.06."""
    usd = usd_for(
        model="claude-sonnet-4-6",
        input_tokens=10_000,
        output_tokens=2_000,
        params=_params(),
    )
    assert usd == pytest.approx(0.06, abs=1e-9)


def test_haiku_mixed_small_counts() -> None:
    """Haiku ($1 in / $5 out): 50_000 in + 4_000 out = 0.05 + 0.02 = $0.07."""
    usd = usd_for(
        model="claude-haiku-4-5-20251001",
        input_tokens=50_000,
        output_tokens=4_000,
        params=_params(),
    )
    assert usd == pytest.approx(0.07, abs=1e-9)


def test_zero_tokens_is_zero() -> None:
    """No tokens ⇒ no cost (a degraded run charges nothing)."""
    assert usd_for(
        model="claude-opus-4-8",
        input_tokens=0,
        output_tokens=0,
        params=_params(),
    ) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 2. DRIFT — the rate comes from params, not a code literal (INV-11).
# --------------------------------------------------------------------------- #
def test_rate_reads_from_params_drift() -> None:
    """Doubling the Opus input rate in params doubles the input-side cost."""
    params = _params()
    base = usd_for(
        model="claude-opus-4-8",
        input_tokens=1_000_000,
        output_tokens=0,
        params=params,
    )
    assert base == pytest.approx(5.00)

    # Mutate the in-memory params block; the helper must reflect it.
    params.anthropic_pricing.models["claude-opus-4-8"].input_per_mtok = 10.00
    drifted = usd_for(
        model="claude-opus-4-8",
        input_tokens=1_000_000,
        output_tokens=0,
        params=params,
    )
    assert drifted == pytest.approx(10.00)


# --------------------------------------------------------------------------- #
# 3. UNPRICED MODEL — fail loud, never a silent $0 (config gap, not free).
# --------------------------------------------------------------------------- #
def test_unpriced_model_raises() -> None:
    """An unknown model id raises (KeyError) rather than charging $0 silently."""
    with pytest.raises(KeyError):
        usd_for(
            model="claude-imaginary-9-0",
            input_tokens=1_000,
            output_tokens=1_000,
            params=_params(),
        )

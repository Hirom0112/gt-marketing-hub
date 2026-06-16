"""Live `complete()` charges REAL USD so the cost caps bite (INV-8; TECH_STACK §6.1/§6.2).

Previously `client.py` charged `usd=0.0`, so the per-run USD cap
(`cost_caps.anthropic_per_run_usd`) and the daily cap never accrued any dollars
and never tripped on cost. These tests prove a permitted live call now prices its
reported tokens via `app/ai/pricing.py` for the ACTUAL model the client uses
(`settings.anthropic_model_primary`) and charges the budget that real USD — and
that enough priced calls trip the per-run USD ceiling and degrade to the
deterministic template (fail-closed, INV-8).

No live Anthropic call ever runs: a fake transport reports known token counts.
The committed `params/params.example.yaml` is the authoritative params source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.client import AnthropicLLMClient
from app.ai.cost import RunBudget
from app.ai.pricing import usd_for
from app.core.params import load_params
from app.core.settings import Settings

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params():
    return load_params(EXAMPLE_PARAMS)


def _live_settings(**overrides: object) -> Settings:
    """A live-keyed settings snapshot (so a call is permitted)."""
    base: dict[str, object] = {"anthropic_api_key": "sk-test-live-key"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 3. A permitted live call charges the budget the REAL priced USD, not 0.0.
# --------------------------------------------------------------------------- #
def test_complete_charges_real_usd() -> None:
    """The budget accrues the priced USD for the reported tokens (not 0.0)."""
    params = _params()
    settings = _live_settings()

    in_tokens, out_tokens = 10_000, 2_000

    def fake_transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return ("DRAFTED: " + prompt, in_tokens, out_tokens)

    budget = RunBudget.from_config(settings=settings, params=params)
    client = AnthropicLLMClient(settings=settings, transport=fake_transport, params=params)
    result = client.complete("draft a nudge", max_tokens=512, budget=budget)

    expected = usd_for(
        model=settings.anthropic_model_primary,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        params=params,
    )
    assert expected > 0.0
    assert result.degraded is False
    assert budget.tokens_used == in_tokens + out_tokens
    assert budget.usd_spent == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# 4. END-TO-END cap bite — enough priced live calls trip the per-run USD cap.
# --------------------------------------------------------------------------- #
def test_priced_calls_trip_per_run_usd_cap_and_degrade() -> None:
    """Repeated priced live calls accrue USD until the per-run cap trips ⇒ degrade.

    Each call is well under the TOKEN cap, so it can only be the USD ceiling that
    fails closed — proving the dollar cap now actually enforces. Once tripped, the
    client degrades to the deterministic template (INV-8).
    """
    params = _params()
    cap = params.cost_caps.anthropic_per_run_usd
    settings = _live_settings()

    # Each call: Opus at 100k in + 100k out = 100k*5/1e6 + 100k*25/1e6 = $3.00.
    per_call = usd_for(
        model=settings.anthropic_model_primary,
        input_tokens=100_000,
        output_tokens=100_000,
        params=params,
    )
    assert per_call == pytest.approx(3.00)

    def fake_transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return ("DRAFTED", 100_000, 100_000)

    budget = RunBudget.from_config(settings=settings, params=params)
    client = AnthropicLLMClient(settings=settings, transport=fake_transport, params=params)

    degraded_seen = False
    for _ in range(10):
        result = client.complete("draft", max_tokens=512, budget=budget)
        if result.degraded:
            degraded_seen = True
            break

    assert degraded_seen, "the per-run USD cap never tripped under priced live calls"
    assert budget.tripped
    # Never overspent past the cap.
    assert budget.usd_spent <= cap + 1e-9

"""Token→USD pricing for live Anthropic calls (INV-8, INV-11; TECH_STACK §6.1).

`RunBudget` is deliberately designed so USD is charged EXPLICITLY by the caller,
never inferred inside the budget or in `core/` — that keeps the per-run governor
a pure accumulator and the core free of a per-token rate. This helper lives in
the AI layer and owns that one inference: it reads the canonical per-MTok rates
(data) from params and turns the model's reported token counts into the real
dollar cost the budget then charges.

    usd = (input_tokens * input_$/MTok + output_tokens * output_$/MTok) / 1e6

Rates are tunables, so they live in `params/params.yaml` (`anthropic_pricing`),
never a literal here (INV-11). An unpriced model id is a CONFIG GAP, not free
work: this raises ``KeyError`` rather than silently charging $0, so the caps keep
biting (INV-8 fail-loud posture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.params import Params

_TOKENS_PER_MTOK = 1_000_000


def usd_for(*, model: str, input_tokens: int, output_tokens: int, params: Params) -> float:
    """Return the USD cost of a call for `model` at the params per-MTok rates.

    Args:
        model: the model id the call ACTUALLY used (e.g. ``settings.anthropic_model_primary``).
        input_tokens: prompt tokens the model reported.
        output_tokens: completion tokens the model reported.
        params: the loaded params carrying the `anthropic_pricing` rates (INV-11).

    Raises:
        KeyError: if `model` has no pricing entry — a config gap, never a silent $0.
    """
    rates = params.anthropic_pricing.models[model]
    return (
        input_tokens * rates.input_per_mtok + output_tokens * rates.output_per_mtok
    ) / _TOKENS_PER_MTOK

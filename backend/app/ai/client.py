"""Thin, mockable LLM client wrapper — gated by the kill switch and the budget.

`AnthropicLLMClient` is the single seam through which the AI edge reaches a live
model. It fails closed before ever touching the network:

* if ``settings.llm_available`` is False (no key, or ``LLM_KILL_SWITCH=true``),
* or if the per-run :class:`~app.ai.cost.RunBudget` is already ``tripped``,

it returns a clearly-marked deterministic **template** (`degraded=True`) instead
of calling out — never a silent skip, never an overspend (INV-8, NFR-5).

When a live call IS permitted it goes through an injectable ``transport``
callable. The default transport lazily imports the ``anthropic`` SDK *inside the
call* — so importing this module needs no SDK and no key (core-purity / light
imports stay intact), and tests inject a fake transport so no live call ever
runs under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.ai.cost import CostCapExceeded, RunBudget
from app.core.settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class LLMResult:
    """The outcome of a `complete` call.

    `degraded` is True when the deterministic template path was used instead of
    a live model call (unavailable edge or tripped budget); `text` then holds
    the operator template. Token counts are 0 on the degraded path.
    """

    text: str
    degraded: bool
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class LLMClient(Protocol):
    """The contract the AI edge depends on — one method, fully mockable."""

    def complete(self, prompt: str, *, max_tokens: int, budget: RunBudget) -> LLMResult:
        """Return a completion, or a degraded template when unavailable/over-cap."""
        ...


def deterministic_fallback(prompt: str) -> str:
    """The operator-facing template returned when the live edge is unavailable.

    A pure function (no I/O): a clearly-marked placeholder a human operator can
    fill in, so a degraded run still surfaces *something* actionable rather than
    failing or silently overspending. Marked so it is never mistaken for a
    model-authored draft (INV-2 — this is a proposal stand-in, not a state write).
    """
    return (
        "[DEGRADED — LLM unavailable or per-run cost cap reached] "
        "Operator template; complete manually before review. "
        f"Request: {prompt}"
    )


def _default_transport(
    settings: Settings,
) -> Callable[..., tuple[str, int, int]]:
    """Build the real Anthropic transport, importing the SDK lazily on first call.

    The ``import anthropic`` lives *inside* the returned closure so this module
    (and `_default_transport` itself) imports with no SDK installed and no key —
    the SDK is only required when an actual live call is made.
    """

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        import anthropic  # lazy: optional live-only dep (mypy: see [tool.mypy] override)

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.anthropic_model_primary,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return (text, usage.input_tokens, usage.output_tokens)

    return transport


class AnthropicLLMClient:
    """Kill-switch- and budget-gated Anthropic client with an injectable transport.

    Args:
        settings: env snapshot consulted for ``llm_available``; defaults to a
            fresh :func:`~app.core.settings.get_settings` read.
        transport: the call mechanism. Defaults to the real (lazy-importing)
            Anthropic SDK transport; tests inject a fake to avoid any live call.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: Callable[..., tuple[str, int, int]] | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._transport = transport if transport is not None else _default_transport(self._settings)

    def complete(self, prompt: str, *, max_tokens: int, budget: RunBudget) -> LLMResult:
        """Complete `prompt`, or degrade to the template when blocked.

        Fails closed (degraded template, no live call) when the edge is
        unavailable or the budget is already tripped. Otherwise calls the
        transport and charges the per-run budget with the reported usage; a cap
        breach mid-run also degrades rather than raising to the caller.
        """
        if not self._settings.llm_available or budget.tripped:
            return LLMResult(text=deterministic_fallback(prompt), degraded=True)

        text, input_tokens, output_tokens = self._transport(prompt, max_tokens=max_tokens)
        try:
            budget.charge(tokens=input_tokens + output_tokens, usd=0.0)
        except CostCapExceeded:
            return LLMResult(text=deterministic_fallback(prompt), degraded=True)
        return LLMResult(
            text=text,
            degraded=False,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

"""Per-run cost/token governor + kill switch tests (S2; INV-8, NFR-5; CLAUDE.md §4.1).

Every metered LLM run is bounded by a HARD per-run token cap (env
`LLM_RUN_TOKEN_CAP`) AND a hard per-run USD cap (`params.cost_caps`
`.anthropic_per_run_usd`). Exceeding *either* trips the kill switch: the budget
reports `tripped` and the next call is REFUSED — the caller degrades to a
deterministic template path (`degraded=True`) and records NO further live
charge. There is never a silent overspend (INV-8).

The `LLMClient` wrapper additionally fails closed when the LLM edge is
unavailable: `LLM_KILL_SWITCH=true` or a missing `ANTHROPIC_API_KEY` ⇒
`settings.llm_available` is False ⇒ degraded path, no raise, no live call.

To prove "never live in tests", an injectable FAKE transport callable stands in
for the Anthropic SDK; the suite asserts it is NOT invoked once the budget is
tripped or the edge is unavailable. No live Anthropic call ever happens here,
and importing `app.ai.client` pulls in no SDK (the import is lazy).

Caps are read from settings/params — never hardcoded (INV-11). The committed
`params/params.example.yaml` is passed explicitly (`params/params.yaml` is
gitignored/absent), mirroring `test_work_queue.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.client import AnthropicLLMClient, LLMResult
from app.ai.cost import CostCapExceeded, RunBudget
from app.core.params import load_params
from app.core.settings import get_settings

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _budget() -> RunBudget:
    """A RunBudget built from the live settings snapshot + the example params."""
    settings = get_settings()
    params = load_params(EXAMPLE_PARAMS)
    return RunBudget.from_config(settings=settings, params=params)


def _exploding_transport(*_args: object, **_kwargs: object) -> object:
    """A transport that must never be called once tripped/unavailable."""
    raise AssertionError("live transport was invoked — fail-closed posture violated")


def test_under_cap_allows() -> None:
    """A charge well under both caps leaves the budget un-tripped and chargeable."""
    budget = _budget()
    assert not budget.tripped
    assert not budget.would_exceed(tokens=10, usd=0.01)
    budget.charge(tokens=10, usd=0.01)
    assert not budget.tripped
    assert budget.tokens_used == 10
    assert budget.usd_spent == pytest.approx(0.01)


def test_token_cap_trips_kill_switch() -> None:
    """A charge that would exceed the per-run TOKEN cap is refused and trips the budget."""
    budget = _budget()
    over = budget.token_cap + 1
    assert budget.would_exceed(tokens=over, usd=0.0)
    with pytest.raises(CostCapExceeded):
        budget.charge(tokens=over, usd=0.0)
    assert budget.tripped


def test_usd_cap_trips_kill_switch() -> None:
    """A charge that would exceed the per-run USD cap is refused and trips the budget."""
    budget = _budget()
    over = budget.usd_cap + 0.01
    assert budget.would_exceed(tokens=0, usd=over)
    with pytest.raises(CostCapExceeded):
        budget.charge(tokens=0, usd=over)
    assert budget.tripped


def test_draft_run_trips_kill_switch_over_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run accumulates usage; once a charge would breach a cap the next call degrades.

    The client is given a live key (so unavailability is NOT the reason it
    degrades) but the budget is already tripped — the degraded template path is
    returned, flagged `degraded=True`, and the live transport is NEVER invoked
    (no further live charge recorded).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-live-key")
    budget = _budget()

    # Accumulate live usage right up to — then past — the USD cap.
    budget.charge(tokens=100, usd=budget.usd_cap - 0.50)
    assert not budget.tripped
    spent_before = budget.usd_spent
    tokens_before = budget.tokens_used

    # The next charge would breach the cap: it is refused and trips the switch.
    with pytest.raises(CostCapExceeded):
        budget.charge(tokens=100, usd=1.00)
    assert budget.tripped
    # No partial charge leaked through the refusal.
    assert budget.usd_spent == pytest.approx(spent_before)
    assert budget.tokens_used == tokens_before

    client = AnthropicLLMClient(transport=_exploding_transport)
    result = client.complete("draft a nudge", max_tokens=512, budget=budget)

    assert isinstance(result, LLMResult)
    assert result.degraded is True
    assert result.text  # a clearly-marked operator template, non-empty
    # The tripped budget recorded no further spend from the degraded call.
    assert budget.usd_spent == pytest.approx(spent_before)
    assert budget.tokens_used == tokens_before


def test_kill_switch_env_blocks_all_live_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_KILL_SWITCH=true ⇒ llm_available False ⇒ degraded path, transport untouched."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-live-key")
    monkeypatch.setenv("LLM_KILL_SWITCH", "true")
    settings = get_settings()
    assert settings.llm_available is False

    budget = _budget()
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport)
    result = client.complete("draft a nudge", max_tokens=512, budget=budget)

    assert result.degraded is True
    assert result.text
    assert not budget.tripped  # availability gate, not a cap breach


def test_missing_key_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ANTHROPIC_API_KEY ⇒ llm_available False ⇒ degraded path, no raise, no live call."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = get_settings()
    assert settings.llm_available is False

    budget = _budget()
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport)
    result = client.complete("draft a nudge", max_tokens=512, budget=budget)

    assert result.degraded is True
    assert result.text


def test_available_client_calls_injected_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """When available and under cap, the client uses the injected transport (no live SDK)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-live-key")
    monkeypatch.delenv("LLM_KILL_SWITCH", raising=False)

    calls: list[str] = []

    def fake_transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        calls.append(prompt)
        return ("DRAFTED: " + prompt, 11, 22)

    settings = get_settings()
    assert settings.llm_available is True
    budget = _budget()
    client = AnthropicLLMClient(settings=settings, transport=fake_transport)
    result = client.complete("draft a nudge", max_tokens=512, budget=budget)

    assert calls == ["draft a nudge"]
    assert result.degraded is False
    assert result.text == "DRAFTED: draft a nudge"
    assert result.input_tokens == 11
    assert result.output_tokens == 22

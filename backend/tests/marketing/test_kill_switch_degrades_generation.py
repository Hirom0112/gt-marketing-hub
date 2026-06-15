"""The kill switch / cost cap degrades generation with NO live call (INV-8, NFR-5).

When `LLM_KILL_SWITCH=true` (or the per-run budget is breached), the generation
graph must NOT make a live LLM call. It degrades to persistent brand-memory /
library candidates — never a silent overspend. We inject an EXPLODING transport:
if any live call were attempted the test would raise, proving the kill switch
short-circuits before the network.

Drives `app.ai.graphs.content_generate.generate_content_batch` on the degraded
path.
"""

from __future__ import annotations

from pathlib import Path

from app.ai.client import AnthropicLLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.content_generate import generate_content_batch
from app.ai.schemas.content import Channel
from app.core.params import load_params
from app.core.settings import Settings

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _exploding_transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
    """A transport that MUST never be called on the degraded path."""
    raise AssertionError("live LLM call attempted under kill switch / cost cap")


def _seed_store(tmp_path: Path):
    from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
    from app.data.synthetic import generate_brand_memory

    params = load_params(EXAMPLE_PARAMS)
    store = SqliteBrandMemoryStore(
        tmp_path / "brand.sqlite3", weight_step=params.brand_memory.weight_step
    )
    for item in generate_brand_memory():
        store.upsert(item)
    return store


def _on_brand_judge(score: float = 0.99):
    def judge(record: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def test_kill_switch_degrades_no_live_call(tmp_path: Path) -> None:
    """`LLM_KILL_SWITCH=true` ⇒ no live call; degraded candidates from persistence."""
    params = load_params(EXAMPLE_PARAMS)
    # Kill switch on ⇒ llm_available False ⇒ the client degrades before the transport.
    settings = Settings(anthropic_api_key="sk-test", llm_kill_switch=True)
    assert settings.llm_available is False

    store = _seed_store(tmp_path)
    budget = RunBudget.from_config(settings=settings, params=params)
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport)

    outcome = generate_content_batch(
        "Draft Instagram copy.",
        Channel.INSTAGRAM,
        store=store,
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    # Degraded path: marked degraded, and it returned persistent candidates (no
    # live call was made — the exploding transport never raised).
    assert outcome.degraded is True
    assert len(outcome.surfaced) >= 1
    for _candidate, validation in outcome.surfaced:
        assert validation.passed is True


def test_cost_cap_breach_degrades_no_live_call(tmp_path: Path) -> None:
    """A pre-tripped per-run budget ⇒ no live call; degraded persistent candidates."""
    params = load_params(EXAMPLE_PARAMS)
    settings = Settings(anthropic_api_key="sk-test")  # key present, NOT killed

    store = _seed_store(tmp_path)
    budget = RunBudget.from_config(settings=settings, params=params)
    # Trip the budget before the run (cap breach).
    budget._tripped = True  # noqa: SLF001 — exercising the tripped-budget seam
    assert budget.tripped is True

    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport)

    outcome = generate_content_batch(
        "Draft Instagram copy.",
        Channel.INSTAGRAM,
        store=store,
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.degraded is True
    assert len(outcome.surfaced) >= 1

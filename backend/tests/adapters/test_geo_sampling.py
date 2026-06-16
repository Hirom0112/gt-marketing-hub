"""Simulated GeoSamplingAdapter — FR-3.7, FR-4.4, INV-9 (ARCHITECTURE.md §7.6).

GEO coverage is **stochastic**: identical prompts yield different citations, so
coverage MUST be measured by **repeated sampling** with variance reported — a
single-snapshot coverage claim is invalid (CONTENT_SPEC §7.4). §7.6:

    interface GeoSamplingAdapter:
      sample(prompt_set, engine) -> [GeoObservation]   # repeated sampling, variance reported

"v1: simulated against the 0% baseline with the gifted-school competitor set
(RESEARCH.md Q6), using repeated sampling because stochasticity is real
(RESEARCH.md Q5)." Live polling of real AI engines is OUT in v1 (PROJECT §7).

These are the §4.1-adapter-scope RED tests:

- ``sample(...)`` returns ≥ ``min_samples_per_prompt`` observations per prompt,
  each a frozen ``GeoObservation``.
- **Deterministic** under a fixed seed: same (prompt_set, engine, seed) ⇒
  identical observations across calls and fresh instances (tests stay stable).
- **Stochastic** across runs: observations vary across ``run_index`` — repeated
  sampling is real, not a constant snapshot.
- ``min_samples_per_prompt`` is PASSED IN (the caller reads it from params); the
  adapter does not hardcode or import it.
- The simulated impl is a pure, offline source — it imports no http client and no
  ``anthropic``; "simulated, not a live engine call" is structural (INV-9).
- The registry returns the simulated impl under the v1 default; ``live`` fails
  **loud** (``NotImplementedError``) — never a silent live engine poll.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import ValidationError

from app.adapters.geo_sampling.base import GeoObservation, GeoSamplingAdapter
from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.adapters.registry import get_geo_sampling_adapter

_PROMPTS = (
    "best online school for gifted children",
    "top accredited gifted education programs",
)
_ENGINE = "test-engine"


def test_sample_returns_at_least_min_observations_per_prompt() -> None:
    """``sample`` ⇒ ≥ ``min_samples_per_prompt`` frozen observations per prompt."""
    adapter = SimulatedGeoSamplingAdapter()
    assert isinstance(adapter, GeoSamplingAdapter)

    min_samples = 5
    observations = adapter.sample(_PROMPTS, _ENGINE, min_samples_per_prompt=min_samples, seed=7)

    assert len(observations) >= min_samples * len(_PROMPTS)
    for obs in observations:
        assert isinstance(obs, GeoObservation)
        assert obs.engine == _ENGINE
        assert obs.prompt in _PROMPTS
        assert isinstance(obs.cited_domains, tuple)
        assert isinstance(obs.brand_cited, bool)

    # Each prompt got at least ``min_samples`` observations.
    for prompt in _PROMPTS:
        per_prompt = [o for o in observations if o.prompt == prompt]
        assert len(per_prompt) >= min_samples
        # run_index is dense 0..n-1 — repeated sampling, slot-ordered.
        assert {o.run_index for o in per_prompt} == set(range(len(per_prompt)))


def test_observation_is_frozen() -> None:
    """A ``GeoObservation`` is an immutable record of one sampling run."""
    obs = SimulatedGeoSamplingAdapter().sample(_PROMPTS, _ENGINE, min_samples_per_prompt=3, seed=1)[
        0
    ]
    with pytest.raises(ValidationError):
        obs.brand_cited = True  # type: ignore[misc]


def test_deterministic_under_fixed_seed() -> None:
    """Same (prompt_set, engine, seed) ⇒ identical observations, even fresh instance."""
    first = SimulatedGeoSamplingAdapter().sample(
        _PROMPTS, _ENGINE, min_samples_per_prompt=6, seed=42
    )
    same = SimulatedGeoSamplingAdapter().sample(
        _PROMPTS, _ENGINE, min_samples_per_prompt=6, seed=42
    )
    assert first == same

    # A different seed yields a different sampling sequence (real stochasticity).
    other_seed = SimulatedGeoSamplingAdapter().sample(
        _PROMPTS, _ENGINE, min_samples_per_prompt=6, seed=43
    )
    assert other_seed != first


def test_stochastic_across_runs() -> None:
    """Observations VARY across ``run_index`` — repeated sampling, not a snapshot."""
    adapter = SimulatedGeoSamplingAdapter()
    observations = adapter.sample(_PROMPTS, _ENGINE, min_samples_per_prompt=12, seed=99)
    prompt = _PROMPTS[0]
    runs = [o for o in observations if o.prompt == prompt]
    distinct = {o.cited_domains for o in runs}
    # Identical prompt, repeated sampling ⇒ the citations must not all be identical.
    assert len(distinct) > 1


def test_gt_brand_near_zero_baseline() -> None:
    """GT starts near the 0% baseline — rarely cited under the seed default (Q6)."""
    adapter = SimulatedGeoSamplingAdapter()
    observations = adapter.sample(_PROMPTS, _ENGINE, min_samples_per_prompt=40, seed=5)
    brand_hits = sum(1 for o in observations if o.brand_cited)
    # Coverage-vs-0%-baseline is demonstrable: GT is cited in a small minority.
    assert brand_hits < len(observations) // 2


def test_simulated_is_not_a_live_engine_call() -> None:
    """Structural INV-9: the module is a pure, offline source — no live engine.

    It imports no http client and no ``anthropic`` — there is no AI engine to
    poll, so "simulated, not a live call" is provable from the source text.
    """
    module = importlib.import_module("app.adapters.geo_sampling.simulated")
    source = inspect.getsource(module)

    forbidden = ("httpx", "requests", "aiohttp", "urllib", "socket", "anthropic")
    for token in forbidden:
        assert token not in source, f"simulated geo adapter must not reference {token!r}"


def test_publishing_a_prompt_raises_gt_coverage() -> None:
    """generate-to-win flywheel: a PUBLISHED prompt samples at a higher GT cite rate.

    Today GT's cite-probability is a fixed near-0% constant, so publishing can
    never move coverage. The adapter consults a published-prompt registry: once a
    prompt is published, GT is cited far more often FOR THAT PROMPT (the lift
    amount is a param, read by the adapter — INV-11). The OTHER prompts are
    unaffected (the win is prompt-scoped). Deterministic under a fixed seed.
    """
    adapter = SimulatedGeoSamplingAdapter()
    won, other = _PROMPTS

    def gt_hits(prompt: str) -> int:
        obs = adapter.sample([prompt], _ENGINE, min_samples_per_prompt=40, seed=3)
        return sum(1 for o in obs if o.brand_cited)

    baseline_hits = gt_hits(won)

    # Publish a piece for `won` — the adapter now cites GT much more for it.
    adapter.publish(won, published_cite_buckets=200)

    assert gt_hits(won) > baseline_hits, "publishing must RAISE GT coverage (lift > 0)"
    # The win is prompt-scoped: a prompt that was NOT published is unchanged.
    assert gt_hits(other) == 0 or gt_hits(other) <= baseline_hits + 2


def test_published_lift_amount_is_parametric() -> None:
    """The lift amount is the param the adapter is told, not a hardcoded literal.

    A larger `published_cite_buckets` cites GT more often than a smaller one — so
    a code literal would NOT respond to the param (INV-11). Deterministic seed.
    """
    won = _PROMPTS[0]

    def hits_with(buckets: int) -> int:
        adapter = SimulatedGeoSamplingAdapter()
        adapter.publish(won, published_cite_buckets=buckets)
        obs = adapter.sample([won], _ENGINE, min_samples_per_prompt=60, seed=4)
        return sum(1 for o in obs if o.brand_cited)

    assert hits_with(240) > hits_with(64)


def test_unpublished_prompt_stays_near_baseline() -> None:
    """With no publish, the adapter is unchanged — GT stays near the 0% baseline."""
    adapter = SimulatedGeoSamplingAdapter()
    obs = adapter.sample(_PROMPTS, _ENGINE, min_samples_per_prompt=40, seed=5)
    brand_hits = sum(1 for o in obs if o.brand_cited)
    assert brand_hits < len(obs) // 2


def test_registry_returns_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 default ⇒ simulated impl; a future live mode fails loud (no silent poll)."""
    monkeypatch.setenv("SEND_MODE", "simulate")
    adapter = get_geo_sampling_adapter()
    assert isinstance(adapter, SimulatedGeoSamplingAdapter)
    assert isinstance(adapter, GeoSamplingAdapter)

    monkeypatch.setenv("SEND_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_geo_sampling_adapter()

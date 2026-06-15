"""The GEO sampling boundary — interface + observation model (ARCHITECTURE.md §7.6).

§7.6 (authoritative):

    interface GeoSamplingAdapter:
      sample(prompt_set, engine) -> [GeoObservation]   # repeated sampling, variance reported

"v1: simulated against the 0% baseline with the gifted-school competitor set
(RESEARCH.md Q6), using repeated sampling because stochasticity is real
(RESEARCH.md Q5)."

GEO coverage is **stochastic**: identical prompts yield different citations, so
coverage MUST be measured by repeated sampling with variance reported — a single
snapshot is invalid (CONTENT_SPEC §7.4). Hence ``sample`` returns *multiple*
observations per prompt (≥ ``min_samples_per_prompt``), and the downstream
metrics derive coverage (the ``brand_cited`` booleans) and citation-share (the
per-slot ``cited_domains`` lists) from this stream.

INV-9: like every external boundary, this is an interface with two impls —
Simulated (v1) and Production (go-live) — selected by config in
:mod:`app.adapters.registry`. Live polling of real AI engines is OUT in v1
(PROJECT §7); the simulated impl is a pure, offline source with no network client
at all, so "simulated, not a live engine call" is a structural property. This
module imports nothing from ``anthropic`` and keeps ``core/`` untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict


class GeoObservation(BaseModel):
    """One simulated AI-engine sampling run for one prompt (§7.6, CONTENT_SPEC §7.4).

    Frozen: an observation is an immutable record of a single sampled answer,
    never a mutable row. The metrics layer consumes ``brand_cited`` for coverage
    and ``cited_domains`` (ordered as citation slots) for citation-share.

    Attributes:
        prompt: The prompt that was sampled.
        engine: The AI engine identifier the sample is attributed to.
        run_index: The 0-based index of this run within the prompt's repeated
            sampling (dense, ``0..n-1``) — repeated sampling is explicit here.
        cited_domains: The domains cited in this simulated answer, ordered as the
            citation slots they occupied.
        brand_cited: Convenience flag — was GT's own domain among ``cited_domains``
            (i.e. did GT earn coverage in this run).
    """

    model_config = ConfigDict(frozen=True)

    prompt: str
    engine: str
    run_index: int
    cited_domains: tuple[str, ...]
    brand_cited: bool


class GeoSamplingAdapter(ABC):
    """The GEO sampling external boundary (§7.6).

    Two impls — Simulated (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. The metrics/eval layer depends only on this
    interface, never on a concrete engine client.
    """

    @abstractmethod
    def sample(
        self,
        prompt_set: Sequence[str],
        engine: str,
        *,
        min_samples_per_prompt: int,
        seed: int = 0,
    ) -> list[GeoObservation]:
        """Repeatedly sample ``engine`` over ``prompt_set`` (§7.6, CONTENT_SPEC §7.4).

        Returns at least ``min_samples_per_prompt`` :class:`GeoObservation` per
        prompt — repeated sampling, so variance can be reported downstream.
        ``min_samples_per_prompt`` is supplied by the caller (it reads the value
        from ``params``); this interface never hardcodes it.

        Args:
            prompt_set: The prompts to sample.
            engine: The AI engine identifier to attribute samples to.
            min_samples_per_prompt: Minimum repeated samples per prompt.
            seed: Seed for deterministic-but-stochastic sampling (stable tests).
        """

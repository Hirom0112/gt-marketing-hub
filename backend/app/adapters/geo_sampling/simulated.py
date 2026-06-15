"""Simulated GeoSamplingAdapter — synthetic, offline, no live engine (INV-9).

The v1 impl of the §7.6 boundary. It generates, per prompt, ``min_samples_per_prompt``
repeated :class:`GeoObservation`s whose cited domains are derived
**deterministically** from ``(prompt, run_index, seed)`` — so the same inputs
always reproduce the same sampling stream (tests stay stable), while the answers
still **vary across runs** (stochasticity is real, CONTENT_SPEC §7.4, RESEARCH.md
Q5). There is **no network client** here by construction — no http transport and
no LLM SDK, no live AI engine — so "simulated, not a live engine call" (INV-9,
PROJECT §7) holds structurally, provable from the source text alone.

Determinism without shared randomness: each citation decision is a salted
``hashlib.blake2b`` digest keyed on ``(seed, prompt, run_index, domain)`` (the
same technique as :mod:`app.adapters.funding.simulated`) — no ``random`` global
state, no wall-clock. GT's own domain starts near the **0% baseline** (rarely
cited under the seed default, RESEARCH.md Q6) so coverage-vs-0%-baseline is
demonstrable; the gifted-school competitor set is cited far more often.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.adapters.geo_sampling.base import GeoObservation, GeoSamplingAdapter

# The gifted-school competitor set (RESEARCH.md Q6) plus GT's own domain. These
# are the only domains the simulated engine can "cite". This is the fixed
# synthetic universe of the v1 simulation, not a tunable governing live behaviour
# (no live engine exists in v1), so it lives with the simulation it defines.
_GT_DOMAIN = "gtschool.com"
_COMPETITOR_DOMAINS = (
    "joinprisma.com",
    "fusionacademy.com",
    "davidsononline.org",
    "k12.com",
    "niche.com",
)

# Per-domain citation likelihood, expressed as the number of the 256 digest
# buckets (out of `_BUCKETS`) that count as "cited". GT sits near the 0% baseline
# (cited ~3% of runs) so coverage gains are demonstrable against it; competitors
# are cited far more often. These shape the synthetic distribution only.
_BUCKETS = 256
_GT_CITE_BUCKETS = 8  # ~3% — near the 0% baseline (RESEARCH.md Q6).
_COMPETITOR_CITE_BUCKETS = 128  # ~50% each — competitors dominate the citations.


def _digest_byte(seed: int, prompt: str, run_index: int, domain: str) -> int:
    """One deterministic byte from ``(seed, prompt, run_index, domain)``.

    A salted BLAKE2b digest keyed per (run, domain) gives an independent decision
    with no shared randomness state — pure, no I/O, stable across processes.
    """
    key = f"{seed}:{prompt}:{run_index}:{domain}".encode()
    return hashlib.blake2b(key, digest_size=8).digest()[0]


class SimulatedGeoSamplingAdapter(GeoSamplingAdapter):
    """Offline synthetic source for GEO sampling (INV-9, PROJECT §7).

    No network client exists on this class — "simulated, not a live engine call"
    is therefore a structural property, not a configured behaviour. Each
    ``sample`` call replays a deterministic-but-varying citation stream derived
    from ``(prompt, run_index, seed)``.
    """

    def sample(
        self,
        prompt_set: Sequence[str],
        engine: str,
        *,
        min_samples_per_prompt: int,
        seed: int = 0,
    ) -> list[GeoObservation]:
        """Repeatedly sample over ``prompt_set`` (§7.6, CONTENT_SPEC §7.4).

        Returns exactly ``min_samples_per_prompt`` observations per prompt — the
        ≥ guarantee with no waste. Citations vary across ``run_index`` yet are
        fully reproducible under ``(prompt, run_index, seed)``. No I/O, no live
        engine (INV-9).
        """
        observations: list[GeoObservation] = []
        for prompt in prompt_set:
            for run_index in range(min_samples_per_prompt):
                cited = self._cited_domains(seed, prompt, run_index)
                observations.append(
                    GeoObservation(
                        prompt=prompt,
                        engine=engine,
                        run_index=run_index,
                        cited_domains=cited,
                        brand_cited=_GT_DOMAIN in cited,
                    )
                )
        return observations

    @staticmethod
    def _cited_domains(seed: int, prompt: str, run_index: int) -> tuple[str, ...]:
        """Deterministically decide which domains are cited in one simulated run.

        Each domain is included iff its salted digest byte falls in its citation
        bucket band. Slot order is the fixed domain order, so ``cited_domains``
        doubles as the ordered citation slots the metrics layer consumes.
        """
        cited: list[str] = []
        for domain in (_GT_DOMAIN, *_COMPETITOR_DOMAINS):
            threshold = _GT_CITE_BUCKETS if domain == _GT_DOMAIN else _COMPETITOR_CITE_BUCKETS
            if _digest_byte(seed, prompt, run_index, domain) < threshold:
                cited.append(domain)
        return tuple(cited)

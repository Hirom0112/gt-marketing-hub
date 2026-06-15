"""Placeholder SentimentAdapter — synthetic, offline, aggregate-only (INV-6/9, OUT-5).

The v1 impl of the §7.5 boundary. ``fetch`` returns an **aggregate** sentiment
summary derived **deterministically** from the window — bucket counts only, with
``source_mode="placeholder"`` (never ``live_feed``) and **no per-person or
child-keyed field** (INV-6: no minor targeting). There is **no network client**
here by construction, so "no live feed" (INV-9) holds structurally, provable from
the source text alone (no live transport to mock).

Determinism without shared entropy: each bucket count is a salted
``hashlib.blake2b`` digest of ``(bucket, window)`` mapped into a bounded range
(the same technique as :mod:`app.adapters.funding.simulated`) — no PRNG global
state, no v4 GUIDs, no wall-clock. The same window always yields the same
aggregate across calls and fresh instances; different windows differ.
"""

from __future__ import annotations

import hashlib

from app.adapters.sentiment.base import SentimentAdapter, SentimentSummary, SentimentWindow

# The synthetic per-bucket count band. A placeholder summary over synthetic data
# spreads counts across a small bounded range so the aggregate is non-trivial and
# windows differ; this shapes the synthetic distribution only (no live feed in
# v1), so it lives with the simulation it defines.
_MIN_COUNT = 5
_COUNT_SPAN = 95  # counts land in [5, 99] per bucket — a non-trivial aggregate.


def _bucket_count(bucket: str, window: SentimentWindow) -> int:
    """Deterministic synthetic count for ``bucket`` over ``window``.

    A salted BLAKE2b digest mapped into ``[_MIN_COUNT, _MIN_COUNT + _COUNT_SPAN)``
    gives a stable, non-negative count with no shared entropy state — pure, no
    I/O, reproducible across processes (no PRNG/GUID/wall-clock).
    """
    key = f"{bucket}:{window.start}:{window.end}".encode()
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return _MIN_COUNT + (digest[0] % _COUNT_SPAN)


class PlaceholderSentimentAdapter(SentimentAdapter):
    """Offline synthetic source for sentiment (INV-6/9, OUT-5: no live feed).

    No network client exists on this class — "no live feed" is therefore a
    structural property, not a configured behaviour. ``fetch`` returns an
    aggregate-only summary (counts by bucket, ``source_mode="placeholder"``)
    derived deterministically from the window; no per-person record is ever
    produced (INV-6).
    """

    def fetch(self, window: SentimentWindow) -> SentimentSummary:
        """Return an aggregate :class:`SentimentSummary` for ``window`` (§7.5, INV-6).

        Bucket counts are derived deterministically from the window; the summary
        is aggregate-only with ``source_mode="placeholder"`` — never ``live_feed``
        (OUT-5), never per-person (INV-6).
        """
        positive = _bucket_count("positive", window)
        neutral = _bucket_count("neutral", window)
        negative = _bucket_count("negative", window)
        return SentimentSummary(
            positive=positive,
            neutral=neutral,
            negative=negative,
            total=positive + neutral + negative,
            source_mode="placeholder",
        )

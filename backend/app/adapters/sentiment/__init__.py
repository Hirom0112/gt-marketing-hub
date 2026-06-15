"""Sentiment-feed adapter package — the §7.5 boundary (OUT-5, INV-6, INV-9).

A ``SentimentAdapter`` interface with a ``PlaceholderSentimentAdapter`` that
returns deterministic **aggregate-only** summaries over synthetic data — counts
by positive/neutral/negative, ``source_mode="placeholder"`` (never ``live_feed``)
and no per-person/minor-keyed field (INV-6) — with no network client (no live
feed, OUT-5). v1 ships only the placeholder impl; ``live`` is reserved and fails
loud in :mod:`app.adapters.registry`.
"""

from app.adapters.sentiment.base import (
    SentimentAdapter,
    SentimentSummary,
    SentimentWindow,
    SourceMode,
)
from app.adapters.sentiment.placeholder import PlaceholderSentimentAdapter

__all__ = [
    "PlaceholderSentimentAdapter",
    "SentimentAdapter",
    "SentimentSummary",
    "SentimentWindow",
    "SourceMode",
]

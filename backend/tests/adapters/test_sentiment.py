"""Placeholder SentimentAdapter — FR-3.x sentiment, OUT-5, INV-6/INV-9 (ARCH §7.5).

Sentiment is **aggregate only** over synthetic data in v1 — no live feed, and
**no minor-keyed targeting or scraping** (P-4, INV-6). §7.5:

    interface SentimentAdapter:
      fetch(window) -> SentimentSummary   # aggregate only, no minor targeting

"v1: placeholder over synthetic — returns an aggregate summary, ``source_mode``
never ``live_feed``." Counts/share by positive/neutral/negative — never a per-
person or child-keyed record (INV-6).

These are the §4.1-adapter-scope RED tests:

- ``fetch(window)`` ⇒ a frozen aggregate ``SentimentSummary`` with
  ``source_mode="placeholder"`` (never ``"live_feed"``) and non-negative bucket
  counts that sum to ``total``.
- Deterministic for a given window (no ``random``/``uuid4``/wall-clock).
- The placeholder impl is a pure, offline source — it imports no http client and
  no ``anthropic``; "no live feed" is structural (INV-9), and it exposes no
  per-person/minor-keyed field (INV-6).
- The registry returns the placeholder impl under the v1 default; ``live`` fails
  **loud** (``NotImplementedError``) — never a silent live feed.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import ValidationError

from app.adapters.registry import get_sentiment_adapter
from app.adapters.sentiment.base import (
    SentimentAdapter,
    SentimentSummary,
    SentimentWindow,
)
from app.adapters.sentiment.placeholder import PlaceholderSentimentAdapter

_WINDOW = SentimentWindow(start="2026-06-01", end="2026-06-14")


def test_fetch_returns_aggregate_summary() -> None:
    """``fetch`` ⇒ frozen aggregate ``SentimentSummary``; placeholder source, deterministic."""
    adapter = PlaceholderSentimentAdapter()
    assert isinstance(adapter, SentimentAdapter)

    summary = adapter.fetch(_WINDOW)

    assert isinstance(summary, SentimentSummary)
    # Aggregate-only, never a live feed (INV-6, OUT-5).
    assert summary.source_mode == "placeholder"
    assert summary.source_mode != "live_feed"

    # Non-negative bucket counts that sum to the reported total (a real aggregate).
    assert summary.positive >= 0
    assert summary.neutral >= 0
    assert summary.negative >= 0
    assert summary.total == summary.positive + summary.neutral + summary.negative
    assert summary.total > 0

    # Deterministic for a given window: same instance and a fresh one agree.
    assert adapter.fetch(_WINDOW) == summary
    assert PlaceholderSentimentAdapter().fetch(_WINDOW) == summary

    # Frozen — an aggregate summary is an immutable read, not a mutable record.
    with pytest.raises(ValidationError):
        summary.positive = 0  # type: ignore[misc]

    # Derivation, not a constant: a different window yields a different aggregate.
    other = adapter.fetch(SentimentWindow(start="2026-05-01", end="2026-05-31"))
    assert other != summary


def test_summary_has_no_per_person_field() -> None:
    """INV-6: the aggregate exposes no per-person / minor-keyed field — counts only."""
    summary = PlaceholderSentimentAdapter().fetch(_WINDOW)
    fields = set(summary.model_dump().keys())
    forbidden = {"family_id", "child_id", "person", "name", "email", "records", "items"}
    assert not (fields & forbidden), f"sentiment summary must be aggregate-only; got {fields}"


def test_placeholder_is_not_a_live_feed() -> None:
    """Structural INV-9: the module is a pure, offline source — no live feed.

    It imports no http client and no ``anthropic`` — there is no sentiment feed to
    poll, so "placeholder, not a live feed" is provable from the source text.
    """
    module = importlib.import_module("app.adapters.sentiment.placeholder")
    source = inspect.getsource(module)

    forbidden = (
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "anthropic",
        "random",
        "uuid4",
    )
    for token in forbidden:
        assert token not in source, f"placeholder sentiment adapter must not reference {token!r}"


def test_registry_returns_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 default ⇒ placeholder impl; a future live mode fails loud (no live feed)."""
    monkeypatch.setenv("SEND_MODE", "simulate")
    adapter = get_sentiment_adapter()
    assert isinstance(adapter, PlaceholderSentimentAdapter)
    assert isinstance(adapter, SentimentAdapter)

    monkeypatch.setenv("SEND_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_sentiment_adapter()

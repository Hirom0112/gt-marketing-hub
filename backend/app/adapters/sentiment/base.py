"""The sentiment-feed boundary — interface + window/summary models (ARCH §7.5).

§7.5 (authoritative):

    interface SentimentAdapter:
      fetch(window) -> SentimentSummary   # aggregate only, no minor targeting

"v1: placeholder over synthetic — returns an aggregate summary, ``source_mode``
never ``live_feed``." The summary is **aggregate only** — counts/share by
positive/neutral/negative — with **no per-person or child-keyed field** (P-4,
INV-6: no minor-keyed targeting or scraping).

INV-9: like every external boundary, this is an interface with two impls —
Placeholder (v1) and Production (go-live) — selected by config in
:mod:`app.adapters.registry`. A live sentiment feed is OUT in v1 (PROJECT §7,
OUT-5); the placeholder impl is a pure, offline source over synthetic data with
no network client at all, so "no live feed" is a structural property. This module
imports nothing from ``anthropic`` and keeps ``core/`` untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

SourceMode = Literal["placeholder", "live_feed"]


class SentimentWindow(BaseModel):
    """The time window to summarise sentiment over (§7.5).

    A small typed input: an inclusive ``start``/``end`` (opaque date strings).
    Frozen — a window is an immutable request, not mutable state.

    Attributes:
        start: Inclusive window start (opaque date string).
        end: Inclusive window end (opaque date string).
    """

    model_config = ConfigDict(frozen=True)

    start: str
    end: str


class SentimentSummary(BaseModel):
    """An aggregate sentiment summary for a window (§7.5, INV-6).

    **Aggregate only** — bucket counts by positive/neutral/negative plus the
    total; there is deliberately **no per-person or child-keyed field** (INV-6:
    no minor targeting). ``source_mode`` is ``placeholder`` in v1 and is **never**
    ``live_feed``. Frozen — a summary is an immutable read.

    Attributes:
        positive: Count of positive mentions in the window (aggregate).
        neutral: Count of neutral mentions in the window (aggregate).
        negative: Count of negative mentions in the window (aggregate).
        total: Total mentions (= positive + neutral + negative).
        source_mode: ``placeholder`` in v1; never ``live_feed`` (OUT-5, INV-6).
    """

    model_config = ConfigDict(frozen=True)

    positive: int
    neutral: int
    negative: int
    total: int
    source_mode: SourceMode = "placeholder"


class SentimentAdapter(ABC):
    """The sentiment-feed external boundary (§7.5).

    Two impls — Placeholder (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. The marketing/KPI layer depends only on this
    interface, never on a concrete feed client, and only ever sees aggregates.
    """

    @abstractmethod
    def fetch(self, window: SentimentWindow) -> SentimentSummary:
        """Return an aggregate sentiment summary for ``window`` (§7.5, INV-6)."""

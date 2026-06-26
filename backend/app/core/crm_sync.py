"""CRM-as-truth incremental-poll planner (A2; RESEARCH_v2 §II.1; CLAUDE.md §3).

The seam flip (A2) makes HubSpot the source of truth for human/pipeline-edited
fields, pulled via HubSpot's CRM Search **incremental poll** keyed on
``hs_lastmodifieddate``. RESEARCH_v2 §II.1 (verbatim):

    ``POST /crm/v3/objects/{object}/search`` with filter
    ``hs_lastmodifieddate GT <watermark-epoch-ms>``,
    ``sorts:[{hs_lastmodifieddate, ASCENDING}]`` (max 1 sort), page via
    ``paging.next.after``; page max 200, 10,000-result cap per query.

This module owns the two PURE responsibilities of that poll — it imports only the
stdlib, never an adapter / httpx / repository (the core-purity test guards this),
and it NEVER calls ``datetime.now()`` (``now`` is injected by the caller):

1. **Watermark advance** (:func:`advance_watermark`) — given the current
   watermark (the last-synced ``hs_lastmodifieddate``, ``datetime | None``) and a
   pulled batch, the next watermark is the MAX ``hs_lastmodifieddate`` seen. It
   never moves backward, and an empty batch leaves it unchanged.
2. **Time-window chunking past the 10k cap** (:func:`plan_sync_windows`) — because
   one CRM Search query is capped at 10,000 results, a wide [watermark, now]
   window is split into sequential, contiguous, non-overlapping sub-windows of
   ``chunk_days`` each so no single query risks the cap.

``chunk_days`` (and the 200 page / 10k cap) are tunables — they live in the
``crm_sync`` params block (INV-11), read by the caller and passed in here; this
core hardcodes none of them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class PulledRecord:
    """One record pulled from a CRM Search page, carrying its version stamp.

    Only the ``hs_lastmodifieddate`` matters to the planner: it is HubSpot's
    last-modified timestamp (RESEARCH_v2 §II.1's version stamp) and the sole input
    to the watermark advance. The rest of a pulled record's payload is the
    reconcile layer's concern, not this pure planner's.
    """

    hs_lastmodifieddate: datetime


@dataclass(frozen=True, slots=True)
class SyncWindow:
    """A half-open-in-spirit [start, end] poll sub-window (start < end).

    The planner emits an ordered, contiguous, non-overlapping list of these
    covering exactly [watermark, now]; each is narrow enough (``<= chunk_days``)
    that a single CRM Search over it cannot hit the 10,000-result cap.
    """

    start: datetime
    end: datetime


def advance_watermark(current: datetime | None, records: Iterable[PulledRecord]) -> datetime | None:
    """Compute the next sync watermark from a pulled batch (RESEARCH_v2 §II.1).

    The next watermark is the MAX ``hs_lastmodifieddate`` in the batch, never
    moving backward: a batch wholly older than ``current`` leaves it unchanged,
    and an empty batch is a no-op. A ``None`` ``current`` (first-ever sync)
    advances straight to the batch max (or stays ``None`` if the batch is empty).

    Args:
        current: The last-synced ``hs_lastmodifieddate``, or ``None`` on the
            first sync.
        records: The records pulled this poll, each carrying its
            ``hs_lastmodifieddate``.

    Returns:
        The new watermark — ``max(current, max(batch))``, with ``None`` handled
        as "no lower bound yet".
    """
    seen = [record.hs_lastmodifieddate for record in records]
    if not seen:
        return current
    batch_max = max(seen)
    if current is None:
        return batch_max
    return max(current, batch_max)


def plan_sync_windows(start: datetime, now: datetime, chunk_days: int) -> list[SyncWindow]:
    """Split [start, now] into ``chunk_days`` sub-windows under the 10k cap (§II.1).

    Produces an ordered list of contiguous, non-overlapping :class:`SyncWindow`
    whose union is exactly [start, now]. There are ``ceil(span / chunk_days)``
    windows; every window but the last spans a full ``chunk_days``, and the last
    is clamped to ``now`` (so a span narrower than ``chunk_days`` yields exactly
    one window [start, now]). Each window is then narrow enough that a single CRM
    Search over it cannot risk the 10,000-result cap.

    Pure and ``now``-injected (CLAUDE.md §3): the caller supplies ``now``; this
    function never reads the clock.

    Args:
        start: The lower bound — the current sync watermark.
        now: The injected upper bound (the caller's "now").
        chunk_days: The per-window span in whole days (the ``crm_sync.chunk_days``
            tunable, INV-11). Must be ``>= 1``.

    Returns:
        The ordered list of sub-windows, or ``[]`` when ``now <= start`` (nothing
        new to sync).

    Raises:
        ValueError: if ``chunk_days < 1``.
    """
    if chunk_days < 1:
        raise ValueError(f"chunk_days must be >= 1, got {chunk_days!r}")
    if now <= start:
        return []

    chunk = timedelta(days=chunk_days)
    span = now - start
    window_count = math.ceil(span / chunk)

    windows: list[SyncWindow] = []
    cursor = start
    for index in range(1, window_count + 1):
        end = min(start + chunk * index, now)
        windows.append(SyncWindow(start=cursor, end=end))
        cursor = end
    return windows

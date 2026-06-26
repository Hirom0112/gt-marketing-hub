"""crm_sync incremental-poll planner tests (A2; RESEARCH_v2 §II.1; INV-11).

The CRM-as-truth seam pulls HubSpot changes via the CRM Search incremental poll
keyed on ``hs_lastmodifieddate`` (RESEARCH_v2 §II.1: page max 200, 10,000-result
cap per query). ``app/core/crm_sync.py`` owns the two PURE responsibilities of
that poll — no I/O, no adapter:

1. **Watermark advance** — the next watermark is the MAX ``hs_lastmodifieddate``
   seen in a pulled batch; it never moves backward, and an empty batch leaves it
   unchanged.
2. **Time-window chunking past the 10k cap** — the [watermark, now] window is
   split into sequential, contiguous, non-overlapping sub-windows of ``chunk_days``
   each so no single CRM Search query risks the 10,000-result cap.

The ``chunk_days`` (and the 200 page / 10k cap) live in the ``crm_sync`` params
block (INV-11); this test reads them via ``load_params`` so a param drift fails
the build (CLAUDE.md §4.1). ``now`` is INJECTED — the pure core never calls
``datetime.now()``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.crm_sync import (
    PulledRecord,
    SyncWindow,
    advance_watermark,
    plan_sync_windows,
)
from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_watermark_advances_and_chunks_past_cap() -> None:
    """Watermark = max(hs_lastmodifieddate); wide window chunks by params chunk_days."""
    crm_sync = load_params(EXAMPLE_PARAMS).crm_sync
    chunk_days = crm_sync.chunk_days

    # RESEARCH_v2 §II.1 grounded facts pinned to params (INV-11; drift fails build).
    assert crm_sync.result_cap == 10000  # 10,000-result cap per query
    assert chunk_days >= 1
    assert crm_sync.search_qps >= 1

    # (a) Next watermark = MAX hs_lastmodifieddate over the pulled batch.
    watermark = datetime(2026, 1, 1, tzinfo=UTC)
    batch = [
        PulledRecord(hs_lastmodifieddate=datetime(2026, 1, 3, tzinfo=UTC)),
        PulledRecord(hs_lastmodifieddate=datetime(2026, 1, 9, tzinfo=UTC)),  # max
        PulledRecord(hs_lastmodifieddate=datetime(2026, 1, 5, tzinfo=UTC)),
    ]
    assert advance_watermark(watermark, batch) == datetime(2026, 1, 9, tzinfo=UTC)

    # First-ever sync: a None watermark advances to the batch max.
    assert advance_watermark(None, batch) == datetime(2026, 1, 9, tzinfo=UTC)

    # Never moves backward: a batch wholly older than the watermark is ignored.
    older = [PulledRecord(hs_lastmodifieddate=datetime(2025, 12, 1, tzinfo=UTC))]
    assert advance_watermark(watermark, older) == watermark

    # (b) Empty batch leaves the watermark unchanged.
    assert advance_watermark(watermark, []) == watermark
    assert advance_watermark(None, []) is None

    # (c) A wide [watermark, now] window splits into ceil(span/chunk_days) ordered,
    #     contiguous, non-overlapping sub-windows whose union == [watermark, now].
    start = datetime(2026, 1, 1, tzinfo=UTC)
    span_days = chunk_days * 3 + 7  # deliberately not a whole multiple of chunk_days
    now = start + timedelta(days=span_days)
    windows = plan_sync_windows(start, now, chunk_days)

    expected_count = math.ceil(span_days / chunk_days)
    assert len(windows) == expected_count
    assert all(isinstance(w, SyncWindow) for w in windows)

    # Ordered + contiguous + non-overlapping: each window starts where the prior ended.
    assert windows[0].start == start
    assert windows[-1].end == now
    for w in windows:
        assert w.start < w.end  # every window is forward in time
    for prev, nxt in zip(windows[:-1], windows[1:], strict=True):
        assert prev.end == nxt.start  # contiguous, no gap, no overlap

    # No single sub-window exceeds chunk_days (so no query risks the 10k cap).
    chunk = timedelta(days=chunk_days)
    assert all(w.end - w.start <= chunk for w in windows)

    # Union of the sub-windows covers exactly [watermark, now].
    covered = sum((w.end - w.start for w in windows), timedelta())
    assert covered == now - start

    # (d) A window narrower than chunk_days yields exactly one window == [start, now].
    narrow_now = start + timedelta(days=1)  # chunk_days defaults well above 1 day
    narrow = plan_sync_windows(start, narrow_now, chunk_days)
    assert narrow == [SyncWindow(start=start, end=narrow_now)]

    # Degenerate: now at/​before the watermark ⇒ nothing to sync.
    assert plan_sync_windows(start, start, chunk_days) == []

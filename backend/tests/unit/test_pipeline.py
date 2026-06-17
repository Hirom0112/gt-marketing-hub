"""S1 core pipeline-counts tests (ARCHITECTURE.md §5.1, §4.1; FR-2.1).

`GET /pipeline` returns the per-stage funnel tally. The *counting contract* is a
pure core function — `core.pipeline.pipeline_counts(families)` — that the
repository delegates to (no duplicated logic). These tests pin that contract:

- every §4.8 `Stage` (`interest`|`apply`|`enroll`|`tuition`) is present,
  zero-filled, so the dashboard always renders all four;
- the counts **sum to the family total** (a partition of the input);
- against a fixed-seed fixture set the tallies match an independent expectation,
  so the count is exact and reproducible (CLAUDE.md §4.1).

Pure unit: no I/O, no adapters, no LLM — only the models + the counter.
"""

from __future__ import annotations

from collections import Counter

from app.core.pipeline import pipeline_counts
from app.data.models import Stage
from app.data.synthetic import generate

# A fixed seed + size so the tally is byte-reproducible (CLAUDE.md §4.1).
_SEED = 42
_N = 200


def test_per_stage_counts() -> None:
    """`pipeline_counts` partitions a fixed-seed fixture set across the four stages."""
    families = generate(n=_N, seed=_SEED).families
    counts = pipeline_counts(families)

    # Independent expectation drawn from the same fixed-seed dataset.
    expected = Counter(f.current_stage for f in families)

    # Every funnel stage is present (zero-filled) and matches exactly.
    assert set(counts) == set(Stage)
    for stage in Stage:
        assert counts[stage] == expected.get(stage, 0), stage

    # The per-stage counts sum to the family total (a true partition).
    assert sum(counts.values()) == _N


def test_empty_input_zero_fills_every_stage() -> None:
    """An empty family list still returns all four stages at zero."""
    counts = pipeline_counts([])
    assert counts == dict.fromkeys(Stage, 0)

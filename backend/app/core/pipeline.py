"""Core pipeline-counts — the deterministic per-stage funnel tally (§5.1, FR-2.1).

`GET /pipeline` shows how many families sit at each §4.8 funnel stage. The
*counting contract* lives here, in the pure core, as a single function over a
list of spine rows: the repository (the store seam, NFR-8) delegates to it so
the count is defined in exactly one place — a SQL-backed store maps the same
contract to a ``GROUP BY current_stage`` without re-deriving anything.

Purity (CLAUDE.md §3, INV-2): a deterministic function of its input alone — no
I/O, no LLM, no adapters. It imports only the pure data models, so it sits above
the store and below the API untouched by either.
"""

from __future__ import annotations

from app.data.models import FamilyRecord, Stage


def pipeline_counts(families: list[FamilyRecord]) -> dict[Stage, int]:
    """Tally families per ``current_stage``, zero-filling every §4.8 stage.

    A true partition of the input: every family lands in exactly one stage, so
    the returned counts sum to ``len(families)``. Every :class:`Stage` is present
    even at zero, so the dashboard always renders all four funnel columns (FR-2.1).

    Args:
        families: The spine rows to tally (the full set, or a filtered slice).

    Returns:
        A ``{Stage: count}`` mapping over all four stages.
    """
    counts: dict[Stage, int] = dict.fromkeys(Stage, 0)
    for family in families:
        counts[family.current_stage] += 1
    return counts

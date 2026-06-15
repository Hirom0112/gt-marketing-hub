"""GEO competitor-set validation — CONTENT_SPEC §7.3 (LOCKED), INV-6.

§7.3 locks the GEO `competitorSet` to the **gifted-school domain set** — the
schools GT School actually competes with for profoundly-gifted families. It is
NEVER an auto-picked set of test-prep brands (Kaplan, Princeton Review, …): a
GEO piece whose competitor set includes test-prep brands is **content-invalid**
for this category. This is the INV-6 posture in the GEO surface — the category
is defined deliberately, not scraped.

Pure logic per CLAUDE.md §3: no I/O, no `anthropic` / `langgraph` import — a
single deterministic predicate over the LOCKED universe.
"""

from __future__ import annotations

from collections.abc import Sequence

# The LOCKED gifted-school competitor universe (§7.3). The allowed set for a
# GEO `competitorSet` — exactly these domains, never auto-picked test-prep brands.
GIFTED_SCHOOL_COMPETITOR_SET: tuple[str, ...] = (
    "joinprisma.com",
    "fusionacademy.com",
    "davidsononline.org",
    "k12.com",
    "niche.com",
)


def validate_competitor_set(competitor_set: Sequence[str]) -> bool:
    """True iff `competitor_set` is a non-empty subset of the gifted-school set.

    §7.3 (LOCKED): the gifted-school domain set is the allowed universe. A set
    that is empty, or that contains ANY domain outside the universe (a test-prep
    brand such as ``kaplan.com`` / ``princetonreview.com``), is **content-invalid**
    for this category — the caller must BLOCK it (INV-6). Pure: no I/O.

    Args:
        competitor_set: the GEO piece's `competitorSet` domains.

    Returns:
        ``True`` only when the set is non-empty and every domain is in the LOCKED
        gifted-school universe; ``False`` otherwise.
    """
    if not competitor_set:
        return False
    allowed = set(GIFTED_SCHOOL_COMPETITOR_SET)
    return all(domain in allowed for domain in competitor_set)

"""The app-layer sales-agent registry — the rank→agent static lookup (M1).

This module MIRRORS the deterministic demo seed in
``supabase/migrations/0013_sales_agents.sql``: the SQL seed is authoritative for
the cloud DB; this is the app-layer / in-memory + demo-principal lookup. The two
MUST agree — the agent uuids, ranks, synthetic names, and tiers here are the same
stable per-rank literals the migration inserts (rank→agent survives re-seeding).

Purity: no I/O, no DB, no ``service_role`` — a frozen in-process table the demo
principal resolves against (MULTI_AGENT_COCKPIT §4). Tier is stored per agent
(``closer`` for rank 1, ``setter`` for rank 2), consistent with
``params.assignment.closer_rank_max`` (= 1 in the demo): rank ≤ ``closer_rank_max``
⇒ the closer tier. The tier is materialized here from that rule (the param is the
single canonical home for the cutoff; INV-11) so the registry agrees with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

# The two canonical demo agents — STABLE per-rank uuid literals, identical to the
# 0013_sales_agents.sql seed (NOT random, so rank→agent is a static lookup). The
# demo seeds exactly 2; the registry shape supports any number.
_AGENT_1_ID = UUID("a0000000-0000-4000-8000-000000000001")  # rank 1
_AGENT_2_ID = UUID("a0000000-0000-4000-8000-000000000002")  # rank 2

# The closer/setter cutoff. The canonical home for this value is
# ``params.assignment.closer_rank_max`` (= 1; INV-11). It is mirrored here ONLY to
# materialize each agent's stored ``tier`` at import (the registry is a pure
# constant); the demo principal resolves tier off the stored field, never by
# recomputing. Both the SQL seed and this table encode the same demo cutoff (1).
_DEMO_CLOSER_RANK_MAX = 1


@dataclass(frozen=True)
class SalesAgent:
    """One demo sales agent — the app-layer mirror of a ``sales_agent`` row.

    ``agent_id`` is the stable per-rank uuid (the demo principal's ``agent_id`` and
    the ``family_record.assigned_rep_id`` FK target). ``tier`` is ``closer`` (rank
    ≤ ``closer_rank_max``) or ``setter`` — already materialized from the param
    cutoff so the lookup is a field read, not a recomputation.
    """

    agent_id: UUID
    rank: int
    synthetic_name: str
    tier: str  # 'closer' | 'setter'


def _tier_for_rank(rank: int) -> str:
    """The demo tier for a rank: closer iff rank ≤ the demo closer_rank_max (= 1)."""
    return "closer" if rank <= _DEMO_CLOSER_RANK_MAX else "setter"


# The frozen registry, in rank order. Synthetic names only (INV-1).
SALES_AGENTS: tuple[SalesAgent, ...] = (
    SalesAgent(
        agent_id=_AGENT_1_ID,
        rank=1,
        synthetic_name="Riley Carter",
        tier=_tier_for_rank(1),
    ),
    SalesAgent(
        agent_id=_AGENT_2_ID,
        rank=2,
        synthetic_name="Jordan Avery",
        tier=_tier_for_rank(2),
    ),
)

_BY_ID: dict[UUID, SalesAgent] = {a.agent_id: a for a in SALES_AGENTS}
_BY_RANK: dict[int, SalesAgent] = {a.rank: a for a in SALES_AGENTS}


def lookup(agent_id: UUID) -> SalesAgent | None:
    """Resolve an agent by its id, or ``None`` for an unknown id (the demo lookup)."""
    return _BY_ID.get(agent_id)


def by_rank(rank: int) -> SalesAgent | None:
    """Resolve an agent by rank, or ``None`` for an unknown rank (rank→agent)."""
    return _BY_RANK.get(rank)

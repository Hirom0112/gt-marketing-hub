"""Synthetic dual-source ambassador roster (Grassroots; INV-1 / INV-6 / INV-9).

The Grassroots reconciler (:mod:`app.core.ambassador_reconcile`) resolves the
HubSpot ambassador-tracking property against the ``community.gt.school``
community export. ``community.gt.school`` is a **stood-in** source — it is not a
live service this build can reach — so this module GENERATES both sides as
synthetic, deterministic fixtures that overlap realistically:

- most ambassadors appear in BOTH sources (matched),
- a few are HubSpot-only (tagged by a rep, not yet on the community roll),
- a few are community-only (active in the portal, not yet tracked in HubSpot),
- a couple are in both but with a CONFLICTING ``status`` — exercising the
  reconciler's conflict surfacing.

Deterministic (the roster is a fixed, hand-shaped fixture; the seed parameter
keeps the contract identical to the other generators even though no random draw
is needed for this small curated cohort). PII-safe (INV-1): synthetic names, all
emails in the ``@example.invalid`` sink, aggregate segment/region labels only —
ambassadors are adults and no child-keyed data ever appears (INV-6). The two
sources also carry simulated freshness metadata (minutes since each last synced)
so the reconcile API can report real source health instead of a hardcoded badge
(INV-9 — simulated, labeled, no live call).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ambassador_reconcile import AmbassadorRecord

# The default deterministic seed (kept for contract parity with the other
# generators; the curated roster below is fixed, so the value only documents
# intent — there is no random draw to vary).
DEFAULT_AMBASSADOR_SEED = 4207

# Simulated per-source freshness — how many minutes ago each source last synced.
# This is generated FIXTURE data (like a seeded timestamp), not a logic tunable:
# it feeds the API's source-health summary so the reconciled badge reflects real
# source state rather than a hardcoded "6m ago" (INV-9, simulated + labeled).
_HUBSPOT_SYNCED_MINUTES_AGO = 6
_COMMUNITY_SYNCED_MINUTES_AGO = 14


@dataclass(frozen=True, slots=True)
class AmbassadorSource:
    """One generated source feed: its rows + simulated freshness (INV-9)."""

    name: str
    rows: tuple[AmbassadorRecord, ...]
    synced_minutes_ago: int


@dataclass(frozen=True, slots=True)
class AmbassadorSources:
    """The two stood-in sources the reconciler consumes (synthetic; INV-1)."""

    hubspot: AmbassadorSource
    community: AmbassadorSource


# The canonical synthetic roster. Each entry is hand-placed into one of four
# overlap buckets so the reconciler exercises every path deterministically:
#   - ``both``           — identical in both sources (a clean match).
#   - ``hubspot_only``   — only in the HubSpot tracking property.
#   - ``community_only`` — only in the community.gt.school export.
#   - ``conflict``       — in both, but the community ``status`` differs (the
#                          ``status_community`` override) ⇒ a surfaced conflict.
# All names are synthetic adults; segment/region are aggregate labels (INV-6).
@dataclass(frozen=True, slots=True)
class _RosterEntry:
    name: str
    email: str
    segment: str
    region: str
    status: str
    intros: int
    p2p: int
    last_touch: str
    bucket: str
    status_community: str | None = None  # set only for a conflict entry


_ROSTER: tuple[_RosterEntry, ...] = (
    _RosterEntry(
        "Renata Fields",
        "fields.214@example.invalid",
        "Robotics parents",
        "Austin metro",
        "Champion",
        14,
        9,
        "2d",
        "both",
    ),
    _RosterEntry(
        "Marcus Bell",
        "bell.731@example.invalid",
        "Homeschool co-op",
        "Plano",
        "Active",
        8,
        6,
        "4d",
        "both",
    ),
    _RosterEntry(
        "Priya Nair",
        "nair.498@example.invalid",
        "Chess club",
        "Round Rock",
        "Active",
        6,
        4,
        "1d",
        "both",
    ),
    _RosterEntry(
        "Devon Carter",
        "carter.305@example.invalid",
        "Math circle",
        "Frisco",
        "Onboarded",
        2,
        1,
        "6d",
        "both",
    ),
    _RosterEntry(
        "Aisha Rahman",
        "rahman.872@example.invalid",
        "Parent group",
        "Houston",
        "Outreached",
        0,
        0,
        "9d",
        "both",
    ),
    _RosterEntry(
        "Leon Whitaker",
        "whitaker.640@example.invalid",
        "STEM meetup",
        "Houston",
        "Active",
        5,
        3,
        "3d",
        "hubspot_only",
    ),
    _RosterEntry(
        "Camila Ortiz",
        "ortiz.157@example.invalid",
        "Robotics parents",
        "San Antonio",
        "Onboarded",
        1,
        0,
        "7d",
        "hubspot_only",
    ),
    _RosterEntry(
        "Theo Nakamura",
        "nakamura.926@example.invalid",
        "Chess club",
        "DFW",
        "Active",
        4,
        2,
        "2d",
        "community_only",
    ),
    _RosterEntry(
        "Hana Park",
        "park.583@example.invalid",
        "Homeschool co-op",
        "Hill Country",
        "Outreached",
        0,
        0,
        "11d",
        "community_only",
    ),
    _RosterEntry(
        "Grace Liu",
        "liu.419@example.invalid",
        "Math circle",
        "Austin metro",
        "Champion",
        11,
        7,
        "1d",
        "conflict",
        status_community="Active",
    ),
    _RosterEntry(
        "Omar Haddad",
        "haddad.268@example.invalid",
        "Robotics parents",
        "Plano",
        "Active",
        7,
        5,
        "5d",
        "conflict",
        status_community="Onboarded",
    ),
)


def _record(entry: _RosterEntry, *, status: str) -> AmbassadorRecord:
    """Build an :class:`AmbassadorRecord` from a roster entry with a given status."""
    return AmbassadorRecord(
        synthetic_name=entry.name,
        synthetic_email=entry.email,
        segment=entry.segment,
        region=entry.region,
        status=status,
        intros=entry.intros,
        p2p=entry.p2p,
        last_touch=entry.last_touch,
    )


def generate_ambassador_sources(
    seed: int = DEFAULT_AMBASSADOR_SEED,
) -> AmbassadorSources:
    """Generate the two stood-in ambassador sources (deterministic; INV-1/INV-6).

    The HubSpot side carries every ``both`` / ``hubspot_only`` / ``conflict``
    entry (the conflict rows use their HubSpot ``status``); the community side
    carries every ``both`` / ``community_only`` / ``conflict`` entry (the
    conflict rows use the diverging ``status_community``). Running these through
    :func:`app.core.ambassador_reconcile.reconcile_ambassadors` yields a stable
    union of all 11 ambassadors with 7 matched (2 of them conflicting), 2
    HubSpot-only, and 2 community-only.

    ``seed`` is accepted for contract parity with the other generators; the
    curated roster is fixed, so the output is identical for any seed.

    Args:
        seed: Documented for parity; does not vary the curated roster.

    Returns:
        The :class:`AmbassadorSources` pair (each with simulated freshness).
    """
    _ = seed  # fixed roster — no random draw to seed (documented above)

    hubspot_rows: list[AmbassadorRecord] = []
    community_rows: list[AmbassadorRecord] = []
    for entry in _ROSTER:
        if entry.bucket in ("both", "hubspot_only", "conflict"):
            hubspot_rows.append(_record(entry, status=entry.status))
        if entry.bucket in ("both", "community_only", "conflict"):
            community_status = entry.status_community or entry.status
            community_rows.append(_record(entry, status=community_status))

    return AmbassadorSources(
        hubspot=AmbassadorSource(
            name="HubSpot",
            rows=tuple(hubspot_rows),
            synced_minutes_ago=_HUBSPOT_SYNCED_MINUTES_AGO,
        ),
        community=AmbassadorSource(
            name="community.gt.school",
            rows=tuple(community_rows),
            synced_minutes_ago=_COMMUNITY_SYNCED_MINUTES_AGO,
        ),
    )

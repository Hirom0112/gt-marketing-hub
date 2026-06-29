"""In-process TTL + single-flight cache for the LIVE CRM-Ops snapshots (perf).

Why this exists. Every CRM-Ops page load fires BOTH the global data-confidence
banner (``GET /crm/status``) and the page's own endpoint, and several of those
endpoints recompute the same LIVE Supabase⇄HubSpot snapshot — the per-family
:meth:`read_mirror` parity scan plus the aggregate lead-score / last-modified
reads — against the live portal (``CRM_MODE=live``). Concurrent + repeated loads
stormed the HubSpot rate limit, and whichever call lost degraded to the SEED
fallback (a stale parity flicker). This memoizes each expensive LIVE snapshot per
program for a short params TTL, with single-flight so concurrent cache-miss
callers compute it AT MOST ONCE and share the one result.

Honesty (unchanged). A cached LIVE read is STILL live — the cache only avoids
recomputation within the TTL; it never changes the computed values or any
``live``/``seed``/``synthetic`` source label. The pure cores stay clock-free; the
monotonic clock lives HERE at the API layer (and is injectable, so tests drive TTL
expiry deterministically without sleeping).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.core.parity import ParityScore, compute_parity
from app.core.program import Program
from app.core.seam import MirrorState
from app.data.models import FamilyRecord
from app.data.repository import FamilyRepository

# Every cache registers here so :func:`reset_crm_ops_cache` can clear them all at
# once — the test-isolation seam, mirroring ``deps.reset_crm_adapter``.
_REGISTRY: list[TtlSingleFlightCache[Any]] = []


@dataclass(slots=True)
class _Entry[T]:
    """One cached value + the monotonic instant it expires at."""

    value: T
    expires_at: float


class TtlSingleFlightCache[T]:
    """A per-:class:`Program` memo of one expensive value — TTL + single-flight.

    On a HIT within the entry's TTL the stored value is returned WITHOUT calling
    ``compute`` (so no adapter call, no live read). On a MISS the single
    :class:`threading.Lock` makes the first caller compute while concurrent callers
    wait and then reuse the freshly-stored value — never an N-way recompute storm
    (FastAPI sync endpoints run in a threadpool, so real concurrency is possible).
    The lock is held across ``compute`` deliberately: serializing the (rare,
    once-per-TTL) recompute is the whole point — it collapses the storm into one
    live call.

    ``clock`` is injected (defaults to :func:`time.monotonic`) so tests drive TTL
    expiry deterministically without sleeping or touching the wall clock.
    ``register=False`` keeps a throwaway test instance out of the global reset set.
    """

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, register: bool = True
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Program, _Entry[T]] = {}
        if register:
            _REGISTRY.append(self)

    def get_or_compute(
        self, program: Program, compute: Callable[[], T], *, ttl_seconds: float
    ) -> T:
        """Return ``program``'s cached value, or compute+store it (single-flight)."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(program)
            if entry is not None and entry.expires_at > now:
                return entry.value
            value = compute()
            self._entries[program] = _Entry(value=value, expires_at=now + ttl_seconds)
            return value

    def reset(self) -> None:
        """Drop every cached entry (test isolation; never needed in production)."""
        with self._lock:
            self._entries.clear()


def reset_crm_ops_cache() -> None:
    """Clear EVERY registered CRM-Ops snapshot cache (mirrors ``deps.reset_crm_adapter``)."""
    for cache in _REGISTRY:
        cache.reset()


@dataclass(frozen=True, slots=True)
class ParitySnapshot:
    """One program's cached LIVE parity scan — the (record, mirror) pairs + score.

    ``pairs`` is the SAME A4 ``(FamilyRecord, MirrorState)`` cohort the §4.7 seam
    endpoints pair (``list_families`` + ``read_mirror``); ``parity`` is
    :func:`app.core.parity.compute_parity` over it. Both ``GET /crm/status`` (the
    banner) and the CRM-Ops parity views read this one shared snapshot instead of
    each re-scanning the live mirror.
    """

    pairs: tuple[tuple[FamilyRecord, MirrorState], ...]
    parity: ParityScore


# The shared LIVE parity-scan cache — the per-family read_mirror storm's one home.
_PARITY_CACHE: TtlSingleFlightCache[ParitySnapshot] = TtlSingleFlightCache()


def parity_snapshot(
    repository: FamilyRepository,
    crm_adapter: CRMAdapter,
    *,
    program: Program,
    ttl_seconds: float,
) -> ParitySnapshot:
    """The program's LIVE parity snapshot, computed at most once per TTL (single-flight).

    The expensive part — one :meth:`read_mirror` live call per family — runs only
    on a cache miss; a hit returns the stored snapshot with NO adapter call. The
    result is shared by ``GET /crm/status`` and the CRM-Ops parity / overview /
    sync-parity views (and the scan), so a banner + page load is ONE live scan, not
    several. The cohort is program-keyed so one program's data never serves another.
    """

    def compute() -> ParitySnapshot:
        pairs = tuple(
            (record, crm_adapter.read_mirror(record.family_id))
            for record in repository.list_families()
        )
        return ParitySnapshot(pairs=pairs, parity=compute_parity(pairs))

    return _PARITY_CACHE.get_or_compute(program, compute, ttl_seconds=ttl_seconds)

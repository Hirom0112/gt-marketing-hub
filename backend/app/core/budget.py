"""Pure marketing-budget reconcile + variance flag (TODO_v2 §B4; INV-11/INV-2).

The deterministic core for the $365K marketing budget: given the actual spend
against each workstream's planned spend, compute the per-workstream variance
``(actual - planned) / planned`` and flag an OVERRUN past the params variance
threshold. An overrun strictly ABOVE ``budget.variance_threshold`` flags; at or
under the threshold (including any under-budget, negative variance) does not.

This is the deterministic, *pure* core (mirrors :mod:`app.core.parity`): a
function of the entries + params alone — no repository, adapter, decision-queue,
or httpx import (the core-purity test guards this). The variance → decision
WIRING (turning a flagged workstream into a queued decision) is a LATER unit;
this module only computes ``flagged`` and the roll-up.

Money is cent-precise :class:`~decimal.Decimal` (matching the TEFA money
discipline in :mod:`app.core.funding_gate`); the variance is the exact Decimal
ratio so the at-threshold comparison has no float drift. The threshold is read
from ``params.budget.variance_threshold`` (INV-11) — never a 0.10 literal here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.params import Params

# A money value the entry constructor accepts: a whole-dollar int or a Decimal.
Money = int | Decimal

# Days per ISO week — a Gregorian-calendar constant (the burn series buckets by ISO
# week), not a tunable, so it is not a param (mirrors weekly_scorecard._DAYS_PER_WEEK).
_DAYS_PER_WEEK = 7

# The three per-workstream health bands (Module 10b indicator). Named wire tokens, not
# tunables (INV-11 carve-out) — the BANDS' thresholds are the params (variance_threshold
# + watch_frac); these are just the labels.
HEALTH_ON_TRACK = "on_track"
HEALTH_WATCH = "watch"
HEALTH_AT_RISK = "at_risk"


def _health(
    planned: Decimal, actual: Decimal, variance: Decimal, *, threshold: Decimal, watch_frac: Decimal
) -> str:
    """Classify one workstream into on_track / watch / at_risk from the params bands.

    ``at_risk`` when the OVERRUN is past the variance threshold OR the workstream is
    over budget (``actual > planned``); ``watch`` when ``actual >= watch_frac *
    planned`` (approaching, but not yet over); else ``on_track``. The two band dials
    are params (``budget.variance_threshold`` / ``budget.watch_frac``) — never a code
    literal (INV-11).
    """
    if variance > threshold or actual > planned:
        return HEALTH_AT_RISK
    if actual >= watch_frac * planned:
        return HEALTH_WATCH
    return HEALTH_ON_TRACK


def _to_decimal(value: Money) -> Decimal:
    """Normalize an int/Decimal money amount to a Decimal (never via float)."""
    return value if isinstance(value, Decimal) else Decimal(value)


@dataclass(frozen=True, slots=True)
class BudgetEntry:
    """One workstream's planned vs actual spend (frozen input row).

    ``planned``/``actual`` accept a whole-dollar ``int`` or a ``Decimal``; both
    are normalized to cent-capable ``Decimal`` so the reconciler never mixes
    float money. ``committed`` is an optional already-committed-but-not-yet-spent
    amount carried for extensibility (the PLAN's recommended/planned/committed/
    actual/remaining shape) — it does not affect the variance flag.

    Attributes:
        workstream: The workstream token (e.g. ``grassroots`` / ``content``).
        planned: The planned spend for this workstream.
        actual: The actual spend to date.
        committed: Optional committed-but-unspent amount (default ``0``).
    """

    workstream: str
    planned: Decimal
    actual: Decimal
    committed: Decimal = Decimal("0")

    def __init__(
        self,
        workstream: str,
        planned: Money,
        actual: Money,
        committed: Money = 0,
    ) -> None:
        # Normalize money inputs to Decimal on the frozen dataclass.
        object.__setattr__(self, "workstream", workstream)
        object.__setattr__(self, "planned", _to_decimal(planned))
        object.__setattr__(self, "actual", _to_decimal(actual))
        object.__setattr__(self, "committed", _to_decimal(committed))


@dataclass(frozen=True, slots=True)
class BudgetReconcileResult:
    """A single workstream's reconciled variance (frozen output row).

    Attributes:
        workstream: The workstream token.
        planned: Planned spend (Decimal).
        actual: Actual spend (Decimal).
        remaining: ``planned - actual`` (positive = under budget).
        variance: ``(actual - planned) / planned`` as an exact Decimal ratio.
        flagged: ``variance > params.budget.variance_threshold`` — an overrun
            strictly past the threshold flags; at/under does not.
        health: The Module-10b band — ``on_track`` / ``watch`` / ``at_risk`` from the
            params bands (``variance_threshold`` + ``watch_frac``).
    """

    workstream: str
    planned: Decimal
    actual: Decimal
    remaining: Decimal
    variance: Decimal
    flagged: bool
    health: str


@dataclass(frozen=True, slots=True)
class BudgetReconciliation:
    """A cohort's budget reconciliation — per-workstream rows + the roll-up.

    Attributes:
        results: The per-workstream :class:`BudgetReconcileResult` rows, in input
            order.
        flagged: The tuple of flagged workstream tokens (overruns past threshold),
            in input order.
        total_planned: Sum of planned across the entries.
        total_actual: Sum of actual across the entries.
        total_remaining: ``total_planned - total_actual``.
        total_usd: The whole-budget figure from ``params.budget.total_usd`` (the
            full plan; the entries may cover only a subset of it).
    """

    results: tuple[BudgetReconcileResult, ...]
    flagged: tuple[str, ...]
    total_planned: Decimal
    total_actual: Decimal
    total_remaining: Decimal
    total_usd: int


def reconcile(entries: Iterable[BudgetEntry], *, params: Params) -> BudgetReconciliation:
    """Reconcile per-workstream actual-vs-planned spend into variances + flags.

    For each entry the variance is the exact Decimal ratio
    ``(actual - planned) / planned`` and ``flagged`` is
    ``variance > params.budget.variance_threshold`` — an OVERRUN strictly past
    the threshold flags; at or under it (including under-budget) does not. The
    threshold is read from params (INV-11), never hardcoded.

    Args:
        entries: The per-workstream :class:`BudgetEntry` rows. Consumed once
            (materialized internally), so a one-shot iterator is fine.
        params: Loaded params; supplies ``budget.variance_threshold`` and
            ``budget.total_usd``.

    Returns:
        The :class:`BudgetReconciliation` — per-workstream results, the flagged
        workstream tuple, and the roll-up vs ``budget.total_usd``.

    Raises:
        ValueError: if an entry's ``planned`` is zero (variance is undefined —
            fail loud rather than divide by zero).
    """
    threshold = Decimal(str(params.budget.variance_threshold))
    watch_frac = Decimal(str(params.budget.watch_frac))

    results: list[BudgetReconcileResult] = []
    flagged: list[str] = []
    total_planned = Decimal("0")
    total_actual = Decimal("0")
    for entry in entries:
        if entry.planned == 0:
            raise ValueError(
                f"budget reconcile: workstream {entry.workstream!r} has planned=0; "
                "variance is undefined"
            )
        variance = (entry.actual - entry.planned) / entry.planned
        is_flagged = variance > threshold
        results.append(
            BudgetReconcileResult(
                workstream=entry.workstream,
                planned=entry.planned,
                actual=entry.actual,
                remaining=entry.planned - entry.actual,
                variance=variance,
                flagged=is_flagged,
                health=_health(
                    entry.planned,
                    entry.actual,
                    variance,
                    threshold=threshold,
                    watch_frac=watch_frac,
                ),
            )
        )
        if is_flagged:
            flagged.append(entry.workstream)
        total_planned += entry.planned
        total_actual += entry.actual

    return BudgetReconciliation(
        results=tuple(results),
        flagged=tuple(flagged),
        total_planned=total_planned,
        total_actual=total_actual,
        total_remaining=total_planned - total_actual,
        total_usd=params.budget.total_usd,
    )


# ===========================================================================
# Weekly cumulative burn time-series + projected burn-out (Module 10b). Pure +
# CLOCK-FREE: the reference date ``as_of`` is INJECTED by the API (mirrors
# weekly_scorecard's as_of-injection). Same inputs + as_of ⇒ same series.
# ===========================================================================


def _iso_week_start(d: date) -> date:
    """The Monday anchoring ``d``'s ISO week (the bucket key)."""
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True, slots=True)
class BurnWeek:
    """One ISO-week bucket of the cumulative burn series (frozen output row).

    Attributes:
        week_start: The Monday anchoring this ISO week.
        cumulative_actual: Cumulative ACTUAL spend through the end of this week.
        cumulative_planned: The straight plan line's cumulative value at this week —
            ``total_planned`` apportioned linearly across the period (see
            :func:`build_burn_series`), so the chart can compare burn vs an even
            pace.
    """

    week_start: date
    cumulative_actual: Decimal
    cumulative_planned: Decimal


@dataclass(frozen=True, slots=True)
class BurnSeries:
    """The whole weekly cumulative burn series + the injected reference date.

    Attributes:
        weeks: The per-ISO-week :class:`BurnWeek` buckets, oldest → newest (empty
            when there are no dated actual entries).
        as_of: The injected reference date the series buckets run through.
    """

    weeks: tuple[BurnWeek, ...]
    as_of: date


def build_burn_series(
    dated_actuals: Iterable[tuple[date, Money]],
    *,
    total_planned: Money,
    as_of: date,
) -> BurnSeries:
    """Bucket dated actual spend into a weekly CUMULATIVE burn series (Module 10b) — pure.

    From the earliest actual entry's ISO week through the ISO week of ``as_of``, one
    bucket per week. ``cumulative_actual`` is the running sum of every actual whose
    date falls on or before that week's end; ``cumulative_planned`` is a STRAIGHT
    PLAN LINE — ``total_planned`` apportioned linearly across the buckets, i.e.
    ``total_planned * (i + 1) / n`` for bucket ``i`` of ``n`` — so the chart shows
    burn against an even-pace baseline (documented approximation, not a per-week
    plan ledger).

    ``as_of`` is INJECTED (the core reads no clock). With no dated actuals the series
    is empty (no period to bucket).

    Args:
        dated_actuals: ``(date, amount)`` pairs for ACTUAL-kind ledger lines.
            Consumed once (materialized internally), so a one-shot iterator is fine.
        total_planned: The planned total the straight plan line rises to (whole
            dollars or Decimal).
        as_of: The injected reference date the buckets run through.

    Returns:
        The :class:`BurnSeries` the ``GET /budget`` ``burn_series`` field renders.
    """
    items = [(d, _to_decimal(amount)) for d, amount in dated_actuals]
    if not items:
        return BurnSeries(weeks=(), as_of=as_of)

    plan_total = _to_decimal(total_planned)
    first_week = min(_iso_week_start(d) for d, _ in items)
    last_week = _iso_week_start(as_of)
    if last_week < first_week:
        # An as_of before every entry — clamp the window to the first week so the
        # series is never empty when there IS spend (never a backwards range).
        last_week = first_week

    weeks: list[BurnWeek] = []
    week_starts: list[date] = []
    cursor = first_week
    while cursor <= last_week:
        week_starts.append(cursor)
        cursor = cursor + timedelta(days=_DAYS_PER_WEEK)
    n = len(week_starts)

    for i, week_start in enumerate(week_starts):
        week_end = week_start + timedelta(days=_DAYS_PER_WEEK - 1)
        cumulative_actual = sum((amount for d, amount in items if d <= week_end), Decimal("0"))
        # Straight plan line: total_planned apportioned linearly across the n buckets.
        cumulative_planned = plan_total * Decimal(i + 1) / Decimal(n)
        weeks.append(
            BurnWeek(
                week_start=week_start,
                cumulative_actual=cumulative_actual,
                cumulative_planned=cumulative_planned,
            )
        )
    return BurnSeries(weeks=tuple(weeks), as_of=as_of)


def weekly_burn_rate(series: BurnSeries) -> Decimal:
    """The average weekly ACTUAL burn rate over the series (the burn-out denominator).

    ``cumulative_actual`` at the final week divided by the number of weeks — the mean
    dollars-burned-per-week. ``0`` for an empty series (no weeks), so the caller never
    divides by zero downstream.
    """
    if not series.weeks:
        return Decimal("0")
    return series.weeks[-1].cumulative_actual / Decimal(len(series.weeks))


def project_burnout(total_remaining: Money, rate: Decimal, *, as_of: date) -> date | None:
    """Project the date the remaining budget burns out at the recent weekly rate — pure.

    ``as_of + (total_remaining / rate) weeks``. Returns ``None`` when the weekly burn
    ``rate`` is zero or negative (never a divide-by-zero — an idle budget has no
    burn-out date). A non-positive remaining (already over budget) projects ``as_of``
    itself (burned out as of now). ``as_of`` is INJECTED — the core reads no clock.
    """
    if rate <= 0:
        return None
    remaining = _to_decimal(total_remaining)
    if remaining <= 0:
        return as_of
    weeks_left = remaining / rate
    days_left = int((weeks_left * Decimal(_DAYS_PER_WEEK)).to_integral_value())
    return as_of + timedelta(days=days_left)

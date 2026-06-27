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
from decimal import Decimal

from app.core.params import Params

# A money value the entry constructor accepts: a whole-dollar int or a Decimal.
Money = int | Decimal


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
    """

    workstream: str
    planned: Decimal
    actual: Decimal
    remaining: Decimal
    variance: Decimal
    flagged: bool


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

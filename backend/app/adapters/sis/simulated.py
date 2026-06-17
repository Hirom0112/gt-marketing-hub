"""SimulatedSISAdapter — the v1 synthetic-roster SIS impl (INV-1, INV-9).

M5: the concrete :class:`~app.adapters.sis.base.EnrollmentSystemAdapter` the
registry selects for ``SIS_MODE=simulate`` (TECH_STACK §5). It reads a SYNTHETIC
roster — built from the synthetic cohort by
:func:`app.data.sis_roster.generate_sis_roster`, or any injected sequence of
:class:`~app.adapters.sis.base.RosterRecord` (a CSV would normalize to the same
shape) — and yields normalized ``RosterRecord``s, the only shape the reconcile
core consumes (INV-9). No live SIS, no real student PII (INV-1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Self

from app.adapters.sis.base import EnrollmentSystemAdapter, RosterRecord
from app.core.params import Params
from app.data.sis_roster import generate_sis_roster
from app.data.synthetic import SyntheticDataset, generate


class SimulatedSISAdapter(EnrollmentSystemAdapter):
    """Yields a fixed synthetic SIS roster (INV-9 boundary; INV-1 synthetic)."""

    def __init__(self, roster: Sequence[RosterRecord]) -> None:
        self._roster: tuple[RosterRecord, ...] = tuple(roster)

    @classmethod
    def from_cohort(cls, dataset: SyntheticDataset, *, seed: int, params: Params) -> Self:
        """Build the roster over an existing synthetic cohort (the demo path)."""
        return cls(generate_sis_roster(dataset, seed=seed, params=params))

    @classmethod
    def from_seed(cls, *, n: int, seed: int, params: Params) -> Self:
        """Generate the cohort + its roster from a single seed (registry path)."""
        return cls.from_cohort(generate(n=n, seed=seed), seed=seed, params=params)

    def fetch_roster(self) -> Iterable[RosterRecord]:
        """Yield the normalized synthetic roster (INV-1)."""
        return self._roster

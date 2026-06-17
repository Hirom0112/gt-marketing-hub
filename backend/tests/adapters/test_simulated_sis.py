"""M5 — SimulatedSISAdapter reads the synthetic roster (INV-1, INV-9).

TODO.md M5 item 2: the adapter implements the M0 ``EnrollmentSystemAdapter``
interface and yields ``RosterRecord``s built from the synthetic roster. The
reconcile core depends only on this boundary, never on which SIS is behind it.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.sis.base import EnrollmentSystemAdapter, RosterRecord
from app.adapters.sis.simulated import SimulatedSISAdapter
from app.core.params import load_params
from app.data.sis_roster import generate_sis_roster
from app.data.synthetic import generate

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_reads_roster() -> None:
    params = load_params(EXAMPLE_PARAMS)
    ds = generate(60, seed=7)
    roster = generate_sis_roster(ds, seed=7, params=params)

    adapter = SimulatedSISAdapter(roster)

    # Implements the M0 interface (INV-9).
    assert isinstance(adapter, EnrollmentSystemAdapter)

    out = list(adapter.fetch_roster())
    assert out == roster
    assert out, "the synthetic roster must be non-empty"
    assert all(isinstance(r, RosterRecord) for r in out)


def test_from_cohort_equals_generator() -> None:
    """The ``from_cohort`` factory yields exactly the generator's roster."""
    params = load_params(EXAMPLE_PARAMS)
    ds = generate(60, seed=7)

    adapter = SimulatedSISAdapter.from_cohort(ds, seed=7, params=params)

    assert list(adapter.fetch_roster()) == generate_sis_roster(ds, seed=7, params=params)

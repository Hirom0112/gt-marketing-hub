"""Budget store tests (Module 10) — created_at exposure, planned edit, seed ledger.

The in-memory :class:`InMemoryBudgetStore` is the CI-tested path; these cover the
Module-10 additions:

- ``Entry.created_at`` is exposed and stamped on append (powers the burn series).
- ``set_planned`` mutates the workstream allocation (planned lives on the MUTABLE
  ``budget_workstream`` row, not the append-only ledger).
- ``seed_demo_ledger`` lays down a deterministic dated committed+actual ledger and is
  idempotent (re-seeding the same program adds nothing — INV-1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.api import deps
from app.core.program import Program
from app.data.budget_store import InMemoryBudgetStore

_PROGRAM = Program.FALL_ENROLLMENT


@pytest.fixture
def store() -> InMemoryBudgetStore:
    """A fresh params-seeded (workstreams-only, no ledger) in-memory store per test."""
    return InMemoryBudgetStore(params=deps._params)


def test_add_entry_stamps_created_at(store: InMemoryBudgetStore) -> None:
    """An appended entry exposes a created_at instant (stamped to now when omitted)."""
    entry = store.add_entry(
        _PROGRAM, workstream="content", kind="actual", amount_usd=Decimal("1000")
    )
    assert entry.created_at is not None
    listed = store.list_entries(_PROGRAM)
    assert listed[0].created_at is not None


def test_set_planned_updates_allocation(store: InMemoryBudgetStore) -> None:
    """set_planned mutates the workstream's planned allocation; an unknown one raises."""
    updated = store.set_planned(_PROGRAM, workstream="ops", planned_usd=50000)
    assert updated.planned_usd == 50000
    by_name = {w.name: w.planned_usd for w in store.list_workstreams(_PROGRAM)}
    assert by_name["ops"] == 50000

    with pytest.raises(KeyError):
        store.set_planned(_PROGRAM, workstream="nope", planned_usd=1000)


def test_seed_demo_ledger_is_idempotent(store: InMemoryBudgetStore) -> None:
    """Seeding lays down dated actual+committed entries; re-seeding adds nothing."""
    store.seed_demo_ledger(_PROGRAM)
    first = store.list_entries(_PROGRAM)
    assert first, "seed should produce ledger entries"
    assert all(e.created_at is not None for e in first)
    assert any(e.kind == "actual" for e in first)
    assert any(e.kind == "committed" for e in first)

    store.seed_demo_ledger(_PROGRAM)  # re-seed
    assert len(store.list_entries(_PROGRAM)) == len(first)  # no duplication

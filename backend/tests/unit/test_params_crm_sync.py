"""crm_sync params-block tests (A2; RESEARCH_v2 §II.1; CLAUDE.md §4.1, INV-11).

The ``crm_sync`` block is the single home (INV-11) for the CRM-as-truth
incremental poll's tunables: the ``result_cap`` (10,000-result cap per query),
the ``chunk_days`` window split that keeps any one query under that cap, and the
``search_qps`` rate budget (RESEARCH_v2 §II.1). The CRM Search 200-row page max
is a fixed HubSpot protocol ceiling owned by the adapter's ``_SEARCH_PAGE_SIZE``
constant, not a GT tunable (A-39). ``load_params`` parses the block into the
typed :class:`CrmSync` model; a renamed/retuned/out-of-range key fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import CrmSync, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_crm_sync_block_loads() -> None:
    """The crm_sync block loads into a typed CrmSync model with the §II.1 values."""
    crm_sync = load_params(EXAMPLE_PARAMS).crm_sync

    assert isinstance(crm_sync, CrmSync)
    assert crm_sync.result_cap == 10000  # 10,000-result cap per query
    assert crm_sync.chunk_days == 30
    assert crm_sync.search_qps == 4

    # Drift guard: a non-positive tunable is rejected.
    with pytest.raises(ValidationError):
        CrmSync(result_cap=10000, chunk_days=0, search_qps=4)

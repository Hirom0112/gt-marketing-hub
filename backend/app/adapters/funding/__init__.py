"""Funding-signal adapter package (ARCHITECTURE.md §7.2, INV-9, INV-10).

The funding-signal external boundary: a ``FundingSignalAdapter`` interface with a
``SimulatedFundingSignalAdapter`` that derives a GT-controlled ``FundingSignal``
from synthetic signals + the app's self-report field, in memory, with no network
client — explicitly **not** an Odyssey/TEFA status feed (none exists,
RESEARCH.md Q1). The production impl is supplied at go-live (config flip, zero
``core/`` change).
"""

from app.adapters.funding.base import FundingSignal, FundingSignalAdapter
from app.adapters.funding.simulated import SimulatedFundingSignalAdapter

__all__ = [
    "FundingSignal",
    "FundingSignalAdapter",
    "SimulatedFundingSignalAdapter",
]

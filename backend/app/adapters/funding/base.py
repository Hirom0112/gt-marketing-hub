"""The funding-signal boundary — interface + signal model (ARCHITECTURE.md §7.2).

§7.2 (authoritative):

    interface FundingSignalAdapter:
      read_signal(family_id) -> FundingSignal
      # {gt_confirmed, first_installment_received, self_report}

"Reads a GT-controlled signal, never an Odyssey/TEFA status feed. v1: simulated
from synthetic signals + the app's self-report field."

This is the boundary the §5.4 funding gate consults (INV-10): the gate opens on
GT-controlled signals — GT-confirmed enrollment, a first-installment receipt, and
the family's self-report — **not** an Odyssey/TEFA status API (none exists,
RESEARCH.md Q1). Award amounts live in params (§8); the signal carries only the
three booleans, never a dollar figure.

INV-9: like every external boundary, this is an interface with two impls —
Simulated (v1) and Production (go-live) — selected by config in
:mod:`app.adapters.registry`. The simulated impl is a pure in-memory/synthetic
source with no network client at all, so "GT-controlled, not an external API" is
a structural property. This module imports nothing from ``anthropic`` and keeps
``core/`` untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FundingSignal(BaseModel):
    """A GT-controlled funding signal for one family (§7.2, INV-10).

    The three booleans the §5.4 funding gate reads — all GT-owned, none sourced
    from an external Odyssey/TEFA feed. Frozen: a read of a signal is immutable,
    never a mutable record.

    Attributes:
        gt_confirmed: GT has confirmed the family's enrollment (a GT-owned fact).
        first_installment_received: GT has received the first tuition installment
            (a GT-owned receipt, not a third-party status).
        self_report: The family self-reported funding via the app's own field.
    """

    model_config = ConfigDict(frozen=True)

    gt_confirmed: bool
    first_installment_received: bool
    self_report: bool


class FundingSignalAdapter(ABC):
    """The funding-signal external boundary (§7.2).

    Two impls — Simulated (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. Core/gate depend only on this interface.
    """

    @abstractmethod
    def read_signal(self, family_id: UUID) -> FundingSignal:
        """Read the GT-controlled funding signal for one family (§7.2, INV-10)."""

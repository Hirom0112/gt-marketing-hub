"""HubSpot CRM adapter package (ARCHITECTURE.md §7.1, INV-9, OUT-3).

The CRM external boundary: a `CRMAdapter` interface with a `SimulatedCRMAdapter`
that records write-shaped calls in memory and never performs a live send. The
production impl is supplied at go-live (config flip, zero `core/`/`ai/` change).
"""

from app.adapters.hubspot.crm_adapter import (
    CRMAdapter,
    SendResult,
    SimulatedCRMAdapter,
    SyncResult,
)

__all__ = ["CRMAdapter", "SendResult", "SimulatedCRMAdapter", "SyncResult"]

"""The payments external boundary (A3; ARCHITECTURE §7-style adapter seam).

Two impls — Simulated (v1 default) and Production (Stripe, go-live) — selected by
config in :mod:`app.adapters.registry`. Core/fulfillment depend only on the
:class:`~app.adapters.payments.base.PaymentsAdapter` interface (INV-9).
"""

from __future__ import annotations

from app.adapters.payments.base import (
    PaymentIntentResult,
    PaymentsAdapter,
    PaymentsBudgetExceededError,
    SignatureVerificationError,
    verify_webhook_signature,
)
from app.adapters.payments.live import LivePaymentsAdapter
from app.adapters.payments.simulated import SimulatedPaymentsAdapter

__all__ = [
    "LivePaymentsAdapter",
    "PaymentIntentResult",
    "PaymentsAdapter",
    "PaymentsBudgetExceededError",
    "SignatureVerificationError",
    "SimulatedPaymentsAdapter",
    "verify_webhook_signature",
]

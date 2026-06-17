"""Simulated FundingSignalAdapter — synthetic, in-memory, no I/O (INV-9, INV-10).

The v1 impl of the §7.2 boundary: it derives a :class:`FundingSignal`
deterministically from the ``family_id`` (a synthetic stand-in for GT's own
enrollment/installment/self-report state) or replays an explicitly injected
signal state. There is **no network client** here by construction — no Odyssey,
no TEFA, no http at all — so "GT-controlled, not an external API" (INV-10) holds
structurally, provable from the source text alone (no live transport to mock).

Determinism: the derived path hashes the ``family_id`` (a stable, salted digest)
into three independent booleans, so the same family always reads the same signal
across calls and across fresh adapter instances, while different families spread
across the eight ``{gt_confirmed, first_installment_received, self_report}``
combinations. This module imports no LLM client and touches no ``core/`` state.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from app.adapters.funding.base import FundingSignal, FundingSignalAdapter


def _derive_bit(family_id: UUID, salt: str) -> bool:
    """Deterministically derive one boolean from ``family_id`` and a field salt.

    A salted BLAKE2b digest keyed per field gives three statistically independent
    bits with no shared randomness state — pure, no I/O, stable across processes.
    """
    digest = hashlib.blake2b(f"{salt}:{family_id}".encode(), digest_size=8).digest()
    return digest[0] & 1 == 1


class SimulatedFundingSignalAdapter(FundingSignalAdapter):
    """In-memory synthetic source for the GT-controlled funding signal (INV-9/10).

    Two modes, both pure and offline:

    - **Injected** — a fixed ``{family_id: FundingSignal}`` map supplied at
      construction (e.g. seeded from the synthetic generator + the app's
      self-report field); ``read_signal`` returns the recorded signal.
    - **Derived** — for any family not in the map (or none supplied),
      ``read_signal`` derives the three booleans deterministically from the
      ``family_id`` via :func:`_derive_bit`.

    No network client exists on this class — "GT-controlled, not an external API"
    is therefore a structural property, not a configured behaviour.
    """

    def __init__(self, signals: dict[UUID, FundingSignal] | None = None) -> None:
        # Optional injected synthetic signals; never required, never mutated.
        self._signals: dict[UUID, FundingSignal] = dict(signals or {})

    def read_signal(self, family_id: UUID) -> FundingSignal:
        """Return the GT-controlled funding signal for ``family_id`` (§7.2).

        An injected signal wins; otherwise the signal is derived deterministically
        from the ``family_id``. No I/O, no external feed (INV-9, INV-10).
        """
        injected = self._signals.get(family_id)
        if injected is not None:
            return injected
        return FundingSignal(
            gt_confirmed=_derive_bit(family_id, "gt_confirmed"),
            first_installment_received=_derive_bit(family_id, "first_installment_received"),
            self_report=_derive_bit(family_id, "self_report"),
            # R2: the GT-controlled voucher-selection signal, derived like the
            # others from a per-field salt (INV-10) — no external feed.
            family_selected=_derive_bit(family_id, "family_selected"),
        )

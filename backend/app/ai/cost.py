"""Per-run spend/token governor — the metered-API hard cap + kill switch.

Every metered LLM run is bounded by TWO hard ceilings (INV-8, NFR-5):

* a per-run **token** cap — env ``LLM_RUN_TOKEN_CAP`` (`Settings.llm_run_token_cap`);
* a per-run **USD** cap — `params.cost_caps.anthropic_per_run_usd`.

A :class:`RunBudget` accumulates token/USD usage across the calls of a single
run. A charge that would breach *either* ceiling is REFUSED (raising
:class:`CostCapExceeded`) and trips the budget for the rest of the run — so the
caller can degrade to a deterministic/placeholder path rather than overspend
silently. Both limits are read from settings/params at construction; this module
hardcodes no tunable (INV-11) and invents no pricing constant — USD is charged
explicitly by the caller, never inferred from a per-token rate.

This module is part of the AI edge but imports nothing external: it is a pure
accumulator over plain numbers, so importing it needs no SDK or key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.params import Params
    from app.core.settings import Settings


class CostCapExceeded(Exception):
    """Raised when a charge would breach the per-run token or USD cap.

    Signals the caller to degrade to the deterministic/placeholder path. The
    refused charge is NOT applied — `tokens_used`/`usd_spent` are unchanged —
    but the budget is left :attr:`RunBudget.tripped` for the rest of the run.
    """


@dataclass
class RunBudget:
    """A per-run accumulator that fails closed at the token and USD ceilings.

    Build via :meth:`from_config`; the caps come from the env settings (token)
    and params (USD), never a literal (INV-11). The caller reports usage via
    :meth:`charge`; once a charge would breach a cap the budget is tripped and
    every subsequent :meth:`charge` is refused.
    """

    token_cap: int
    usd_cap: float
    tokens_used: int = 0
    usd_spent: float = 0.0
    _tripped: bool = field(default=False, repr=False)

    @classmethod
    def from_config(cls, *, settings: Settings, params: Params) -> RunBudget:
        """Construct a budget from the env settings (token cap) + params (USD cap)."""
        return cls(
            token_cap=settings.llm_run_token_cap,
            usd_cap=params.cost_caps.anthropic_per_run_usd,
        )

    @property
    def tripped(self) -> bool:
        """True once a charge has breached (or would have breached) a cap."""
        return self._tripped

    def would_exceed(self, *, tokens: int, usd: float) -> bool:
        """Whether applying this charge would breach the token OR USD cap.

        Already-tripped budgets always report True so no further live work runs.
        """
        if self._tripped:
            return True
        return (self.tokens_used + tokens) > self.token_cap or (self.usd_spent + usd) > self.usd_cap

    def charge(self, *, tokens: int, usd: float) -> None:
        """Record usage for one call, or refuse + trip if it would breach a cap.

        Raises:
            CostCapExceeded: if the charge would breach either ceiling (or the
                budget is already tripped). The charge is not applied.
        """
        if self.would_exceed(tokens=tokens, usd=usd):
            self._tripped = True
            raise CostCapExceeded(
                "per-run cap exceeded: "
                f"tokens {self.tokens_used + tokens}/{self.token_cap}, "
                f"usd {self.usd_spent + usd:.2f}/{self.usd_cap:.2f}"
            )
        self.tokens_used += tokens
        self.usd_spent += usd

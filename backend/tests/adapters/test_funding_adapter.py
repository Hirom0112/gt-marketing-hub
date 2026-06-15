"""Simulated FundingSignalAdapter — FR-2.7, INV-9, INV-10 (ARCHITECTURE.md §7.2).

The funding signal is a **GT-controlled** read, explicitly *not* an Odyssey/TEFA
status feed — none exists (RESEARCH.md Q1). §7.2:

    interface FundingSignalAdapter:
      read_signal(family_id) -> FundingSignal
      # {gt_confirmed, first_installment_received, self_report}

"Reads a GT-controlled signal, never an Odyssey/TEFA status feed. v1: simulated
from synthetic signals + the app's self-report field." Award amounts come from
params (§8), **not** the adapter.

These are the §4.1-adapter-scope RED tests:

- ``read_signal(family_id)`` returns a frozen ``FundingSignal`` carrying the
  three booleans, **deterministically** for a given family_id, with no network.
- The simulated impl is a pure in-memory/synthetic signal source — it imports no
  http client and no ``anthropic`` (INV-10: GT-controlled, not an external API).
- The registry returns the simulated impl under the v1 default; a future live
  mode fails **loud** (``NotImplementedError``) — never a silent live feed.
"""

from __future__ import annotations

import importlib
import inspect
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.adapters.funding.base import FundingSignal, FundingSignalAdapter
from app.adapters.funding.simulated import SimulatedFundingSignalAdapter
from app.adapters.registry import get_funding_signal_adapter


def test_read_signal_returns_funding_signal() -> None:
    """``read_signal`` ⇒ frozen ``FundingSignal`` of three bools; deterministic, no I/O.

    The same ``family_id`` yields an identical signal across calls and across
    fresh adapter instances (the synthetic source is pure/derived), and distinct
    families can differ — proving a real derivation rather than a constant.
    """
    family_id = uuid4()
    adapter = SimulatedFundingSignalAdapter()
    assert isinstance(adapter, FundingSignalAdapter)

    signal = adapter.read_signal(family_id)

    assert isinstance(signal, FundingSignal)
    assert isinstance(signal.gt_confirmed, bool)
    assert isinstance(signal.first_installment_received, bool)
    assert isinstance(signal.self_report, bool)

    # Deterministic for a given family_id: same instance and a fresh instance agree.
    assert adapter.read_signal(family_id) == signal
    assert SimulatedFundingSignalAdapter().read_signal(family_id) == signal

    # Frozen — a GT-controlled signal is an immutable read, not a mutable record.
    with pytest.raises(ValidationError):
        signal.gt_confirmed = True  # type: ignore[misc]

    # Derivation, not a constant: at least one other family differs across a sample.
    others = {adapter.read_signal(uuid4()) for _ in range(64)}
    assert len(others) > 1


def test_simulated_is_not_odyssey_api() -> None:
    """Structural INV-10: the module is a pure in-memory signal source.

    It imports no http client and no ``anthropic`` — there is no external funding
    API to call, so "GT-controlled" is provable from the source text, not by
    mocking sockets.
    """
    module = importlib.import_module("app.adapters.funding.simulated")
    source = inspect.getsource(module)

    forbidden = ("httpx", "requests", "aiohttp", "urllib", "socket", "anthropic")
    for token in forbidden:
        assert token not in source, f"simulated funding adapter must not reference {token!r}"


def test_registry_returns_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 default ⇒ simulated impl; a future live mode fails loud (no silent feed)."""
    monkeypatch.setenv("SEND_MODE", "simulate")
    adapter = get_funding_signal_adapter()
    assert isinstance(adapter, SimulatedFundingSignalAdapter)
    assert isinstance(adapter, FundingSignalAdapter)

    monkeypatch.setenv("SEND_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_funding_signal_adapter()

"""Simulated SocialAdapter — FR-3.x social, OUT-2, INV-9 (ARCHITECTURE.md §7.4).

All v1 social sends are **simulated**: the backend holds the queue (no native
platform scheduling), and ``publish`` records a simulated receipt — **no live
send** (OUT-2, ``SOCIAL_POST_MODE=simulate``, ``SEND_MODE=simulate``). §7.4:

    interface SocialAdapter:
      schedule(post, when) -> QueueRef    # backend-held queue, no native scheduling
      publish(post) -> PostResult         # simulated

These are the §4.1-adapter-scope RED tests:

- ``schedule`` ⇒ a frozen ``QueueRef`` (synthetic, deterministic queue ref).
- ``publish`` ⇒ a frozen ``PostResult`` with ``simulated=True`` and a synthetic
  ``post_id`` receipt — deterministic (no ``random``/``uuid4``/wall-clock).
- The simulated impl is a pure, offline source — it imports no http client and no
  ``anthropic``; "no live send" is structural (INV-9).
- The registry returns the simulated impl under the v1 default
  (``SOCIAL_POST_MODE=simulate``); ``live`` fails **loud**
  (``NotImplementedError``) — never a silent live send.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import ValidationError

from app.adapters.registry import get_social_adapter
from app.adapters.social.base import (
    PostResult,
    QueueRef,
    SocialAdapter,
    SocialPost,
)
from app.adapters.social.simulated import SimulatedSocialAdapter

_POST = SocialPost(channel="instagram", body="A win worth sharing #gtschool")
_WHEN = "2026-07-01T09:00:00Z"


def test_schedule_returns_simulated_queue_ref() -> None:
    """``schedule`` ⇒ frozen ``QueueRef``; deterministic backend-held queue ref."""
    adapter = SimulatedSocialAdapter()
    assert isinstance(adapter, SocialAdapter)

    ref = adapter.schedule(_POST, _WHEN)

    assert isinstance(ref, QueueRef)
    assert ref.simulated is True
    assert ref.queue_id  # non-empty synthetic queue ref
    assert ref.scheduled_for == _WHEN

    # Deterministic for a given (post, when): same instance and a fresh one agree.
    assert adapter.schedule(_POST, _WHEN) == ref
    assert SimulatedSocialAdapter().schedule(_POST, _WHEN) == ref

    # Frozen — a queue ref is an immutable receipt, not a mutable row.
    with pytest.raises(ValidationError):
        ref.queue_id = "x"  # type: ignore[misc]

    # Derivation, not a constant: a different time yields a different queue ref.
    other = adapter.schedule(_POST, "2026-07-02T09:00:00Z")
    assert other != ref


def test_publish_returns_simulated_receipt() -> None:
    """``publish`` ⇒ frozen ``PostResult`` with ``simulated=True`` + synthetic post_id."""
    adapter = SimulatedSocialAdapter()

    result = adapter.publish(_POST)

    assert isinstance(result, PostResult)
    assert result.simulated is True
    assert result.post_id  # synthetic receipt id

    assert adapter.publish(_POST) == result
    assert SimulatedSocialAdapter().publish(_POST) == result

    # Derivation, not a constant: a different post yields a different receipt.
    other = adapter.publish(SocialPost(channel="x", body="different"))
    assert other != result


def test_simulated_is_not_a_live_send() -> None:
    """Structural INV-9/OUT-2: the module is a pure, offline source — no live send.

    It imports no http client and no ``anthropic`` — there is no platform endpoint
    to call, so "simulated, not a live send" is provable from the source text.
    """
    module = importlib.import_module("app.adapters.social.simulated")
    source = inspect.getsource(module)

    forbidden = (
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "anthropic",
        "random",
        "uuid4",
    )
    for token in forbidden:
        assert token not in source, f"simulated social adapter must not reference {token!r}"


def test_registry_returns_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 default ⇒ simulated impl; a future live mode fails loud (no silent send)."""
    monkeypatch.setenv("SOCIAL_POST_MODE", "simulate")
    adapter = get_social_adapter()
    assert isinstance(adapter, SimulatedSocialAdapter)
    assert isinstance(adapter, SocialAdapter)

    monkeypatch.setenv("SOCIAL_POST_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_social_adapter()

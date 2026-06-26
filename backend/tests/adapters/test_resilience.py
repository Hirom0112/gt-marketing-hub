"""Standalone retry/backoff helper — ``with_retry`` (A5, INV-8/9).

The reusable resilience wrapper for outbound adapter HTTP calls (the HubSpot +
Stripe live adapters reuse it in a later unit). It wraps a per-attempt thunk
(``Callable[[], httpx.Response]``) and:

- retries transient failures — HTTP 429 + any 5xx, and transient transport
  errors (``httpx.TransportError`` subclasses) — with exponential backoff;
- honors a ``Retry-After`` (seconds) header when present;
- propagates a non-retryable 4xx (e.g. 404) immediately, with NO retry;
- gives up after ``max_attempts`` and returns the LAST response (the caller's
  existing non-2xx handling then applies — see the helper's docstring).

Everything is INJECTED: the config (``max_attempts`` / ``base_delay_ms`` /
``max_delay_ms``) and the ``sleep`` callable. A ``sleep`` SPY records its args so
the tests are deterministic — NO real wall-clock sleeping (repo clock-injection
discipline). No params/settings are read here; the caller passes the numbers.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.adapters._resilience import with_retry


class _SleepSpy:
    """A ``sleep`` double that records every delay it was asked to wait."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _scripted(*items: httpx.Response | Exception) -> Callable[[], httpx.Response]:
    """A per-attempt thunk that yields ``items`` in order (Exceptions are raised).

    Mirrors the real "one attempt = one ``httpx`` call" contract: each invocation
    returns the next scripted response, or raises the next scripted exception.
    """
    state = {"i": 0}

    def thunk() -> httpx.Response:
        item = items[state["i"]]
        state["i"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return thunk


# --------------------------------------------------------------- the headline RED
def test_retries_on_429_then_succeeds() -> None:
    """A 429 (twice) then a 200: returns the 200; ``sleep`` saw backoff-shaped delays."""
    spy = _SleepSpy()
    call = _scripted(
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json={"ok": True}),
    )

    response = with_retry(
        call,
        max_attempts=5,
        base_delay_ms=100,
        max_delay_ms=1000,
        sleep=spy,
    )

    assert response.status_code == 200
    # Exponential on base: attempt 0 -> 100ms, attempt 1 -> 200ms (seconds to sleep).
    assert spy.calls == pytest.approx([0.1, 0.2])


def test_retries_on_500_then_succeeds() -> None:
    """A transient 5xx is retried just like a 429."""
    spy = _SleepSpy()
    call = _scripted(httpx.Response(500), httpx.Response(200))

    response = with_retry(call, max_attempts=4, base_delay_ms=100, max_delay_ms=1000, sleep=spy)

    assert response.status_code == 200
    assert spy.calls == pytest.approx([0.1])


def test_non_retryable_4xx_returns_immediately() -> None:
    """A 404 is NOT transient — returned at once, with NO sleep/retry."""
    spy = _SleepSpy()
    call = _scripted(httpx.Response(404))

    response = with_retry(call, max_attempts=5, base_delay_ms=100, max_delay_ms=1000, sleep=spy)

    assert response.status_code == 404
    assert spy.calls == []


def test_retry_after_header_is_honored() -> None:
    """A ``Retry-After: 2`` header makes ``sleep`` wait ~2.0s, not the computed backoff."""
    spy = _SleepSpy()
    call = _scripted(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200),
    )

    response = with_retry(call, max_attempts=5, base_delay_ms=100, max_delay_ms=1000, sleep=spy)

    assert response.status_code == 200
    assert spy.calls == pytest.approx([2.0])


def test_backoff_is_capped_at_max_delay() -> None:
    """Exponential growth is clamped to ``max_delay_ms`` (no unbounded waits)."""
    spy = _SleepSpy()
    call = _scripted(
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200),
    )

    response = with_retry(call, max_attempts=5, base_delay_ms=100, max_delay_ms=250, sleep=spy)

    assert response.status_code == 200
    # 100ms, 200ms, then 400ms clamped to 250ms.
    assert spy.calls == pytest.approx([0.1, 0.2, 0.25])


def test_max_attempts_exhausted_returns_last_response() -> None:
    """All attempts still transient: returns the LAST 429 (caller surfaces it)."""
    spy = _SleepSpy()
    call = _scripted(httpx.Response(429), httpx.Response(429), httpx.Response(429))

    response = with_retry(call, max_attempts=3, base_delay_ms=100, max_delay_ms=1000, sleep=spy)

    assert response.status_code == 429
    # 3 attempts -> 2 backoff sleeps between them; no sleep after the final attempt.
    assert spy.calls == pytest.approx([0.1, 0.2])


def test_transport_error_is_retried() -> None:
    """A transient ``httpx.TransportError`` (e.g. ConnectError) is retried then succeeds."""
    spy = _SleepSpy()
    call = _scripted(httpx.ConnectError("boom"), httpx.Response(200))

    response = with_retry(call, max_attempts=4, base_delay_ms=100, max_delay_ms=1000, sleep=spy)

    assert response.status_code == 200
    assert spy.calls == pytest.approx([0.1])


def test_non_transient_exception_propagates() -> None:
    """A non-transport exception is NOT retried — it propagates immediately."""
    spy = _SleepSpy()
    call = _scripted(ValueError("programmer error"))

    with pytest.raises(ValueError):
        with_retry(call, max_attempts=5, base_delay_ms=100, max_delay_ms=1000, sleep=spy)
    assert spy.calls == []

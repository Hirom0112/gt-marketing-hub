"""Standalone retry/backoff helper for outbound adapter HTTP calls (A5, INV-8/9).

A reusable wrapper the live adapters (HubSpot §W2, Stripe) layer over their
per-attempt call so a transient failure self-heals instead of surfacing as a hard
error. It is deliberately **infrastructure, not policy**:

- **Config is INJECTED.** ``max_attempts`` / ``base_delay_ms`` / ``max_delay_ms``
  are passed by the caller (sourced from ``params.resilience`` at the composition
  root); this module reads NO params/settings/repository — it is pure-ish, stdlib +
  ``httpx`` only.
- **The clock is INJECTED.** It takes ``sleep: Callable[[float], None]`` so tests
  pass a spy and never touch the wall clock (repo clock-injection discipline — no
  real ``time.sleep``/``time.time`` in testable code).

What counts as transient (and is retried, attempts permitting):

- an HTTP response with status **429** or any **5xx**;
- a raised **``httpx.TransportError``** subclass (e.g. ``ConnectError`` /
  ``ReadTimeout`` — a connection/read failure, not a programming error).

Everything else is non-transient: a non-retryable **4xx** response is returned
**immediately** (with_retry NEVER raises on a non-2xx — the CALLER decides what a
non-2xx means; this helper only *retries* the transient ones), and any non-transport
exception **propagates** unchanged.

Exhausted-attempts choice: after ``max_attempts`` on a still-retryable RESPONSE we
**return the last response** (the boring choice — the caller's existing non-2xx
handling, e.g. ``raise_for_status`` or a guard, then applies; no bespoke
``RetryExhaustedError`` to teach callers). When the final attempt instead RAISES a
transient transport error, that exception propagates (there is no response to
return).

Backoff: exponential on ``base_delay_ms`` (attempt 0 → base, attempt 1 → 2×base,
attempt *n* → 2ⁿ×base), clamped to ``max_delay_ms``. A ``Retry-After`` (seconds)
header takes precedence: we wait the larger of it and the computed backoff (the
server's explicit instruction wins — Stripe/HTTP convention). A ``Retry-After`` in
HTTP-date form (not bare seconds) is ignored and the computed backoff is used.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx


def with_retry(
    call: Callable[[], httpx.Response],
    *,
    max_attempts: int,
    base_delay_ms: int,
    max_delay_ms: int,
    sleep: Callable[[float], None],
) -> httpx.Response:
    """Run ``call`` with retry+backoff on transient failures; return its response.

    Calls ``call()`` (one attempt = one HTTP call). On a transient failure (429/5xx
    response, or a raised ``httpx.TransportError``) with attempts remaining, sleeps
    the backoff delay (see module docstring) and retries; otherwise returns the
    response (2xx OR a non-retryable 4xx — this helper does not raise on non-2xx).
    After ``max_attempts`` on a still-retryable response, returns the last response;
    a transient error on the final attempt propagates.

    Args:
        call: A thunk making ONE attempt and returning its ``httpx.Response``.
        max_attempts: Total attempts (must be ≥ 1); the (max_attempts)th is final.
        base_delay_ms: The first backoff delay; doubles each subsequent retry.
        max_delay_ms: The cap the (exponential) backoff is clamped to.
        sleep: Injected sleeper (seconds); a test spy makes this deterministic.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    for attempt in range(max_attempts):
        is_last = attempt + 1 >= max_attempts
        try:
            response = call()
        except httpx.TransportError:
            # A connection/read failure is transient — retry unless this was the
            # last attempt, in which case let it propagate (no response to return).
            if is_last:
                raise
            sleep(_backoff_seconds(attempt, base_delay_ms, max_delay_ms))
            continue

        if not is_last and _is_retryable(response.status_code):
            sleep(_delay_seconds(response, attempt, base_delay_ms, max_delay_ms))
            continue
        return response

    # Unreachable: the loop always returns/raises for max_attempts >= 1 (guarded
    # above). Present so the function is total under mypy --strict.
    raise AssertionError("with_retry exhausted its loop without returning")  # pragma: no cover


def _is_retryable(status_code: int) -> bool:
    """A status is transient iff it is 429 (rate limit) or any 5xx (server error)."""
    return status_code == 429 or 500 <= status_code <= 599


def _backoff_seconds(attempt: int, base_delay_ms: int, max_delay_ms: int) -> float:
    """Exponential backoff for ``attempt`` (0-based), clamped to ``max_delay_ms``, in seconds."""
    delay_ms: int = min(base_delay_ms * (2**attempt), max_delay_ms)
    return delay_ms / 1000.0


def _delay_seconds(
    response: httpx.Response, attempt: int, base_delay_ms: int, max_delay_ms: int
) -> float:
    """The wait before the next retry: ``max(Retry-After, computed backoff)`` seconds.

    The server's explicit ``Retry-After`` (seconds) takes precedence over the
    computed backoff (Stripe/HTTP convention); a missing or HTTP-date-form header
    falls back to the computed exponential backoff.
    """
    backoff = _backoff_seconds(attempt, base_delay_ms, max_delay_ms)
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return max(retry_after, backoff)
    return backoff


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header in bare-seconds form; ``None`` if absent/HTTP-date.

    Only the integer/float "delta-seconds" form is honored (the form Stripe and most
    APIs send); an HTTP-date value isn't parsed here and returns ``None`` so the
    caller falls back to the computed backoff.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None

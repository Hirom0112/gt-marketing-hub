"""Unit tests for the stdlib HS256 JWT verifier (B1; RESEARCH_v2 §II.5).

``verify_hs256`` is the core-pure (stdlib-only) signature+expiry check beneath the
verified-identity principal that replaces the spoofable ``X-Demo-Role`` header (the
S1 audit finding). It NEVER calls ``time.time()`` — ``now`` is injected by the API
layer — so these tests are fully deterministic. Every failure mode is a ``JwtError``,
never an uncaught exception (fail-closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app.core.jwt_verify import JwtError, verify_hs256

_SECRET = "test-supabase-jwt-secret-0123456789"
_NOW = 1_700_000_000  # a fixed epoch-seconds "now" for determinism


def _b64url(raw: bytes) -> str:
    """base64url WITHOUT padding (the JWT wire form)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mint(payload: dict[str, object], *, secret: str = _SECRET) -> str:
    """Build a valid HS256 JWT for ``payload`` signed with ``secret`` (stdlib only)."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def test_valid_round_trip_returns_claims() -> None:
    """A correctly-signed, unexpired token round-trips to its claims dict."""
    token = _mint({"sub": "user-123", "exp": _NOW + 3600, "app_metadata": {"role": "leader"}})
    claims = verify_hs256(token, secret=_SECRET, now=_NOW)
    assert claims["sub"] == "user-123"
    assert claims["app_metadata"]["role"] == "leader"


def test_tampered_signature_raises() -> None:
    """Flipping the payload after signing (or signing with a wrong key) → JwtError."""
    token = _mint({"sub": "u", "exp": _NOW + 3600})
    forged = _mint({"sub": "u", "exp": _NOW + 3600}, secret="wrong-secret")
    with pytest.raises(JwtError):
        verify_hs256(forged, secret=_SECRET, now=_NOW)

    # Same token, but verified with the wrong secret, must also fail.
    with pytest.raises(JwtError):
        verify_hs256(token, secret="another-wrong-secret", now=_NOW)


def test_expired_token_raises() -> None:
    """An ``exp`` at/!before the injected ``now`` is expired ⇒ JwtError."""
    token = _mint({"sub": "u", "exp": _NOW - 1})
    with pytest.raises(JwtError):
        verify_hs256(token, secret=_SECRET, now=_NOW)


def test_nbf_in_future_raises() -> None:
    """A not-before claim ahead of ``now`` ⇒ JwtError (token not yet valid)."""
    token = _mint({"sub": "u", "exp": _NOW + 3600, "nbf": _NOW + 100})
    with pytest.raises(JwtError):
        verify_hs256(token, secret=_SECRET, now=_NOW)


def test_malformed_inputs_raise_jwterror_not_uncaught() -> None:
    """Bad base64, missing segments, and non-JSON payloads all raise JwtError."""
    for bad in ("", "only-one-segment", "two.segments", "a.b.c.d", "!!!.@@@.###"):
        with pytest.raises(JwtError):
            verify_hs256(bad, secret=_SECRET, now=_NOW)

    # Three well-formed-looking segments but a non-JSON payload.
    header_b64 = _b64url(json.dumps({"alg": "HS256"}).encode("utf-8"))
    payload_b64 = _b64url(b"not-json{{{")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    token = f"{header_b64}.{payload_b64}.{_b64url(sig)}"
    with pytest.raises(JwtError):
        verify_hs256(token, secret=_SECRET, now=_NOW)


def test_blank_secret_fails_closed() -> None:
    """An empty verifying secret can never validate a token ⇒ JwtError (fail-closed)."""
    token = _mint({"sub": "u", "exp": _NOW + 3600})
    with pytest.raises(JwtError):
        verify_hs256(token, secret="", now=_NOW)

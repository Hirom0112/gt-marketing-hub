"""Stdlib HS256 JWT verification — the verified-identity core (B1; no new dep).

This is the cryptographic check beneath the verified-identity principal that
REPLACES the spoofable client-supplied role header (the security audit's top
finding, S1). Supabase signs its end-user JWTs with an HS256 shared secret
(``SUPABASE_JWT_SECRET``, TECH_STACK §5.2); this module recomputes that signature
with the stdlib and checks expiry — protecting the ≤15-dependency budget (no
``pyjwt``/``python-jose``): only ``hmac``/``hashlib``/``base64``/``json``.

Purity: it is core-pure and does NO I/O and NEVER reads the clock — ``now`` is
INJECTED by the API layer (which passes ``int(datetime.now(UTC).timestamp())``) so
the verifier is fully deterministic and unit-testable. Every failure mode — bad
base64, missing segments, non-JSON payload, signature mismatch, expiry, a future
``nbf`` — raises :class:`JwtError`, never an uncaught exception (fail-closed:
INV-3/INV-5 posture). The caller maps :class:`JwtError` to a 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


class JwtError(Exception):
    """Any JWT verification failure (bad signature, expiry, malformed input).

    A single exception type so the API layer has exactly one thing to catch and
    map to a 401 — there is no "soft" failure mode (fail-closed; INV-4 posture).
    """


def _b64url_encode(raw: bytes) -> str:
    """base64url WITHOUT padding — the JWT wire form (the inverse of decode)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_hs256(claims: dict[str, Any], *, secret: str) -> str:
    """Sign ``claims`` into a compact HS256 JWS — the inverse of :func:`verify_hs256`.

    Mirrors Supabase's HS256 signing (the same ``hmac.new(secret, …, sha256)`` the
    verifier recomputes) using only the stdlib (no ``pyjwt`` — protects the
    ≤15-dependency budget). The header is the fixed ``{"alg":"HS256","typ":"JWT"}``;
    the payload is ``claims`` serialized as JSON. The verifier round-trips any token
    this produces (``verify_hs256(sign_hs256(c, secret=s), secret=s, now=…) == c``).

    This is the production signer the coworker proxy uses to mint the closer's
    ``Authorization: Bearer`` token (it authenticates AS a fixed operator), and the
    test helper (:func:`tests.api._jwt.mint_jwt`) builds Supabase-shaped claims on
    top of it. It performs NO clock read and NO I/O.

    Args:
        claims: The JWT payload (e.g. ``sub`` / ``exp`` / ``app_metadata``).
        secret: The HS256 shared secret to sign with (``SUPABASE_JWT_SECRET``).

    Returns:
        The compact JWS string (``header.payload.signature``).
    """
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(claims).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (no padding on the wire), raising JwtError on junk.

    JWT segments are unpadded base64url; we restore the ``=`` padding before
    decoding. Any malformed input (bad alphabet, wrong length) becomes a
    :class:`JwtError`, never a leaked ``binascii.Error`` (fail-closed).
    """
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, TypeError) as exc:  # binascii.Error subclasses ValueError
        raise JwtError("invalid base64url segment") from exc


def verify_hs256(token: str, *, secret: str, now: int) -> dict[str, Any]:
    """Verify an HS256 JWT against ``secret`` and return its decoded claims.

    Splits ``header.payload.signature`` (base64url), recomputes
    ``HMAC-SHA256(secret, f"{header}.{payload}")``, base64url-encodes it, and
    compares against the supplied signature with :func:`hmac.compare_digest`
    (constant-time). On a mismatch ⇒ :class:`JwtError`. Then decodes the payload
    JSON and checks ``exp`` (epoch seconds) against the injected ``now`` (expired
    ⇒ :class:`JwtError`); honors ``nbf`` when present.

    Args:
        token: The compact JWS string (``header.payload.signature``).
        secret: The HS256 shared secret (``SUPABASE_JWT_SECRET``). A blank secret
            can never validate a token ⇒ :class:`JwtError` (fail-closed).
        now: Epoch seconds the caller injects (the verifier never reads the clock).

    Returns:
        The decoded claims dict.

    Raises:
        JwtError: On a blank secret, malformed token, signature mismatch, a
            missing/invalid or past ``exp``, or a future ``nbf``.
    """
    if not secret:
        # Fail-closed: with no verifying secret, NOTHING validates (never allow).
        raise JwtError("no verifying secret configured")

    parts = token.split(".")
    if len(parts) != 3:
        raise JwtError("token must have exactly three segments")
    header_b64, payload_b64, signature_b64 = parts

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii", errors="ignore")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_sig = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise JwtError("signature mismatch")

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (json.JSONDecodeError, ValueError) as exc:
        raise JwtError("payload is not valid JSON") from exc
    if not isinstance(claims, dict):
        raise JwtError("payload is not a JSON object")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise JwtError("missing or invalid exp claim")
    if now >= exp:
        raise JwtError("token has expired")

    nbf = claims.get("nbf")
    if nbf is not None:
        if not isinstance(nbf, (int, float)) or isinstance(nbf, bool):
            raise JwtError("invalid nbf claim")
        if now < nbf:
            raise JwtError("token is not yet valid (nbf)")

    return claims

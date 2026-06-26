"""Test-only HS256 JWT mint helper (B1) — produces signed tokens for principal tests.

Mirrors how Supabase signs end-user JWTs (HS256 over the shared secret), with the
role in ``app_metadata`` by default. The negative case mints the role into
``user_metadata`` (client-writable in Supabase) instead, to prove it is IGNORED.
Stdlib only — no new dependency (protects the <=15 dep budget). Used by this unit's
tests and reused by T4b's migration tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4


def _b64url(raw: bytes) -> str:
    """base64url WITHOUT padding (the JWT wire form)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(
    *,
    role: str,
    secret: str,
    agent_id: UUID | str | None = None,
    sub: UUID | str | None = None,
    metadata_key: str = "app_metadata",
    exp_delta: int = 3600,
    now: int | None = None,
) -> str:
    """Build a valid HS256 JWT carrying ``role`` (in ``metadata_key``) signed by ``secret``.

    Args:
        role: The role to place under ``metadata_key.role`` (e.g. ``"leader"``).
        secret: The HS256 shared secret to sign with (the test ``SUPABASE_JWT_SECRET``).
        agent_id: Optional operator agent id, placed under ``metadata_key.agent_id``.
        sub: Optional subject (the user id); a random uuid when omitted.
        metadata_key: Where the role lives — ``"app_metadata"`` (trusted) by default,
            or ``"user_metadata"`` for the negative test (must be ignored).
        exp_delta: Seconds from ``now`` until expiry (negative ⇒ already expired).
        now: Epoch-seconds base; the real clock when omitted.

    Returns:
        The compact JWS string (``header.payload.signature``).
    """
    base = now if now is not None else int(datetime.now(UTC).timestamp())
    metadata: dict[str, object] = {"role": role}
    if agent_id is not None:
        metadata["agent_id"] = str(agent_id)

    payload: dict[str, object] = {
        "sub": str(sub) if sub is not None else str(uuid4()),
        "iat": base,
        "exp": base + exp_delta,
        metadata_key: metadata,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"

"""Test-only HS256 JWT mint helper (B1) — produces signed tokens for principal tests.

Mirrors how Supabase signs end-user JWTs (HS256 over the shared secret), with the
role in ``app_metadata`` by default. The negative case mints the role into
``user_metadata`` (client-writable in Supabase) instead, to prove it is IGNORED.
Stdlib only — no new dependency (protects the <=15 dep budget). Used by this unit's
tests and reused by T4b's migration tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.jwt_verify import sign_hs256

# The single canonical test JWT secret (B1). Every migrated principal test signs with
# this and the conftest auth shim verifies against it, so there is ONE home for the
# value the suite trusts (INV-11 spirit). Distinct from any real SUPABASE_JWT_SECRET.
TEST_JWT_SECRET = "test-supabase-jwt-secret-deadbeef"


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
    # Sign through the SAME stdlib HS256 signer the production code uses (DRY; the
    # verifier round-trips it). The test helper only shapes the Supabase-style claims.
    return sign_hs256(payload, secret=secret)

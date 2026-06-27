"""Demo-login token endpoint (B1 task 5a) — the demo-auth bridge.

``POST /auth/demo-token`` is the DEMO equivalent of a Supabase login: it mints a
REAL signed HS256 JWT for a chosen seat (``admin`` / ``leader`` / ``operator``) so
the frontend can authenticate against the verified-principal gate
(``app.api.deps.get_principal``) without a real Supabase login — which v1 does not
have. The minted token is a Supabase-shaped claim set (role in ``app_metadata``)
signed with the same ``SUPABASE_JWT_SECRET`` the verifier checks, so it round-trips
through ``get_principal`` like any production token.

Honesty / framing (ASSUMPTIONS A-40). This is the demo-auth analogue of the INV-9
SIMULATED adapters, NOT a production auth bypass:

  * It is ``AUTH_MODE``-GATED. With ``AUTH_MODE=live`` the endpoint returns **404**
    (it does not exist) — a live deployment relies on Supabase-issued JWTs and
    never mints its own seats here.
  * All data is SYNTHETIC (INV-1); there is no real account to impersonate.
  * It does NOT re-introduce S1. S1 was an UNVERIFIED, client-spelled ``X-Demo-Role``
    header that defaulted to ``admin``; this issues VERIFIED, SIGNED tokens only in
    demo mode, and the backend still verifies EVERY request through ``get_principal``
    (a forged/tampered/expired token is rejected exactly as before). It does not
    weaken ``get_principal`` in any way.

This module is a composition-root router (it may read the settings + params seams);
it makes no live external call and writes no state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_params, get_settings_dep
from app.core.jwt_verify import sign_hs256
from app.core.params import Params
from app.core.settings import Settings

router = APIRouter(tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ParamsDep = Annotated[Params, Depends(get_params)]

# A FIXED namespace so each seat gets a STABLE demo ``sub`` (the auth-user id):
# uuid5(namespace, "gt-demo-seat:<role>:<agent_id>") is deterministic, so re-minting
# the same seat yields the same subject (a believable, persistent demo identity).
_DEMO_SEAT_NAMESPACE = UUID("d3105ea7-0000-4000-8000-000000000000")


class DemoTokenRequest(BaseModel):
    """The seat to mint a demo token for — a role and an optional operator agent id."""

    role: str
    agent_id: UUID | None = None


class DemoTokenResponse(BaseModel):
    """A signed seat JWT, OAuth-bearer shaped (``access_token`` / ``token_type``)."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _demo_seat_sub(role: str, agent_id: UUID | None) -> UUID:
    """A stable demo ``sub`` (auth-user id) per (role, agent_id) seat — deterministic."""
    return uuid5(_DEMO_SEAT_NAMESPACE, f"gt-demo-seat:{role}:{agent_id or ''}")


@router.post("/auth/demo-token", response_model=DemoTokenResponse)
def mint_demo_token(
    body: DemoTokenRequest,
    settings: SettingsDep,
    params: ParamsDep,
) -> DemoTokenResponse:
    """Mint a signed seat JWT for the demo — the demo-auth bridge (B1 task 5a).

    Fail-closed gating, in order:

    - ``AUTH_MODE != "demo"`` ⇒ **404**: the endpoint does NOT exist in a live
      deployment (real Supabase issues tokens; we never mint demo seats against it).
    - no ``SUPABASE_JWT_SECRET`` configured ⇒ **503**: nothing to sign with — fail
      loud, never return a blank/unsigned token.
    - ``role`` not in ``params.rbac.roles`` ⇒ **422**: not a valid seat.

    On success it signs ``{sub, app_metadata: {role[, agent_id]}, iat, exp}`` with
    the configured secret (the same HS256 ``get_principal`` verifies) and a lifetime
    of ``params.rbac.demo_token_ttl_seconds`` (INV-11 — the single TTL home). The
    minted token round-trips through ``get_principal`` unchanged.
    """
    if settings.auth_mode != "demo":
        # The endpoint is ABSENT in live mode — a real deployment issues its own
        # Supabase JWTs; 404 (not 403) so it reads as "no such route".
        raise HTTPException(status_code=404, detail="Not Found")

    if settings.supabase_jwt_secret is None:
        # No verifying/signing secret ⇒ we cannot mint a token the gate would accept.
        raise HTTPException(status_code=503, detail="JWT secret not configured; cannot mint token")

    if body.role not in params.rbac.roles:
        raise HTTPException(
            status_code=422,
            detail=f"unknown role {body.role!r}; must be one of {params.rbac.roles!r}",
        )

    ttl = params.rbac.demo_token_ttl_seconds
    now = int(datetime.now(UTC).timestamp())
    app_metadata: dict[str, object] = {"role": body.role}
    if body.agent_id is not None:
        app_metadata["agent_id"] = str(body.agent_id)

    claims: dict[str, object] = {
        "sub": str(_demo_seat_sub(body.role, body.agent_id)),
        "iat": now,
        "exp": now + ttl,
        "app_metadata": app_metadata,
    }
    token = sign_hs256(claims, secret=settings.supabase_jwt_secret)
    return DemoTokenResponse(access_token=token, token_type="bearer", expires_in=ttl)

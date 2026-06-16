"""Cross-account RLS regression — THREAT_MODEL.md §6, D-RLS-5 (INV-5).

The product disclosed a CWE-639 / IDOR (a ``public``-schema table with RLS never
enabled, behind the anon key). This test is the LIVE guard that the cure holds —
the headline "we tested the exact IDOR closed" artifact (TODO.md S14 W1).

It runs entirely over **httpx → Supabase PostgREST / Auth Admin** (no
``supabase`` python client, no new dep — the house pattern of the live HubSpot
adapter). When ``SUPABASE_URL`` is set it provisions two synthetic auth users (A
and B) via the Auth Admin API, seeds a ``family_record`` owned by B with the
``service_role`` key, then proves the owner-scoped, null-guarded policy set:

* account A (authenticated, its own JWT) SELECTs ``family_record`` → **0 rows**
  (the IDOR closed — A cannot read B's row; D-RLS-5),
* the **anon** key SELECTs → **0 rows** (deny-by-default; D-RLS-3),
* the **service_role** key SELECTs → **sees B's row** (the server-only
  cross-family read path; D-RLS-4).

Every identity is synthetic (``@example.invalid``) so the PII-scan stays green
(INV-1). When no live Supabase is configured the test **skips with a marker**
(ASSUMPTIONS.md A-3) so CI without creds still passes; the static invariant guard
(``tests/unit/test_migrations_rls.py``) runs unconditionally with no DB.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

# Synthetic identities — @example.invalid satisfies the email CHECK constraint
# (0003) and the PII-scan's synthetic-domain allowance (INV-1).
# Synthetic test credential, not a real secret.
_PASSWORD = "Synthetic-RLS-Probe-123!"  # noqa: S105
_EMAIL_A = "rls-regression-a@example.invalid"
_EMAIL_B = "rls-regression-b@example.invalid"
# A fixed, recognizably-synthetic family_id for B's seeded row (easy teardown).
_SEED_FAMILY_ID = "00000000-0000-0000-0000-0000000000b0"


def _admin_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def _ensure_user(client: httpx.Client, service_key: str, email: str) -> str:
    """Create (or find) a confirmed synthetic auth user; return its user_id.

    Uses the Auth Admin API with the service_role key. Idempotent: a re-run reuses
    the existing user rather than failing on the unique-email constraint.
    """
    created = client.post(
        "/auth/v1/admin/users",
        headers=_admin_headers(service_key),
        json={"email": email, "password": _PASSWORD, "email_confirm": True},
    )
    if created.status_code == 200:
        return str(created.json()["id"])
    # Already exists (or similar) — look it up via the admin list endpoint.
    listed = client.get(
        "/auth/v1/admin/users",
        headers=_admin_headers(service_key),
        params={"per_page": "200"},
    )
    listed.raise_for_status()
    for user in listed.json().get("users", []):
        if user.get("email") == email:
            return str(user["id"])
    raise AssertionError(f"could not provision or find synthetic user {email!r}: {created.text}")


def _sign_in(client: httpx.Client, anon_key: str, email: str) -> str:
    """Password-grant sign-in → the user's access token (the authenticated JWT)."""
    response = client.post(
        "/auth/v1/token",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        params={"grant_type": "password"},
        json={"email": email, "password": _PASSWORD},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _select_family_record(
    client: httpx.Client, *, apikey: str, bearer: str
) -> list[dict[str, Any]]:
    """A PostgREST SELECT * on family_record under the given key/bearer pair."""
    response = client.get(
        "/rest/v1/family_record",
        headers={
            "apikey": apikey,
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json",
        },
        params={"select": "*"},
    )
    response.raise_for_status()
    body: Any = response.json()
    assert isinstance(body, list)
    return body


def test_foreign_read_returns_zero_rows() -> None:
    """A foreign SELECT under authenticated/anon returns 0 rows; service_role sees it.

    The live proof of the closed IDOR (D-RLS-5). Skipped when no live Supabase is
    configured (A-3).
    """
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not supabase_url or supabase_url.startswith("<"):
        pytest.skip("no SUPABASE_URL — RLS regression requires a live Supabase")

    anon_key = os.environ["SUPABASE_ANON_KEY"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    with httpx.Client(base_url=supabase_url, timeout=30.0) as client:
        # Provision two synthetic accounts (idempotent).
        _ensure_user(client, service_key, _EMAIL_A)
        user_b_id = _ensure_user(client, service_key, _EMAIL_B)
        account_a_jwt = _sign_in(client, anon_key, _EMAIL_A)

        # service_role (BYPASSRLS, server-only — D-RLS-4) seeds a row owned by B.
        # Clean any leftover from a prior run first so the seed is deterministic.
        client.request(
            "DELETE",
            "/rest/v1/family_record",
            headers=_admin_headers(service_key),
            params={"family_id": f"eq.{_SEED_FAMILY_ID}"},
        )
        seeded = client.post(
            "/rest/v1/family_record",
            headers={**_admin_headers(service_key), "Prefer": "return=representation"},
            json={
                "family_id": _SEED_FAMILY_ID,
                "user_id": user_b_id,
                "display_name": "The Synthetic-B Family",
                "primary_contact_synthetic_email": "synthetic-b@example.invalid",
                "current_stage": "interest",
                "attribution_source": "rls-regression-seed",
            },
        )
        assert seeded.status_code in (200, 201), f"seed of B's row failed: {seeded.text}"

        try:
            # (1) Account A (authenticated) reads family_record with its OWN JWT.
            #     RLS must return zero rows — A cannot see B's row (D-RLS-2/3/5).
            as_a = _select_family_record(client, apikey=anon_key, bearer=account_a_jwt)
            foreign = [r for r in as_a if r.get("user_id") == user_b_id]
            assert foreign == [], (
                f"IDOR REGRESSION (D-RLS-5): account A read {len(foreign)} of account B's "
                f"family_record rows under the authenticated role — RLS failed"
            )

            # (2) The anon (unauthenticated) key reads → zero rows (D-RLS-3).
            as_anon = _select_family_record(client, apikey=anon_key, bearer=anon_key)
            assert as_anon == [], (
                f"anon SELECT returned {len(as_anon)} rows — deny-by-default broken (D-RLS-3)"
            )

            # (3) service_role (BYPASSRLS) SEES B's row — the server-only read path.
            as_service = _select_family_record(client, apikey=service_key, bearer=service_key)
            assert any(r.get("family_id") == _SEED_FAMILY_ID for r in as_service), (
                "service_role could not read the seeded row — "
                "the cockpit read path is broken (D-RLS-4)"
            )
        finally:
            client.request(
                "DELETE",
                "/rest/v1/family_record",
                headers=_admin_headers(service_key),
                params={"family_id": f"eq.{_SEED_FAMILY_ID}"},
            )

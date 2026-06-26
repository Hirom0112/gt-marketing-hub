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

# A1 cross-program regression (PLAN_v2 §A1) — a principal in program A and the two
# program-tagged rows it (co-)owns: one in its own program (visible), one in another
# program (invisible despite ownership — the RESTRICTIVE program policy, 0024).
_EMAIL_PROG = "rls-regression-program@example.invalid"
_PROGRAM_A = "fall_enrollment"
_PROGRAM_B = "summer_camp"
_SEED_FAMILY_ID_PROG_A = "00000000-0000-0000-0000-0000000000a0"
_SEED_FAMILY_ID_PROG_B = "00000000-0000-0000-0000-0000000000c0"


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


def _ensure_user_in_program(
    client: httpx.Client, service_key: str, email: str, program_id: str
) -> str:
    """Create (or find) a synthetic user whose ``app_metadata.program_id`` is set.

    The RESTRICTIVE program policy (0024) reads ``auth.jwt() -> 'app_metadata' ->>
    'program_id'``, so the principal must carry the claim. Idempotent: a re-run
    updates the existing user's ``app_metadata`` (the program may have changed).
    """
    created = client.post(
        "/auth/v1/admin/users",
        headers=_admin_headers(service_key),
        json={
            "email": email,
            "password": _PASSWORD,
            "email_confirm": True,
            "app_metadata": {"program_id": program_id},
        },
    )
    if created.status_code == 200:
        return str(created.json()["id"])
    user_id = _ensure_user(client, service_key, email)
    # Already existed — force the program claim to the wanted value (PUT is idempotent).
    client.put(
        f"/auth/v1/admin/users/{user_id}",
        headers=_admin_headers(service_key),
        json={"app_metadata": {"program_id": program_id}},
    )
    return user_id


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


def test_cross_program_read_is_isolated() -> None:
    """A program-A principal reads its program-A row but ZERO program-B rows (A1).

    The live proof of program isolation (0024 RESTRICTIVE policy on the
    ``app_metadata.program_id`` claim): a principal whose JWT claims
    ``program_id='fall_enrollment'`` co-owns two rows — one tagged ``fall_enrollment``
    and one tagged ``summer_camp``. Ownership is identical, so ONLY the program tag
    differs: the program-A row is visible, the program-B row is invisible (the
    RESTRICTIVE policy is AND-ed on top of the owner policy — isolation tightens,
    never loosens). Skipped when no live Supabase is configured (A-3).
    """
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not supabase_url or supabase_url.startswith("<"):
        pytest.skip("no SUPABASE_URL — cross-program regression requires a live Supabase")

    anon_key = os.environ["SUPABASE_ANON_KEY"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    with httpx.Client(base_url=supabase_url, timeout=30.0) as client:
        # A principal whose JWT carries app_metadata.program_id = fall_enrollment.
        user_id = _ensure_user_in_program(client, service_key, _EMAIL_PROG, _PROGRAM_A)
        jwt = _sign_in(client, anon_key, _EMAIL_PROG)

        # service_role seeds two rows the principal OWNS, differing ONLY in program_id.
        for seed_id, program_id in (
            (_SEED_FAMILY_ID_PROG_A, _PROGRAM_A),
            (_SEED_FAMILY_ID_PROG_B, _PROGRAM_B),
        ):
            client.request(
                "DELETE",
                "/rest/v1/family_record",
                headers=_admin_headers(service_key),
                params={"family_id": f"eq.{seed_id}"},
            )
            seeded = client.post(
                "/rest/v1/family_record",
                headers={**_admin_headers(service_key), "Prefer": "return=representation"},
                json={
                    "family_id": seed_id,
                    "user_id": user_id,
                    "display_name": f"The Synthetic {program_id} Family",
                    "primary_contact_synthetic_email": "synthetic-prog@example.invalid",
                    "current_stage": "interest",
                    "attribution_source": "rls-regression-seed",
                    "program_id": program_id,
                },
            )
            assert seeded.status_code in (200, 201), (
                f"seed of {program_id} row failed: {seeded.text}"
            )

        try:
            rows = _select_family_record(client, apikey=anon_key, bearer=jwt)
            ids = {r.get("family_id") for r in rows}
            # The program-A row IS visible (owner AND in-program both hold).
            assert _SEED_FAMILY_ID_PROG_A in ids, (
                "program-A principal could not read its OWN program-A row — the "
                "RESTRICTIVE program policy is over-blocking (A1)"
            )
            # The program-B row is INVISIBLE despite identical ownership (the isolation).
            assert _SEED_FAMILY_ID_PROG_B not in ids, (
                f"CROSS-PROGRAM LEAK (A1): a fall_enrollment principal read the "
                f"summer_camp row {_SEED_FAMILY_ID_PROG_B} — RESTRICTIVE isolation failed"
            )
        finally:
            for seed_id in (_SEED_FAMILY_ID_PROG_A, _SEED_FAMILY_ID_PROG_B):
                client.request(
                    "DELETE",
                    "/rest/v1/family_record",
                    headers=_admin_headers(service_key),
                    params={"family_id": f"eq.{seed_id}"},
                )

"""CORS middleware contract — the browser cross-origin guard (TECH_STACK §5.1).

The React app runs on a SEPARATE origin (the Vite dev server / built host), so
every front-end `fetch` is cross-origin. Without an explicit allow-list the
browser blocks the response ("Load failed") even though the API answers 200.
These tests assert the app echoes `Access-Control-Allow-Origin` for an allowed
origin and does NOT for a disallowed one — proving the seam is wired and not a
wildcard. Origins come from the typed env seam (`GT_CORS_ALLOW_ORIGINS`, INV-11).
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

ALLOWED = "http://localhost:5173"
DISALLOWED = "http://evil.example"


def _client() -> TestClient:
    """A fresh app instance (CORS origins are read at construction)."""
    import app.main as main
    from tests.conftest import install_test_principal_override

    importlib.reload(main)
    # The reload builds a BRAND-NEW app with empty dependency_overrides, so the
    # autouse conftest principal shim (pinned to the original app) does not apply to
    # it — install the token-aware shim here so the owner-scoped /seam probe answers
    # 200 (admin-on-no-token) instead of the production default-deny 401.
    install_test_principal_override(app=main.app)
    return TestClient(main.app)


def test_allowed_origin_gets_cors_header() -> None:
    """A request from an allowed origin gets it echoed in Access-Control-Allow-Origin."""
    client = _client()
    resp = client.get("/seam", headers={"Origin": ALLOWED})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED


def test_preflight_allows_post_from_allowed_origin() -> None:
    """A CORS preflight (OPTIONS) from an allowed origin is permitted."""
    client = _client()
    resp = client.options(
        "/ai/enrollment/draft",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == ALLOWED


def test_disallowed_origin_gets_no_cors_header() -> None:
    """A disallowed origin is NOT echoed — the allow-list is not a wildcard."""
    client = _client()
    resp = client.get("/seam", headers={"Origin": DISALLOWED})
    # The endpoint still answers (CORS is browser-enforced), but the API must not
    # grant the disallowed origin an ACAO header.
    assert resp.headers.get("access-control-allow-origin") != DISALLOWED

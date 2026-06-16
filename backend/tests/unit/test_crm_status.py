"""GET /crm/status acceptance tests — surface the HubSpot kill switch (S14 W4).

The read-only CRM seam window the operator UI reads to fail closed (INV-3 pattern;
INV-8 kill switch). These assert the endpoint reports a state CONSISTENT with the
one canonical registry precedence (`effective_crm_mode`):

  * ``CRM_MODE=simulate`` ⇒ ``effective_mode='simulate'``, ``token_configured`` False.
  * ``CRM_MODE=live`` + token + kill switch ⇒ ``effective_mode='simulate'`` (guard 3
    degrades; INV-8), ``kill_switch`` True — the live-push UI fails closed.
  * ``CRM_MODE=live`` + token + no kill switch ⇒ ``effective_mode='live'``.

And the security invariant: NO secret/token value ever appears in the response body
(``token_configured`` is a bool; the token string is never surfaced).

The endpoint reads the cached settings via ``get_settings_dep``; the env seam is a
once-at-import singleton, so each case overrides that dependency with a fresh
``Settings.from_env()`` (after ``monkeypatch.setenv``) — the standard override style
(matching tests/unit/test_evals_scoreboard_endpoints.py + the registry tests' env
monkeypatching). Fully offline (INV-9): no token is a real secret, no live call.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.settings import Settings
from app.main import app

client = TestClient(app)

# A synthetic, NON-secret token value used only to assert it is NEVER echoed back in
# the response body. Deliberately NOT shaped like a real HubSpot `pat-na…` token so
# the PII/secret scanner doesn't flag the fixture (NFR-1; no live call is ever made).
_FAKE_TOKEN = "synthetic-crm-token-not-a-real-secret-0000"


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Clear dependency overrides around each test (test isolation)."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _override_settings_from_env() -> None:
    """Bind the status endpoint's settings dep to a FRESH env read (post-monkeypatch).

    Production reads the §5 env seam once at import (``deps.get_settings_dep`` is a
    cached singleton); a test that flips env vars must override it so the freshly
    built snapshot is what the route sees — never the import-time cache.
    """
    app.dependency_overrides[deps.get_settings_dep] = Settings.from_env


def test_simulate_mode_effective_simulate_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=simulate ⇒ effective_mode simulate, token_configured False, kill off."""
    monkeypatch.setenv("CRM_MODE", "simulate")
    monkeypatch.delenv("HUBSPOT_PRIVATE_APP_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_KILL_SWITCH", raising=False)
    _override_settings_from_env()

    resp = client.get("/crm/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["crm_mode"] == "simulate"
    assert data["effective_mode"] == "simulate"
    assert data["token_configured"] is False
    assert data["kill_switch"] is False


def test_live_token_kill_switch_degrades_to_simulate(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=live + token + kill switch ON ⇒ effective_mode simulate (guard 3, INV-8)."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("HUBSPOT_KILL_SWITCH", "true")
    _override_settings_from_env()

    resp = client.get("/crm/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["crm_mode"] == "live"
    assert data["kill_switch"] is True
    # Fail closed: the kill switch degrades the EFFECTIVE mode to simulate even
    # though CRM_MODE=live, so the UI disables the live-push control (INV-3/INV-8).
    assert data["effective_mode"] == "simulate"
    assert data["token_configured"] is True


def test_live_token_no_kill_switch_effective_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=live + token + kill switch OFF ⇒ effective_mode live (the live adapter)."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", _FAKE_TOKEN)
    monkeypatch.delenv("HUBSPOT_KILL_SWITCH", raising=False)
    _override_settings_from_env()

    resp = client.get("/crm/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["crm_mode"] == "live"
    assert data["effective_mode"] == "live"
    assert data["kill_switch"] is False
    assert data["token_configured"] is True


def test_no_token_value_ever_in_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """SECURITY: the token string is NEVER surfaced — only the token_configured bool."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", _FAKE_TOKEN)
    monkeypatch.delenv("HUBSPOT_KILL_SWITCH", raising=False)
    _override_settings_from_env()

    resp = client.get("/crm/status")

    assert resp.status_code == 200
    # The raw response text must not leak the secret anywhere (key OR value).
    assert _FAKE_TOKEN not in resp.text
    data = resp.json()
    assert "hubspot_private_app_token" not in data
    assert "token" not in {k for k in data if k != "token_configured"}
    # token_configured is a plain bool, never the secret value.
    assert data["token_configured"] is True
    assert isinstance(data["token_configured"], bool)


def test_calls_per_run_cap_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The INV-8 per-run call cap is surfaced (an int the operator can see)."""
    monkeypatch.setenv("CRM_MODE", "simulate")
    monkeypatch.setenv("HUBSPOT_CALLS_PER_RUN_CAP", "150")
    _override_settings_from_env()

    resp = client.get("/crm/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["calls_per_run_cap"] == 150
    assert isinstance(data["calls_per_run_cap"], int)

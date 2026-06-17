"""M7 security/observability — the edge-middleware detection feed + live RLS posture.

MULTI_AGENT_COCKPIT.md §3/§7. Three properties under test:

  * ``test_signals_recorded`` — the FastAPI edge middleware (DETECTION, never inline
    blocking) records a ``security_event`` for each signal class, each carrying the
    EXACT §7 OWASP category id.
  * ``test_posture_flips_red_without_force`` — ``GET /security/posture`` is green on
    the real migrations and flips RED when a table loses its FORCE line.
  * (smoke) the events feed labels the v1 stream ``simulated`` (INV-9) and the
    acknowledge action flips a row.

The middleware is exercised against a minimal probe app (the standard FastAPI
middleware unit-test pattern) so each signal class is driven deterministically; the
SAME middleware is mounted on the real ``app`` in ``app/main.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_security_event_log, reset_security_event_log
from app.api.security import SecurityEdgeMiddleware
from app.core.params import load_params
from app.main import app
from app.observability.security_log import (
    OWASP_BY_SIGNAL,
    InMemorySecurityEventLog,
    SecuritySignal,
)

_PARAMS = load_params(Path(__file__).resolve().parents[3] / "params" / "params.example.yaml")


def _probe_app(log: InMemorySecurityEventLog) -> TestClient:
    """A minimal app carrying the SAME edge middleware, with probe routes per signal.

    The middleware records into the injected ``log``; the probe routes produce the
    raw request/response shapes the middleware classifies (a 403, an anon admin-route
    hit, an oversized list, a user_id-reassign body) so each signal class is driven
    deterministically.
    """
    probe = FastAPI()
    probe.add_middleware(SecurityEdgeMiddleware, log=log, params=_PARAMS)

    @probe.get("/protected/{obj_id}")
    def protected(obj_id: str) -> JSONResponse:
        # A foreign object read denied at the boundary ⇒ 403.
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    @probe.get("/admin/secret")
    def admin_secret() -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    @probe.get("/things")
    def things(n: int = 0) -> list[dict[str, int]]:
        return [{"i": i} for i in range(n)]

    @probe.patch("/things/{obj_id}")
    def patch_thing(obj_id: str, body: dict[str, object]) -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(probe, raise_server_exceptions=True)


def test_signals_recorded() -> None:
    """The edge middleware records a security_event per signal class with the §7 OWASP id."""
    log = InMemorySecurityEventLog()
    client = _probe_app(log)

    # --- API4:2023 oversized result (enumeration / wide-band pull) ---
    over = _PARAMS.security.oversized_result_rows
    resp = client.get(f"/things?n={over}")
    assert resp.status_code == 200

    # --- API3:2023 user_id-reassign attempt (broken object property level auth) ---
    resp = client.patch("/things/abc", json={"user_id": "someone-elses-uid"})
    assert resp.status_code == 200

    # --- API5:2023 anon hitting an admin/service route (broken function level auth) ---
    resp = client.get("/admin/secret")  # no X-Demo-Role ⇒ anon
    assert resp.status_code == 403

    # --- A07:2021 auth-failure burst (forged token / brute force) ---
    burst = _PARAMS.security.auth_failure_burst
    for _ in range(burst):
        client.get("/protected/x")

    events = log.list_events()
    by_signal = {e.signal: e for e in events}

    # Each signal class was recorded, with the EXACT §7 OWASP category id.
    assert SecuritySignal.OVERSIZED_RESULT in by_signal
    assert by_signal[SecuritySignal.OVERSIZED_RESULT].owasp == "API4:2023"
    assert OWASP_BY_SIGNAL[SecuritySignal.OVERSIZED_RESULT] == "API4:2023"

    assert SecuritySignal.USER_ID_REASSIGN_ATTEMPT in by_signal
    assert by_signal[SecuritySignal.USER_ID_REASSIGN_ATTEMPT].owasp == "API3:2023"

    assert SecuritySignal.ANON_ADMIN_ROUTE in by_signal
    assert by_signal[SecuritySignal.ANON_ADMIN_ROUTE].owasp == "API5:2023"

    assert SecuritySignal.AUTH_FAILURE_BURST in by_signal
    assert by_signal[SecuritySignal.AUTH_FAILURE_BURST].owasp == "A07:2021"

    # Foreign-object BOLA id is the documented §7 mapping (A01:2021 / CWE-639).
    assert OWASP_BY_SIGNAL[SecuritySignal.FOREIGN_OBJECT_READ] == "API1:2023"


def test_posture_flips_red_without_force() -> None:
    """GET /security/posture is green on the real migrations, RED when a table loses FORCE."""
    client = TestClient(app)

    # Green on the real committed migrations.
    resp = client.get("/security/posture")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["green"] is True, body
    # Every named check passed.
    assert all(check["passed"] for check in body["checks"]), body

    # The posture core flips RED when a table loses its FORCE line (a regression).
    from app.core.security_posture import evaluate_posture

    doctored = [
        "CREATE TABLE foo (id uuid PRIMARY KEY);",
        "ALTER TABLE foo ENABLE ROW LEVEL SECURITY;",
        # NOTE: no FORCE line — the regression.
        "CREATE POLICY foo_sel ON foo FOR SELECT USING ((SELECT auth.uid()) IS NOT NULL);",
    ]
    result = evaluate_posture(doctored)
    assert result.green is False
    force_check = next(c for c in result.checks if c.name == "every_table_forces_rls")
    assert force_check.passed is False

    # And a clean injected set (table + ENABLE + FORCE + null-guarded policy) is green.
    clean = [
        "CREATE TABLE bar (id uuid PRIMARY KEY);",
        "ALTER TABLE bar ENABLE ROW LEVEL SECURITY;",
        "ALTER TABLE bar FORCE ROW LEVEL SECURITY;",
        "CREATE POLICY bar_sel ON bar FOR SELECT USING ((SELECT auth.uid()) IS NOT NULL);",
    ]
    assert evaluate_posture(clean).green is True


def test_events_feed_is_labeled_simulated_and_acknowledge_flips() -> None:
    """GET /security/events returns the labeled simulated feed; POST ack flips a row (§7)."""
    reset_security_event_log()
    client = TestClient(app)

    resp = client.get("/security/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True, "the v1 feed must be labeled simulated (INV-9)"
    events = body["events"]
    assert events, "the seeded simulated feed must be non-empty"
    # Every seeded row is labeled simulated and carries a non-empty OWASP id.
    for ev in events:
        assert ev["simulated"] is True
        assert ev["owasp"]
        assert ev["acknowledged"] is False

    # Acknowledge the first event ⇒ it flips to acknowledged.
    event_id = events[0]["event_id"]
    ack = client.post(f"/security/events/{event_id}/acknowledge")
    assert ack.status_code == 200, ack.text
    assert ack.json()["acknowledged"] is True

    # Unknown id ⇒ 404.
    missing = client.post("/security/events/00000000-0000-4000-8000-000000000000/acknowledge")
    assert missing.status_code == 404

    reset_security_event_log()


def teardown_module() -> None:
    """Restore the security-event feed singleton after the module's tests."""
    reset_security_event_log()
    app.dependency_overrides.pop(get_security_event_log, None)

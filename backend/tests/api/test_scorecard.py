"""Weekly KPI scorecard endpoint tests (B5) — ``GET /scorecard/weekly``.

The route composes the API-layer weekly SERIES (the observability log bucketed by
ISO week into per-metric weekly counts) with the pure
:func:`app.core.weekly_scorecard.build_weekly_scorecard` transform. These tests pin
the two contracts the brief asks for:

- the DELTA invariant — every metric's ``delta == this_week - last_week`` (the worked
  invariant the pure core owns), and ``as_of`` is present; and
- the WEEK bucketing — seeding proposals across TWO ISO weeks (this week + last week)
  makes the ``proposals`` metric's ``this_week``/``last_week`` equal the seeded counts.

Auth: the scorecard is identical for everyone, so the route is gated only by
``Depends(get_principal)`` (any authenticated seat). The no-token case must 401 — the
S1 default-DENY — so that test pops the conftest admin-on-no-token shim and runs the
real verifier with the test secret configured (mirrors ``test_principal``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.core.settings import Settings
from app.main import app
from app.observability.log_store import DecisionAction, InMemoryObservabilityLog
from tests.api._jwt import TEST_JWT_SECRET, mint_jwt
from tests.conftest import install_test_principal_override

client = TestClient(app)

# The three canonical log-derived metrics the series builder exposes.
_EXPECTED_KEYS = {"proposals", "evals_passed", "approvals"}


def _auth(role: str = "leader") -> dict[str, str]:
    """An ``Authorization: Bearer`` header carrying a signed ``role`` JWT (B1)."""
    return {"Authorization": f"Bearer {mint_jwt(role=role, secret=TEST_JWT_SECRET)}"}


def _seed_log(*, n_this: int, n_last: int) -> InMemoryObservabilityLog:
    """A fresh log with proposals (+ passing eval + approve) across TWO ISO weeks.

    ``n_this`` proposals dated *now* (this ISO week) and ``n_last`` dated exactly a
    week earlier (last ISO week) — seven days apart is always a distinct ISO week, so
    the bucketing is deterministic regardless of which weekday the suite runs on.
    """
    log = InMemoryObservabilityLog()
    now = datetime.now(UTC)
    for when, count in ((now, n_this), (now - timedelta(days=7), n_last)):
        for _ in range(count):
            pid = uuid4()
            log.log_proposal(
                proposal_id=pid,
                flow="enrollment_draft",
                schema_version="1",
                payload={},
                created_at=when,
            )
            log.log_eval(proposal_id=pid, eval_name="grounding", passed=True, created_at=when)
            log.log_decision(
                proposal_id=pid, human="op", action=DecisionAction.APPROVE, created_at=when
            )
    return log


def _get_weekly(log: InMemoryObservabilityLog) -> dict:
    """Call the route against a seeded log override, returning the JSON body."""
    app.dependency_overrides[deps.get_observability_log] = lambda: log
    try:
        resp = client.get("/scorecard/weekly", headers=_auth())
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        app.dependency_overrides.pop(deps.get_observability_log, None)


def test_weekly_scorecard_metrics_and_delta_invariant() -> None:
    """200, the expected metric keys, ``as_of`` present, and delta == this - last."""
    body = _get_weekly(_seed_log(n_this=3, n_last=2))

    assert "as_of" in body and body["as_of"]
    metrics = body["metrics"]
    assert {m["key"] for m in metrics} == _EXPECTED_KEYS

    for m in metrics:
        assert "this_week" in m and "last_week" in m and "delta" in m
        # The worked invariant the pure core owns — asserted for EVERY metric row.
        assert m["delta"] == m["this_week"] - m["last_week"]


def test_proposals_week_bucketing_matches_seeded_counts() -> None:
    """The ``proposals`` metric's this/last week equal the seeded per-week counts."""
    body = _get_weekly(_seed_log(n_this=3, n_last=2))

    proposals = next(m for m in body["metrics"] if m["key"] == "proposals")
    assert proposals["this_week"] == 3
    assert proposals["last_week"] == 2
    assert proposals["delta"] == 1


def test_no_token_unauthorized() -> None:
    """No bearer token → 401 (the S1 default-DENY; the scorecard still needs a seat)."""
    # Pop the conftest admin-on-no-token shim and run the REAL verifier with the test
    # secret configured, so the missing-token path reaches the production default-deny.
    app.dependency_overrides.pop(deps.get_principal, None)
    app.dependency_overrides[deps.get_settings_dep] = lambda: Settings(
        supabase_jwt_secret=TEST_JWT_SECRET
    )
    try:
        resp = client.get("/scorecard/weekly")
        assert resp.status_code == 401, resp.text
    finally:
        install_test_principal_override()

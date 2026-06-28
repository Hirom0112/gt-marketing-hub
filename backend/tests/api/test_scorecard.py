"""Weekly KPI scorecard endpoint tests (B5/B6) — ``GET /scorecard/weekly``.

The route samples the nine business KPIs the product spec lists from their real
sources, reshapes them with the pure
:func:`app.core.weekly_scorecard.build_weekly_scorecard` transform, and attaches a
:class:`app.core.metric_provenance.MetricProvenance` descriptor to every metric. These
tests pin the contracts the brief asks for:

- the NINE KPIs are present, each with the core's fields + the delta invariant
  (``delta == this_week - last_week``) and ``as_of``;
- every metric carries a ``provenance`` object with the five descriptor fields and a
  valid ``kind``;
- the uninstrumented row (``event_to_consult``) is labeled, and the stood-in rows are
  labeled stood-in;
- the spec-default targets are surfaced; and
- the canonical provenance map and the rendered metric keys agree (no drift).

Auth: the scorecard is identical for everyone, so the route is gated only by
``Depends(get_principal)`` (any authenticated seat). The autouse conftest shim returns
an admin principal when no token is sent; the no-token 401 case pops that shim and runs
the real verifier with the test secret configured (mirrors ``test_principal``).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import EngagementSnapshot, SimulatedCRMAdapter
from app.api import deps
from app.core.metric_provenance import (
    KIND_DERIVED,
    KIND_OUR_DB,
    KIND_STOOD_IN,
    KIND_UNINSTRUMENTED,
    PROVENANCE,
)
from app.core.settings import Settings
from app.main import app
from app.observability.log_store import (
    ContactChannel,
    ContactDisposition,
    ObjectionReason,
)
from tests.api._jwt import TEST_JWT_SECRET
from tests.conftest import install_test_principal_override

client = TestClient(app)

# The nine business KPIs the spec lists (the canonical key set).
_EXPECTED_KEYS = {
    "applicants",
    "deposits",
    "conversion_top_channel",
    "engagement_clicked",
    "followup_sla",
    "objections",
    "ambassador_enrollments",
    "handoffs",
    "event_to_consult",
}

# The valid provenance kinds the descriptor may carry.
_VALID_KINDS = {KIND_OUR_DB, KIND_DERIVED, KIND_STOOD_IN, KIND_UNINSTRUMENTED, "live"}


def _get_weekly() -> dict:
    """Call the route (autouse admin shim supplies the seat), returning the JSON body."""
    resp = client.get("/scorecard/weekly")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_key(body: dict, key: str) -> dict:
    return next(m for m in body["metrics"] if m["key"] == key)


def test_nine_kpis_and_delta_invariant() -> None:
    """200, the nine KPI keys, ``as_of`` present, and delta == this - last per row."""
    body = _get_weekly()

    assert "as_of" in body and body["as_of"]
    metrics = body["metrics"]
    assert {m["key"] for m in metrics} == _EXPECTED_KEYS

    for m in metrics:
        assert "this_week" in m and "last_week" in m and "delta" in m
        # The worked invariant the pure core owns — asserted for EVERY metric row.
        assert m["delta"] == m["this_week"] - m["last_week"]
        # A single-point snapshot ⇒ last_week is the honest 0.0 (no fabricated trend).
        assert m["last_week"] == 0.0


def test_every_metric_has_provenance() -> None:
    """Each KPI carries a provenance object with the five fields + a valid kind."""
    body = _get_weekly()

    for m in body["metrics"]:
        prov = m["provenance"]
        assert set(prov) == {"system", "locator", "kind", "compute", "last_sync"}
        assert prov["kind"] in _VALID_KINDS
        assert prov["system"] and prov["compute"]
        # last_sync is not yet wired to a watermark — null for every row in v1.
        assert prov["last_sync"] is None


def test_uninstrumented_and_stood_in_rows_are_labeled() -> None:
    """The event-to-consult row reads uninstrumented; the remaining proxy reads stood_in."""
    body = _get_weekly()

    event = _by_key(body, "event_to_consult")
    assert event["provenance"]["kind"] == KIND_UNINSTRUMENTED
    assert event["this_week"] == 0.0

    # Ambassador enrollments remains a labeled proxy (enrollment attribution untracked).
    assert _by_key(body, "ambassador_enrollments")["provenance"]["kind"] == KIND_STOOD_IN

    # The real reads are labeled as our_db / derived, never stood-in.
    assert _by_key(body, "applicants")["provenance"]["kind"] == KIND_OUR_DB
    assert _by_key(body, "conversion_top_channel")["provenance"]["kind"] == KIND_DERIVED


def test_engagement_clicked_is_seam_sourced_not_stood_in() -> None:
    """Engagement-tier (clicked) is now DERIVED from the CRM engagement seam, not stood_in.

    Overrides the CRM adapter with a fake returning a known snapshot and asserts the KPI
    reflects the seam's clicked share (so the number comes from the seam, not a hardcode).
    """
    prov = PROVENANCE["engagement_clicked"]
    assert prov.kind == KIND_DERIVED
    assert prov.kind != KIND_STOOD_IN

    class _FakeEngagementCRM(SimulatedCRMAdapter):
        def read_engagement(self, family_ids: object) -> EngagementSnapshot:
            return EngagementSnapshot(total=4, clicked=1)  # a known 0.25 share

    app.dependency_overrides[deps.get_crm_adapter_dep] = _FakeEngagementCRM
    try:
        body = _get_weekly()
        row = _by_key(body, "engagement_clicked")
        assert row["provenance"]["kind"] == KIND_DERIVED
        assert row["this_week"] == 0.25  # the seam's clicked_share, not a placeholder
    finally:
        app.dependency_overrides.pop(deps.get_crm_adapter_dep, None)


def test_objections_counted_from_contact_outcome_spine() -> None:
    """Objections logged is now an our_db COUNT off the contact-outcome spine, not stood_in.

    Logging an objection on a contact outcome increments the scorecard's objections KPI —
    the value comes from the seam, not a hardcoded stand-in.
    """
    prov = PROVENANCE["objections"]
    assert prov.kind == KIND_OUR_DB
    assert prov.kind != KIND_STOOD_IN

    deps.reset_observability_log()
    try:
        before = _by_key(_get_weekly(), "objections")["this_week"]
        log = deps.get_observability_log()
        log.log_contact_outcome(
            family_id=uuid4(),
            channel=ContactChannel.CALL,
            disposition=ContactDisposition.NOT_INTERESTED,
            human="rep",
            objection=ObjectionReason.PRICE,
        )
        # An outcome with NO objection must not be counted (only logged objections).
        log.log_contact_outcome(
            family_id=uuid4(),
            channel=ContactChannel.SMS,
            disposition=ContactDisposition.NO_ANSWER,
            human="rep",
        )
        after = _by_key(_get_weekly(), "objections")["this_week"]
        assert after == before + 1.0
    finally:
        deps.reset_observability_log()


def test_spec_default_targets_surfaced() -> None:
    """The spec-default targets ride through onto the rendered metric rows."""
    body = _get_weekly()
    assert _by_key(body, "deposits")["target"] == 180.0
    assert _by_key(body, "followup_sla")["target"] == 0.90
    assert _by_key(body, "conversion_top_channel")["target"] == 0.40
    assert _by_key(body, "ambassador_enrollments")["target"] == 30.0


def test_provenance_map_matches_rendered_keys() -> None:
    """The canonical provenance map is the single home — its keys match the rendered KPIs."""
    body = _get_weekly()
    rendered = {m["key"] for m in body["metrics"]}
    assert set(PROVENANCE) == rendered == _EXPECTED_KEYS


def test_weekly_carries_goal_date() -> None:
    """The pacing horizon (params goal_date) is surfaced for the Goal-pacing tab."""
    body = _get_weekly()
    assert body.get("goal_date"), "goal_date must be present for goal pacing"


def test_connector_freshness_roster() -> None:
    """/scorecard/connectors reports every source with a mode; stood-in sources labeled."""
    resp = client.get("/scorecard/connectors")
    assert resp.status_code == 200, resp.text
    connectors = resp.json()["connectors"]
    names = {c["name"] for c in connectors}
    # The real seams + our DB + the unreachable stood-in sources are all reported.
    assert {"Supabase", "HubSpot", "Stripe", "Meta Business Suite"} <= names
    for c in connectors:
        assert set(c) == {"name", "kind", "mode", "last_sync"}
        assert c["mode"] in {"live", "simulate", "stood_in"}
    # Supabase is our source of record (always live); Meta is an unreachable stand-in.
    supabase = next(c for c in connectors if c["name"] == "Supabase")
    assert supabase["kind"] == "our_db" and supabase["mode"] == "live"
    meta = next(c for c in connectors if c["name"] == "Meta Business Suite")
    assert meta["mode"] == "stood_in"


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

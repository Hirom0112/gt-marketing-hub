"""Unit tests for the Supabase-backed FamilyRepository (S14 W2; A-24).

These run against an ``httpx.MockTransport`` — no network, no live Supabase. They
prove the four contract methods over the PostgREST embed shape:

* a fresh interest lead (spine + lead only, no app_form) reads back at the
  DERIVED stage ``interest`` and appears in ``list_joined`` (A-24 M2),
* a family with a submitted ``app_form`` derives ``apply``,
* full forms + ``tuition_step_unlocked`` derives ``tuition``,
* a ``family_record`` with NO ``leads_new`` is EXCLUDED (the partial-invisible
  INNER-join rule — modeled by the ``!inner`` embed dropping it server-side),
* ``pipeline_counts`` groups by the DERIVED stage (stored ``current_stage`` is a
  stale placeholder and is intentionally wrong in the fixtures), and
* the drop-off views surface the right last-field / heatmap counts.

Every email is synthetic (``@example.invalid``) so the PII-scan stays green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest

from app.core.params import load_params
from app.data.models import FundingState, SeamStatus, Stage
from app.data.supabase_repository import (
    DropOffBucket,
    DropOffPoint,
    SupabaseError,
    SupabaseFamilyRepository,
)

_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# ---------------------------------------------------------------------------
# PostgREST-shaped fixtures. The cockpit read embeds the four source tables as
# nested LISTS (the FK is the non-unique family_id). The stored `current_stage`
# is deliberately WRONG here ("tuition" on a lead-only family) to prove the repo
# derives stage from the source rows, never the stored column (A-24 M2).
# ---------------------------------------------------------------------------

_FID_INTEREST = "00000000-0000-0000-0000-0000000000a1"
_FID_APPLY = "00000000-0000-0000-0000-0000000000a2"
_FID_TUITION = "00000000-0000-0000-0000-0000000000a3"


def _spine(family_id: str, *, current_stage: str = "tuition", **over: Any) -> dict[str, Any]:
    """A family_record row with the source-table embeds, defaults all empty (LEFT miss)."""
    row: dict[str, Any] = {
        "family_id": family_id,
        "user_id": None,
        "display_name": "Synthetic Family",
        "primary_contact_synthetic_email": "synthetic@example.invalid",
        "lead_id": None,
        "app_form_id": None,
        "enrollment_form_id": None,
        "community_profile_id": None,
        # Stored stage is a stale placeholder — intentionally inconsistent so a
        # test that read it (instead of deriving) would fail.
        "current_stage": current_stage,
        "stall_reason": None,
        "stalled_since": None,
        "funding_type": None,
        "funding_state": "none",
        "attribution_source": "web",
        "attribution_utm": {},
        "crm_seam_status": "unsynced",
        "crm_synced_at": None,
        "work_queue_score": None,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "leads_new": [],
        "app_form": [],
        "enrollment_forms": [],
        "community_profiles": [],
    }
    row.update(over)
    return row


def _lead(family_id: str) -> dict[str, Any]:
    return {
        "lead_id": "00000000-0000-0000-0000-0000000000c1",
        "family_id": family_id,
        "synthetic_first_name": "Syn",
        "synthetic_last_name": "Thetic",
        "synthetic_email": "synthetic@example.invalid",
        "synthetic_phone": "000",
        "source": "web",
        "utm": {},
        "product_interest": "anywhere",
        "grade_interest": "5",
        "region": "TX",
        # num_children intentionally OMITTED — the frozen cloud table lacks it, so
        # pydantic must fall back to its default of 1 (the real cloud behavior).
        "created_at": "2026-06-01T00:00:00+00:00",
    }


def _app_form(family_id: str, *, submitted: bool) -> dict[str, Any]:
    return {
        "app_form_id": "00000000-0000-0000-0000-0000000000d1",
        "family_id": family_id,
        "submitted_at": "2026-06-02T00:00:00+00:00" if submitted else None,
        "completion_pct": 100.0 if submitted else 40.0,
        "map_score": None,
        "academic_signals": {},
        "extracted_fields": {},
        "created_at": "2026-06-02T00:00:00+00:00",
    }


def _enrollment(family_id: str, *, signed: int, unlocked: bool) -> dict[str, Any]:
    return {
        "enrollment_form_id": "00000000-0000-0000-0000-0000000000e1",
        "family_id": family_id,
        "forms_total": 6,
        "forms_signed": signed,
        "forms_status": [],
        "tuition_step_unlocked": unlocked,
        "created_at": "2026-06-03T00:00:00+00:00",
    }


def _interest_row() -> dict[str, Any]:
    """Thin interest lead: spine + lead only, no app_form (must derive `interest`)."""
    return _spine(_FID_INTEREST, leads_new=[_lead(_FID_INTEREST)])


def _apply_row() -> dict[str, Any]:
    """Submitted app_form, no forms signed (must derive `apply`)."""
    return _spine(
        _FID_APPLY,
        current_stage="interest",  # stale placeholder, deliberately wrong.
        leads_new=[_lead(_FID_APPLY)],
        app_form=[_app_form(_FID_APPLY, submitted=True)],
    )


def _tuition_row() -> dict[str, Any]:
    """All forms signed + tuition unlocked (must derive `tuition`)."""
    return _spine(
        _FID_TUITION,
        current_stage="apply",  # stale placeholder, deliberately wrong.
        leads_new=[_lead(_FID_TUITION)],
        app_form=[_app_form(_FID_TUITION, submitted=True)],
        enrollment_forms=[_enrollment(_FID_TUITION, signed=6, unlocked=True)],
    )


def _make_repo(handler: Any) -> SupabaseFamilyRepository:
    """A repo whose injected httpx client routes every request to ``handler``."""
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://example.supabase.co")
    return SupabaseFamilyRepository(
        base_url="https://example.supabase.co",
        service_role_key="synthetic-service-role-key",
        params=load_params(_EXAMPLE_PARAMS),
        client=client,
    )


def _family_record_handler(rows: list[dict[str, Any]]) -> Any:
    """A handler that serves the embedded join for /family_record, honoring eq filters.

    Mirrors the server's ``!inner`` behavior: a spine row with an empty
    ``leads_new`` embed is DROPPED (partial-invisible). Supports the
    ``family_id=eq.<id>`` predicate for ``get_family``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/family_record"
        # The service_role key must be on the request (D-RLS-4).
        assert request.headers["apikey"] == "synthetic-service-role-key"
        assert request.headers["Authorization"] == "Bearer synthetic-service-role-key"
        qs = parse_qs(parsed.query)
        # `!inner` on leads_new: drop spine rows whose lead embed is empty.
        served = [r for r in rows if r.get("leads_new")]
        if "family_id" in qs:
            wanted = qs["family_id"][0].removeprefix("eq.")
            served = [r for r in served if r["family_id"] == wanted]
        return httpx.Response(200, content=json.dumps(served))

    return handler


# ---------------------------------------------------------------------------
# Stage derivation on read (A-24 M2).
# ---------------------------------------------------------------------------


def test_fresh_interest_lead_derives_interest_and_appears() -> None:
    repo = _make_repo(_family_record_handler([_interest_row()]))
    joined = repo.list_joined()
    assert len(joined) == 1
    jf = joined[0]
    assert jf.lead is not None
    assert jf.app_form is None
    # num_children absent from the row → pydantic default of 1 (frozen-schema safe).
    assert jf.lead.num_children == 1
    families = repo.list_families(stage=Stage.INTEREST)
    assert [f.family_id for f in families] == [UUID(_FID_INTEREST)]


def test_submitted_app_form_derives_apply() -> None:
    repo = _make_repo(_family_record_handler([_apply_row()]))
    families = repo.list_families(stage=Stage.APPLY)
    assert [f.family_id for f in families] == [UUID(_FID_APPLY)]
    # And NOT under its stale stored stage ("interest").
    assert repo.list_families(stage=Stage.INTEREST) == []


def test_full_forms_and_unlock_derives_tuition() -> None:
    repo = _make_repo(_family_record_handler([_tuition_row()]))
    families = repo.list_families(stage=Stage.TUITION)
    assert [f.family_id for f in families] == [UUID(_FID_TUITION)]
    assert repo.list_families(stage=Stage.APPLY) == []


def test_multiple_enrollment_rows_derive_from_most_advanced() -> None:
    """Insert-only flow writes >1 enrollment_forms row; pick the most-advanced.

    The apply SPA cannot UPDATE (RLS grants INSERT only), so a completed family
    has both a mid-flow enroll row (partial, locked) AND a tuition row (all six
    signed, unlocked). PostgREST embeds them unordered; with the LESS-advanced
    row listed FIRST, a naive head pick would derive `enroll`. The repo must pick
    the furthest-progressed row and derive `tuition`.
    """
    partial = _enrollment(_FID_TUITION, signed=3, unlocked=False)
    partial["enrollment_form_id"] = "00000000-0000-0000-0000-0000000000e2"
    complete = _enrollment(_FID_TUITION, signed=6, unlocked=True)
    row = _spine(
        _FID_TUITION,
        current_stage="enroll",
        leads_new=[_lead(_FID_TUITION)],
        app_form=[_app_form(_FID_TUITION, submitted=True)],
        enrollment_forms=[partial, complete],  # less-advanced FIRST (the trap).
    )
    repo = _make_repo(_family_record_handler([row]))
    assert [f.family_id for f in repo.list_families(stage=Stage.TUITION)] == [UUID(_FID_TUITION)]
    assert repo.list_families(stage=Stage.ENROLL) == []


def test_partial_family_without_lead_is_excluded() -> None:
    """A family_record with no leads_new is invisible (the INNER-join rule)."""
    partial = _spine("00000000-0000-0000-0000-0000000000ff")  # no leads_new embed
    repo = _make_repo(_family_record_handler([partial, _interest_row()]))
    joined = repo.list_joined()
    assert [jf.family.family_id for jf in joined] == [UUID(_FID_INTEREST)]


def test_pipeline_counts_group_by_derived_stage() -> None:
    rows = [_interest_row(), _apply_row(), _tuition_row()]
    repo = _make_repo(_family_record_handler(rows))
    counts = repo.pipeline_counts()
    assert counts == {
        Stage.INTEREST: 1,
        Stage.APPLY: 1,
        Stage.ENROLL: 0,
        Stage.TUITION: 1,
    }
    assert sum(counts.values()) == 3


def test_get_family_returns_joined_or_none() -> None:
    repo = _make_repo(_family_record_handler([_apply_row()]))
    jf = repo.get_family(UUID(_FID_APPLY))
    assert jf is not None
    assert jf.app_form is not None and jf.app_form.submitted_at is not None
    assert repo.get_family(UUID("00000000-0000-0000-0000-0000000000fe")) is None


def test_list_families_filters_funding_and_seam() -> None:
    row = _spine(
        _FID_INTEREST,
        leads_new=[_lead(_FID_INTEREST)],
        funding_state="funded",
        crm_seam_status="synced",
    )
    repo = _make_repo(_family_record_handler([row]))
    assert repo.list_families(funding_state=FundingState.FUNDED) != []
    assert repo.list_families(funding_state=FundingState.NONE) == []
    assert repo.list_families(seam_status=SeamStatus.SYNCED) != []
    assert repo.list_families(seam_status=SeamStatus.CONFLICT) == []


def test_non_2xx_fails_loud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="boom")

    repo = _make_repo(handler)
    with pytest.raises(SupabaseError):
        repo.list_joined()


# ---------------------------------------------------------------------------
# Drop-off views (A-24) — metadata only, never a value/content or child key.
# ---------------------------------------------------------------------------


def _apply_events_handler(rows_by_query: dict[str, list[dict[str, Any]]]) -> Any:
    """Serve /apply_events: keyed by whether the request filters event_type/family_id."""

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/apply_events"
        qs = parse_qs(parsed.query)
        if "family_id" in qs:
            fid = qs["family_id"][0].removeprefix("eq.")
            served = [r for r in rows_by_query.get("by_family", []) if r["family_id"] == fid]
            # Emulate order=occurred_at.desc.
            served = sorted(served, key=lambda r: r["occurred_at"], reverse=True)
        else:
            # The heatmap query filters event_type=eq.last_step_before_exit.
            served = rows_by_query.get("exits", [])
        return httpx.Response(200, content=json.dumps(served))

    return handler


def test_drop_off_for_family_prefers_last_step_before_exit() -> None:
    fid = _FID_APPLY
    events = [
        {
            "family_id": fid,
            "step": "interest.num_children",
            "field_key": "num_children",
            "event_type": "field_focused",
            "occurred_at": "2026-06-02T10:00:00+00:00",
        },
        {
            "family_id": fid,
            "step": "enroll.form3",
            "field_key": "income",
            "event_type": "last_step_before_exit",
            "occurred_at": "2026-06-02T09:00:00+00:00",
        },
    ]
    repo = _make_repo(_apply_events_handler({"by_family": events}))
    point = repo.drop_off_for_family(UUID(fid))
    assert point == DropOffPoint(
        family_id=UUID(fid),
        step="enroll.form3",
        field_key="income",
        event_type="last_step_before_exit",
        occurred_at="2026-06-02T09:00:00+00:00",
    )


def test_drop_off_for_family_falls_back_to_most_recent() -> None:
    fid = _FID_INTEREST
    events = [
        {
            "family_id": fid,
            "step": "interest.tuition_aware",
            "field_key": None,
            "event_type": "step_viewed",
            "occurred_at": "2026-06-01T08:00:00+00:00",
        },
        {
            "family_id": fid,
            "step": "interest.attribution",
            "field_key": "attribution",
            "event_type": "validation_error_shown",
            "occurred_at": "2026-06-01T09:00:00+00:00",
        },
    ]
    repo = _make_repo(_apply_events_handler({"by_family": events}))
    point = repo.drop_off_for_family(UUID(fid))
    assert point is not None
    assert point.step == "interest.attribution"  # most-recent, no exit event present.


def test_drop_off_for_family_none_when_no_events() -> None:
    repo = _make_repo(_apply_events_handler({"by_family": []}))
    assert repo.drop_off_for_family(UUID(_FID_INTEREST)) is None


def test_drop_off_heatmap_counts_by_step_and_field() -> None:
    exits = [
        {"step": "enroll.form3", "field_key": "income"},
        {"step": "enroll.form3", "field_key": "income"},
        {"step": "apply.confirm", "field_key": None},
    ]
    repo = _make_repo(_apply_events_handler({"exits": exits}))
    buckets = repo.drop_off_heatmap()
    assert buckets == [
        DropOffBucket(step="enroll.form3", field_key="income", count=2),
        DropOffBucket(step="apply.confirm", field_key=None, count=1),
    ]

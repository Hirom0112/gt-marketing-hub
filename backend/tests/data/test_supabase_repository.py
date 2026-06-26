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
from app.core.program import Program
from app.data.models import FundingState, SeamStatus, Stage
from app.data.supabase_repository import (
    _PROGRAM_SCOPED_TABLES,
    DropOffBucket,
    DropOffPoint,
    HouseholdRollUp,
    SupabaseError,
    SupabaseFamilyRepository,
    _is_program_scoped,
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


def _make_repo(handler: Any, *, program: Program | None = None) -> SupabaseFamilyRepository:
    """A repo whose injected httpx client routes every request to ``handler``.

    ``program`` threads the A1 active program (``None`` = the back-compat default,
    no program filter — the posture every existing test relies on).
    """
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://example.supabase.co")
    return SupabaseFamilyRepository(
        base_url="https://example.supabase.co",
        service_role_key="synthetic-service-role-key",
        params=load_params(_EXAMPLE_PARAMS),
        client=client,
        program=program,
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


def test_family_grain_ignores_a_childs_enrollment_packet() -> None:
    """A multi-child family's HOUSEHOLD deal grain reads the household packet
    (student_id NULL), never a CHILD's. Both packets share family_id, so the family
    embed returns them together; a child whose 6/6 packet is the most-advanced would
    otherwise make the household derive `tuition`/forms-cleared — the Rivera bug
    (the one 2-child demo family derived `recovered` and vanished from active triage).
    """
    household = _enrollment(_FID_TUITION, signed=1, unlocked=False)  # 1/6 partial
    child = _enrollment(_FID_TUITION, signed=6, unlocked=True)  # a child: 6/6 cleared
    child["enrollment_form_id"] = "00000000-0000-0000-0000-0000000000e9"
    child["student_id"] = "00000000-0000-0000-0000-0000000000f1"  # student grain
    row = _spine(
        _FID_TUITION,
        current_stage="enroll",
        leads_new=[_lead(_FID_TUITION)],
        app_form=[_app_form(_FID_TUITION, submitted=True)],
        enrollment_forms=[child, household],  # child (most-advanced) FIRST — the trap.
    )
    repo = _make_repo(_family_record_handler([row]))
    # The HOUSEHOLD grain reads its OWN 1/6 packet ⇒ derives ENROLL, not TUITION.
    assert [f.family_id for f in repo.list_families(stage=Stage.ENROLL)] == [UUID(_FID_TUITION)]
    assert repo.list_families(stage=Stage.TUITION) == []
    jf = repo.get_family(UUID(_FID_TUITION))
    assert jf is not None and jf.enrollment_forms is not None
    assert jf.enrollment_forms.forms_signed == 1  # the household packet, not the child's 6


def test_family_grain_falls_back_to_child_packet_when_no_household_packet() -> None:
    """A LIVE per-child application writes ONLY child packets (student_id set) — no
    household-grain packet. The household grain then falls back to the FURTHEST child
    packet so the household still derives sensible progress (A-24), instead of an
    empty `interest`. (Synthetic households keep their own household packet, so this
    fallback only fires when there is none — proven by the test above.)"""
    child_app = _app_form(_FID_TUITION, submitted=True)
    child_app["student_id"] = "00000000-0000-0000-0000-0000000000f2"
    child_enroll = _enrollment(_FID_TUITION, signed=6, unlocked=True)
    child_enroll["student_id"] = "00000000-0000-0000-0000-0000000000f2"
    row = _spine(
        _FID_TUITION,
        current_stage="interest",
        leads_new=[_lead(_FID_TUITION)],
        app_form=[child_app],  # ONLY a child packet — no household-grain row.
        enrollment_forms=[child_enroll],
    )
    repo = _make_repo(_family_record_handler([row]))
    # With no household packet, the household derives from the child's packet ⇒ TUITION.
    assert [f.family_id for f in repo.list_families(stage=Stage.TUITION)] == [UUID(_FID_TUITION)]
    jf = repo.get_family(UUID(_FID_TUITION))
    assert jf is not None and jf.app_form is not None and jf.enrollment_forms is not None


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
            # Emulate the repo's order: nav_seq.desc then occurred_at.desc (the
            # "latest navigation position" sort the contract asks for).
            served = sorted(
                served,
                key=lambda r: (r.get("nav_seq") or 0, r["occurred_at"]),
                reverse=True,
            )
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
            "step": "interest",
            "form_key": None,
            "field_key": "num_children",
            "event_type": "field_focused",
            "occurred_at": "2026-06-02T10:00:00+00:00",
            "nav_seq": 1,
        },
        {
            "family_id": fid,
            "step": "enroll",
            "form_key": "data_collection_consent",
            "field_key": "signature",
            "event_type": "last_step_before_exit",
            "occurred_at": "2026-06-02T09:00:00+00:00",
            "nav_seq": 2,
        },
    ]
    repo = _make_repo(_apply_events_handler({"by_family": events}))
    point = repo.drop_off_for_family(UUID(fid))
    assert point == DropOffPoint(
        family_id=UUID(fid),
        step="enroll",
        form_key="data_collection_consent",
        field_key="signature",
        event_type="last_step_before_exit",
        occurred_at="2026-06-02T09:00:00+00:00",
    )


def test_drop_off_for_family_breaks_exit_ties_by_nav_seq() -> None:
    """Two last_step_before_exit events ⇒ the higher nav_seq (later in nav order) wins."""
    fid = _FID_APPLY
    events = [
        {
            "family_id": fid,
            "step": "enroll",
            "form_key": "student_information",
            "field_key": "first_name",
            "event_type": "last_step_before_exit",
            # SAME occurred_at as the other exit — only nav_seq disambiguates.
            "occurred_at": "2026-06-02T09:00:00+00:00",
            "nav_seq": 5,
        },
        {
            "family_id": fid,
            "step": "enroll",
            "form_key": "tuition_agreement",
            "field_key": "signature",
            "event_type": "last_step_before_exit",
            "occurred_at": "2026-06-02T09:00:00+00:00",
            "nav_seq": 9,  # later in the navigation order — the true last position.
        },
    ]
    repo = _make_repo(_apply_events_handler({"by_family": events}))
    point = repo.drop_off_for_family(UUID(fid))
    assert point is not None
    assert (point.step, point.form_key, point.field_key) == (
        "enroll",
        "tuition_agreement",
        "signature",
    )


def test_drop_off_for_family_falls_back_to_most_recent() -> None:
    fid = _FID_INTEREST
    events = [
        {
            "family_id": fid,
            "step": "interest",
            "form_key": None,
            "field_key": None,
            "event_type": "step_viewed",
            "occurred_at": "2026-06-01T08:00:00+00:00",
            "nav_seq": 1,
        },
        {
            "family_id": fid,
            "step": "apply",
            "form_key": "attribution",
            "field_key": "attribution",
            "event_type": "validation_error_shown",
            "occurred_at": "2026-06-01T09:00:00+00:00",
            "nav_seq": 2,
        },
    ]
    repo = _make_repo(_apply_events_handler({"by_family": events}))
    point = repo.drop_off_for_family(UUID(fid))
    assert point is not None
    # most-recent (highest nav_seq), no exit event present.
    assert point.step == "apply"
    assert point.form_key == "attribution"


def test_drop_off_for_family_none_when_no_events() -> None:
    repo = _make_repo(_apply_events_handler({"by_family": []}))
    assert repo.drop_off_for_family(UUID(_FID_INTEREST)) is None


def test_drop_off_heatmap_counts_by_step_form_and_field() -> None:
    exits = [
        {"step": "enroll", "form_key": "data_collection_consent", "field_key": "signature"},
        {"step": "enroll", "form_key": "data_collection_consent", "field_key": "signature"},
        {"step": "apply", "form_key": "consents", "field_key": None},
    ]
    repo = _make_repo(_apply_events_handler({"exits": exits}))
    buckets = repo.drop_off_heatmap()
    assert buckets == [
        DropOffBucket(
            step="enroll", form_key="data_collection_consent", field_key="signature", count=2
        ),
        DropOffBucket(step="apply", form_key="consents", field_key=None, count=1),
    ]


def test_drop_off_heatmap_distinguishes_forms_within_a_step() -> None:
    """Two enroll exits in DIFFERENT sub-forms tally as separate cells (form granularity)."""
    exits = [
        {"step": "enroll", "form_key": "student_information", "field_key": "first_name"},
        {"step": "enroll", "form_key": "tuition_agreement", "field_key": "signature"},
    ]
    repo = _make_repo(_apply_events_handler({"exits": exits}))
    buckets = repo.drop_off_heatmap()
    # Two distinct (step, form_key, field_key) cells, each count 1 — NOT collapsed
    # to one "enroll" bucket. Order: count tie ⇒ step, then form_key.
    assert {(b.form_key, b.count) for b in buckets} == {
        ("student_information", 1),
        ("tuition_agreement", 1),
    }


# ---------------------------------------------------------------------------
# Per-child `student` grain (TODO.md R1) — list_students + household_roll_up.
# The live read embeds the parent `family_record` (with its lead + community
# profile) plus each child's OWN app_form/enrollment_forms (to-one FK embeds).
# Stage is DERIVED on read with the SAME pure stage machine the family path uses
# (A-24 M2): the stored student.current_stage is a placeholder, intentionally
# wrong in the fixtures. Households group by family_record.user_id.
# ---------------------------------------------------------------------------

_UID_A = "00000000-0000-0000-0000-0000000000b1"  # household A (two children)
_UID_B = "00000000-0000-0000-0000-0000000000b2"  # household B (one child)
_SID_A1 = "00000000-0000-0000-0000-0000000000f1"
_SID_A2 = "00000000-0000-0000-0000-0000000000f2"
_SID_B1 = "00000000-0000-0000-0000-0000000000f3"


def _family_embed(family_id: str, *, user_id: str | None) -> dict[str, Any]:
    """A parent family_record embed (with its lead + community profile) for a student."""
    return {
        "family_id": family_id,
        "user_id": user_id,
        "display_name": "Synthetic Household",
        "primary_contact_synthetic_email": "synthetic@example.invalid",
        "lead_id": None,
        "app_form_id": None,
        "enrollment_form_id": None,
        "community_profile_id": None,
        "current_stage": "interest",
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
        "leads_new": [_lead(family_id)],
        "community_profiles": [],
    }


def _student_row(
    student_id: str,
    family_id: str,
    *,
    user_id: str | None,
    label: str,
    stored_stage: str = "tuition",  # stale placeholder, deliberately wrong.
    app_form: dict[str, Any] | None = None,
    enrollment_forms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A `student` PostgREST row: student cols + parent family embed + own app/enroll."""
    return {
        "student_id": student_id,
        "family_id": family_id,
        "display_label": label,
        "synthetic_first_name": "Syn",
        "grade": "3",
        "current_stage": stored_stage,
        "stall_reason": None,
        "stalled_since": None,
        "funding_type": None,
        "funding_state": "none",
        "app_form_id": None,
        "enrollment_form_id": None,
        "crm_seam_status": "unsynced",
        "crm_synced_at": None,
        "work_queue_score": None,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "family_record": _family_embed(family_id, user_id=user_id),
        # To-one FK embeds: a single object (or null), NOT a list.
        "app_form": app_form,
        "enrollment_forms": enrollment_forms,
    }


def _student_handler(rows: list[dict[str, Any]]) -> Any:
    """Serve /rest/v1/student, honoring family_record!inner (drop null-parent rows)."""

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/student"
        assert request.headers["apikey"] == "synthetic-service-role-key"
        served = [r for r in rows if r.get("family_record")]
        return httpx.Response(200, content=json.dumps(served))

    return handler


def test_list_students_non_empty_with_per_child_derived_stage() -> None:
    """list_students returns one JoinedStudent per child, stage DERIVED per-child."""
    rows = [
        # Household A, child 1: submitted app, no forms → derives `apply`.
        _student_row(
            _SID_A1,
            "00000000-0000-0000-0000-00000000a001",
            user_id=_UID_A,
            label="Household A — Alex",
            app_form=_app_form("00000000-0000-0000-0000-00000000a001", submitted=True),
        ),
        # Household A, child 2: no app at all → derives `interest`
        # (proves per-child derivation: same household, different stage).
        _student_row(
            _SID_A2,
            "00000000-0000-0000-0000-00000000a002",
            user_id=_UID_A,
            label="Household A — Bea",
        ),
    ]
    repo = _make_repo(_student_handler(rows))
    students = repo.list_students()
    assert len(students) == 2
    by_label = {js.student.display_label: js for js in students}
    # Per-child DERIVED stage, NOT the stored "tuition" placeholder.
    assert by_label["Household A — Alex"].student.current_stage == Stage.APPLY
    assert by_label["Household A — Bea"].student.current_stage == Stage.INTEREST
    # Parent household + its lead are attached.
    assert by_label["Household A — Alex"].family.user_id == UUID(_UID_A)
    assert by_label["Household A — Alex"].lead is not None
    # The child's own app_form rode along; the lead-less child has none.
    assert by_label["Household A — Alex"].app_form is not None
    assert by_label["Household A — Bea"].app_form is None


def test_list_students_empty_when_no_student_rows() -> None:
    """An empty live `student` table → [] (a real query, not the old stub)."""
    repo = _make_repo(_student_handler([]))
    assert repo.list_students() == []


def test_household_roll_up_groups_children_with_worst_stage() -> None:
    """household_roll_up: one row per household, per-child stages + worst-stage rollup."""
    rows = [
        # Household A — Alex at `tuition` (full forms + unlock).
        _student_row(
            _SID_A1,
            "00000000-0000-0000-0000-00000000a001",
            user_id=_UID_A,
            label="Household A — Alex",
            stored_stage="interest",
            app_form=_app_form("00000000-0000-0000-0000-00000000a001", submitted=True),
            enrollment_forms=_enrollment(
                "00000000-0000-0000-0000-00000000a001", signed=6, unlocked=True
            ),
        ),
        # Household A — Bea at `interest` (the household's weakest link).
        _student_row(
            _SID_A2,
            "00000000-0000-0000-0000-00000000a002",
            user_id=_UID_A,
            label="Household A — Bea",
        ),
        # Household B — one child at `apply`.
        _student_row(
            _SID_B1,
            "00000000-0000-0000-0000-00000000b001",
            user_id=_UID_B,
            label="Household B — Cy",
            app_form=_app_form("00000000-0000-0000-0000-00000000b001", submitted=True),
        ),
    ]
    repo = _make_repo(_student_handler(rows))
    rollups = repo.household_roll_up()
    assert len(rollups) == 2
    by_uid = {r.user_id: r for r in rollups}
    a = by_uid[UUID(_UID_A)]
    assert {c.display_label: c.stage for c in a.children} == {
        "Household A — Alex": Stage.TUITION,
        "Household A — Bea": Stage.INTEREST,
    }
    # Worst stage = the least-advanced child (the weakest link).
    assert a.worst_stage == Stage.INTEREST
    b = by_uid[UUID(_UID_B)]
    assert [c.stage for c in b.children] == [Stage.APPLY]
    assert b.worst_stage == Stage.APPLY
    assert isinstance(a, HouseholdRollUp)


def test_household_roll_up_keeps_null_owner_households_separate() -> None:
    """NULL-owner (server-only) households are NOT collapsed into one group."""
    rows = [
        _student_row(
            _SID_A1,
            "00000000-0000-0000-0000-00000000a001",
            user_id=None,
            label="Unowned 1",
        ),
        _student_row(
            _SID_B1,
            "00000000-0000-0000-0000-00000000b001",
            user_id=None,
            label="Unowned 2",
        ),
    ]
    repo = _make_repo(_student_handler(rows))
    rollups = repo.household_roll_up()
    # Two distinct family_ids ⇒ two separate rollups, both with user_id None.
    assert len(rollups) == 2
    assert all(r.user_id is None for r in rollups)
    assert {r.family_id for r in rollups} == {
        UUID("00000000-0000-0000-0000-00000000a001"),
        UUID("00000000-0000-0000-0000-00000000b001"),
    }


# ---------------------------------------------------------------------------
# Write seam (TODO.md R1) — mark_synced / apply_field PATCH via service_role
# (BYPASSRLS, server-only — INV-5 / D-RLS-4). The reconcile flow's persist step.
# ---------------------------------------------------------------------------


def _capture_patch_handler(captured: dict[str, Any]) -> Any:
    """Serve a PostgREST PATCH on /family_record, recording method/query/body."""

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/family_record"
        # service_role auth (server-only — INV-5 / D-RLS-4).
        assert request.headers["apikey"] == "synthetic-service-role-key"
        assert request.headers["Authorization"] == "Bearer synthetic-service-role-key"
        captured["method"] = request.method
        captured["query"] = parse_qs(parsed.query)
        captured["body"] = json.loads(request.content.decode() or "{}")
        captured["prefer"] = request.headers.get("Prefer")
        return httpx.Response(204)

    return handler


def test_mark_synced_patches_crm_synced_at_via_service_role() -> None:
    """mark_synced issues a row-scoped PATCH of crm_synced_at (service_role)."""
    from datetime import UTC, datetime

    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_patch_handler(captured))
    fid = UUID(_FID_INTEREST)
    when = datetime(2030, 1, 1, tzinfo=UTC)

    repo.mark_synced(fid, when)

    assert captured["method"] == "PATCH"
    assert captured["query"]["family_id"] == [f"eq.{fid}"]
    assert captured["body"] == {"crm_synced_at": when.isoformat()}
    assert captured["prefer"] == "return=minimal"


def test_apply_field_patches_adopted_enum_as_its_value() -> None:
    """apply_field PATCHes one tracked field, serializing an enum to its .value."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_patch_handler(captured))
    fid = UUID(_FID_APPLY)

    repo.apply_field(fid, "current_stage", Stage.TUITION)

    assert captured["method"] == "PATCH"
    assert captured["query"]["family_id"] == [f"eq.{fid}"]
    assert captured["body"] == {"current_stage": "tuition"}


def test_patch_non_2xx_fails_loud() -> None:
    """A non-2xx PATCH raises SupabaseError (fail loud, never a silent lost write)."""
    from datetime import UTC, datetime

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="boom")

    repo = _make_repo(handler)
    with pytest.raises(SupabaseError):
        repo.mark_synced(UUID(_FID_INTEREST), datetime(2030, 1, 1, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Append-only voucher_event timeline (TODO.md R2) — append_voucher_event POSTs a
# row per funding-state transition (time-in-state; feeds the work-queue deadline
# ranking + §10 observability). service_role (BYPASSRLS, server-only — INV-5 /
# D-RLS-4). Append-only: an INSERT (POST), never an UPDATE/DELETE.
# ---------------------------------------------------------------------------


def _capture_post_handler(captured: dict[str, Any]) -> Any:
    """Serve a PostgREST POST on /voucher_event, recording method/path/body."""

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/voucher_event"
        # service_role auth (server-only — INV-5 / D-RLS-4).
        assert request.headers["apikey"] == "synthetic-service-role-key"
        assert request.headers["Authorization"] == "Bearer synthetic-service-role-key"
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode() or "{}")
        captured["prefer"] = request.headers.get("Prefer")
        return httpx.Response(201)

    return handler


def test_append_voucher_event_posts_transition_via_service_role() -> None:
    """append_voucher_event POSTs a from→to transition row (service_role)."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_post_handler(captured))
    fid = UUID(_FID_INTEREST)

    repo.append_voucher_event(
        family_id=fid,
        from_state=FundingState.AWARDED_SELFREPORT,
        to_state=FundingState.SELECTED_GT,
        program="tx_tefa",
        signal="family_selected",
    )

    assert captured["method"] == "POST"
    body = captured["body"]
    assert body["family_id"] == str(fid)
    # Enums serialize to their DB column .value form.
    assert body["from_state"] == "awarded_selfreport"
    assert body["to_state"] == "selected_gt"
    assert body["program"] == "tx_tefa"
    assert body["signal"] == "family_selected"
    # Household-level event: no per-child key.
    assert body.get("student_id") is None


def test_append_voucher_event_records_student_when_given() -> None:
    """A per-child transition carries the optional student_id."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_post_handler(captured))
    fid = UUID(_FID_APPLY)
    sid = UUID("00000000-0000-0000-0000-0000000000f1")

    repo.append_voucher_event(
        family_id=fid,
        from_state=FundingState.GT_CONFIRMED,
        to_state=FundingState.FIRST_INSTALLMENT_RECEIVED,
        program="tx_tefa",
        signal="first_installment_received",
        student_id=sid,
    )

    assert captured["body"]["student_id"] == str(sid)


def test_append_voucher_event_allows_null_from_state() -> None:
    """The first transition (no prior state) writes from_state null (append-only origin)."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_post_handler(captured))

    repo.append_voucher_event(
        family_id=UUID(_FID_INTEREST),
        from_state=None,
        to_state=FundingState.APPLIED,
        program="tx_tefa",
        signal="self_report",
    )

    assert captured["body"]["from_state"] is None
    assert captured["body"]["to_state"] == "applied"


def test_append_voucher_event_non_2xx_fails_loud() -> None:
    """A non-2xx POST raises SupabaseError (fail loud, never a silent lost append)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="boom")

    repo = _make_repo(handler)
    with pytest.raises(SupabaseError):
        repo.append_voucher_event(
            family_id=UUID(_FID_INTEREST),
            from_state=FundingState.NONE,
            to_state=FundingState.APPLIED,
            program="tx_tefa",
            signal="self_report",
        )


# ---------------------------------------------------------------------------
# A1 app-layer program isolation (PLAN_v2 §A1; ASSUMPTIONS A-37/A-38). The backend
# reads Supabase over the service_role key, which BYPASSES the 0024 RESTRICTIVE RLS,
# so isolation is enforced IN CODE: every program-scoped read carries an explicit
# `program_id=eq.<active>` filter and every insert/update stamps it. A repo with no
# active program (the back-compat default) applies NO filter — proven by every test
# above passing unchanged.
# ---------------------------------------------------------------------------


def test_is_program_scoped_matches_the_migration_partition() -> None:
    """The scoped-table set is EXACTLY the 9 program-partitioned tables (0024 / A-37).

    Pure-logic guard: the program filter must apply to the 9 family/enrollment funnel
    tables and NEVER to an operational/global table (`assignment_cursor`,
    `community_profiles`), or it would over-constrain a global registry.
    """
    assert _PROGRAM_SCOPED_TABLES == {
        "family_record",
        "leads_new",
        "app_form",
        "enrollment_forms",
        "apply_events",
        "student",
        "voucher_event",
        "sis_status",
        "lead_assignment",
    }
    assert _is_program_scoped("/rest/v1/family_record") is True
    assert _is_program_scoped("/rest/v1/student") is True
    assert _is_program_scoped("/rest/v1/voucher_event") is True
    # Operational/global tables are NOT program-scoped (A-37).
    assert _is_program_scoped("/rest/v1/assignment_cursor") is False
    assert _is_program_scoped("/rest/v1/community_profiles") is False


def _program_capture_handler(captured: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
    """Serve /family_record, recording the query string so the program filter is visible."""

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        assert parsed.path == "/rest/v1/family_record"
        captured["query"] = parse_qs(parsed.query)
        served = [r for r in rows if r.get("leads_new")]
        return httpx.Response(200, content=json.dumps(served))

    return handler


def test_reads_filter_by_active_program() -> None:
    """A repo with an active program scopes every read to `program_id=eq.<program>`."""
    captured: dict[str, Any] = {}
    repo = _make_repo(
        _program_capture_handler(captured, [_interest_row()]),
        program=Program.SUMMER_CAMP,
    )
    repo.list_joined()
    assert captured["query"]["program_id"] == ["eq.summer_camp"]


def test_reads_apply_no_program_filter_without_an_active_program() -> None:
    """The back-compat default (no active program) issues NO program_id filter."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_program_capture_handler(captured, [_interest_row()]))  # program=None
    repo.list_joined()
    assert "program_id" not in captured["query"]


def test_writes_stamp_the_active_program() -> None:
    """An insert stamps the active TENANT program_id, distinct from the domain `program`."""
    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_post_handler(captured), program=Program.SUMMER_CAMP)
    repo.append_voucher_event(
        family_id=UUID(_FID_INTEREST),
        from_state=None,
        to_state=FundingState.APPLIED,
        program="tx_tefa",  # the voucher program — a DIFFERENT column from program_id.
        signal="self_report",
    )
    body = captured["body"]
    assert body["program_id"] == "summer_camp"  # the A1 tenant tag (stamped).
    assert body["program"] == "tx_tefa"  # the domain voucher program (unchanged).


def test_patch_carries_the_active_program_filter() -> None:
    """A row-scoped PATCH ANDs the active program so a cross-program id can't be written."""
    from datetime import UTC, datetime

    captured: dict[str, Any] = {}
    repo = _make_repo(_capture_patch_handler(captured), program=Program.SUMMER_CAMP)
    repo.mark_synced(UUID(_FID_INTEREST), datetime(2030, 1, 1, tzinfo=UTC))
    assert captured["query"]["program_id"] == ["eq.summer_camp"]
    assert captured["query"]["family_id"] == [f"eq.{UUID(_FID_INTEREST)}"]

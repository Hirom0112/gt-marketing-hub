"""Unit tests for the dual-source ambassador reconciler (Grassroots).

Drives the pure core (:mod:`app.core.ambassador_reconcile`) through every overlap
path — matched, hubspot-only, community-only, conflict — and confirms the curated
synthetic roster (:mod:`app.data.synthetic_ambassadors`) reconciles to the
expected, stable union. No threshold: matching is exact on the normalized key.
"""

from __future__ import annotations

from app.core.ambassador_reconcile import (
    AmbassadorProvenance,
    AmbassadorRecord,
    match_key,
    reconcile_ambassadors,
)
from app.data.synthetic_ambassadors import generate_ambassador_sources


def _rec(
    name: str, email: str, status: str = "Active", segment: str = "Robotics parents"
) -> AmbassadorRecord:
    return AmbassadorRecord(
        synthetic_name=name,
        synthetic_email=email,
        segment=segment,
        region="Austin metro",
        status=status,
    )


def test_match_key_is_normalized_email_first() -> None:
    a = _rec("Renata Fields", "  Fields.214@Example.INVALID ")
    b = _rec("renata fields", "fields.214@example.invalid")
    assert match_key(a) == match_key(b) == "email:fields.214@example.invalid"


def test_match_key_falls_back_to_name_and_segment_without_email() -> None:
    rec = _rec("Sam Okafor", "", segment="Chess club")
    assert match_key(rec) == "ns:sam okafor|chess club"


def test_matched_row_has_both_provenance_no_conflict() -> None:
    hs = [_rec("Renata Fields", "fields.214@example.invalid", status="Active")]
    community = [_rec("Renata Fields", "fields.214@example.invalid", status="Active")]
    result = reconcile_ambassadors(hs, community)

    assert result.union_count == 1
    assert result.matched_count == 1
    assert result.hubspot_only_count == 0
    assert result.community_only_count == 0
    assert result.conflict_count == 0
    assert result.union[0].provenance is AmbassadorProvenance.BOTH
    assert result.union[0].has_conflict is False


def test_hubspot_only_row() -> None:
    hs = [_rec("Leon Whitaker", "whitaker.640@example.invalid")]
    result = reconcile_ambassadors(hs, [])

    assert result.matched_count == 0
    assert result.hubspot_only_count == 1
    assert result.community_only_count == 0
    assert result.union[0].provenance is AmbassadorProvenance.HUBSPOT_ONLY


def test_community_only_row() -> None:
    community = [_rec("Theo Nakamura", "nakamura.926@example.invalid")]
    result = reconcile_ambassadors([], community)

    assert result.matched_count == 0
    assert result.hubspot_only_count == 0
    assert result.community_only_count == 1
    assert result.union[0].provenance is AmbassadorProvenance.COMMUNITY_ONLY


def test_conflicting_status_is_surfaced_not_resolved() -> None:
    hs = [_rec("Grace Liu", "liu.419@example.invalid", status="Champion")]
    community = [_rec("Grace Liu", "liu.419@example.invalid", status="Active")]
    result = reconcile_ambassadors(hs, community)

    # One matched row, flagged — the union keeps the HubSpot side but marks it.
    assert result.matched_count == 1
    assert result.conflict_count == 1
    row = result.union[0]
    assert row.provenance is AmbassadorProvenance.BOTH
    assert row.has_conflict is True
    assert row.conflicting_fields == ("status",)
    assert row.status == "Champion"  # not silently overwritten by the community value

    conflict = result.conflicts[0]
    assert conflict.field == "status"
    assert conflict.hubspot_value == "Champion"
    assert conflict.community_value == "Active"


def test_union_order_is_stable_hubspot_then_community_only() -> None:
    hs = [
        _rec("A One", "a.1@example.invalid"),
        _rec("B Two", "b.2@example.invalid"),
    ]
    community = [
        _rec("B Two", "b.2@example.invalid"),  # matched
        _rec("C Three", "c.3@example.invalid"),  # community-only
    ]
    result = reconcile_ambassadors(hs, community)
    names = [r.synthetic_name for r in result.union]
    assert names == ["A One", "B Two", "C Three"]


def test_synthetic_roster_reconciles_to_expected_counts() -> None:
    sources = generate_ambassador_sources()
    result = reconcile_ambassadors(list(sources.hubspot.rows), list(sources.community.rows))

    # 5 both + 2 conflict (also matched) + 2 hubspot-only + 2 community-only = 11.
    assert result.union_count == 11
    assert result.matched_count == 7
    assert result.hubspot_only_count == 2
    assert result.community_only_count == 2
    assert result.conflict_count == 2


def test_synthetic_roster_is_deterministic() -> None:
    a = generate_ambassador_sources()
    b = generate_ambassador_sources(seed=999)
    assert a == b  # fixed curated roster — seed does not vary output

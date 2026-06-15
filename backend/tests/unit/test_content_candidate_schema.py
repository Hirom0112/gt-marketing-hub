"""ContentCandidate schema tests (S4; CONTENT_SPEC §3, §2.4/§2.5; INV-2, V-1/V-3).

CONTENT_SPEC §1.2 + §9.2 Rule V-1 (schema-validity): a content record missing a
**[req]** field, carrying an out-of-range enum, or holding an unknown extra field
is schema-INVALID and BLOCKED by the gate. These tests pin that the data shape
fails closed at the Pydantic boundary — a malformed candidate RAISES
`ValidationError`, never coerces. A complete, in-range candidate constructs; this
IS the V-1 data shape the grounding gate operates on.

§3 / §9.2 Rule V-3 (COPPA-safe): `audience_tag` is a CLOSED enum over
{prospective_parent, current_parent, leadership, general} — there is **no minor
audience**. A minor-targeted value is not representable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentCandidate,
    ContentFormat,
    HumanDecision,
    LifecycleStage,
    Provenance,
)


def _valid_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for a ContentCandidate (§3)."""
    return {
        "id": "01J0CONTENTCAND0000000000",
        "batch_id": "batch-001",
        "prompt": "Draft a short caption for prospective gifted-K8 parents.",
        "channel": "instagram",
        "format": "short_caption",
        "concept": "Lead with the program and the parent's decision, no hype.",
        "copy": "GT School is a mastery-based virtual school for gifted K-8 learners.",
        "audience_tag": "prospective_parent",
        "lifecycle": "candidate",
        "decision": {"decision": "pending"},
        "provenance": {"generated_by": "llm", "created_at": "2026-06-14T00:00:00Z"},
    }


def test_accepts_wellformed() -> None:
    """A well-formed candidate builds with typed enums + nested groups (§3)."""
    cand = ContentCandidate(**_valid_kwargs())  # type: ignore[arg-type]
    assert cand.channel is Channel.INSTAGRAM
    assert cand.format is ContentFormat.SHORT_CAPTION
    assert cand.audience_tag is AudienceTag.PROSPECTIVE_PARENT
    assert cand.lifecycle is LifecycleStage.CANDIDATE
    assert cand.copy_text.startswith("GT School")  # §3 `copy`, aliased
    assert isinstance(cand.decision, HumanDecision)
    assert isinstance(cand.provenance, Provenance)
    # [opt] fields default empty / None.
    assert cand.claims == []
    assert cand.cta is None
    assert cand.family_ref is None
    assert cand.validation is None


def test_rejects_missing_required_field() -> None:
    """A candidate missing a [req] field RAISES (V-1 schema-validity, §9.2)."""
    for field in ("concept", "copy", "batch_id", "channel", "audience_tag", "decision"):
        bad = _valid_kwargs()
        del bad[field]
        with pytest.raises(ValidationError):
            ContentCandidate(**bad)  # type: ignore[arg-type]


def test_rejects_out_of_range_audience_tag() -> None:
    """An out-of-range audience_tag RAISES — enums are CLOSED (§9.2 V-1/V-3)."""
    bad = _valid_kwargs()
    bad["audience_tag"] = "child"  # never a minor audience (§9 V-3)
    with pytest.raises(ValidationError):
        ContentCandidate(**bad)  # type: ignore[arg-type]


def test_rejects_out_of_range_channel_and_format() -> None:
    """Out-of-range channel / format RAISE — closed enums never coerce."""
    bad_channel = _valid_kwargs()
    bad_channel["channel"] = "snapchat"
    with pytest.raises(ValidationError):
        ContentCandidate(**bad_channel)  # type: ignore[arg-type]

    bad_format = _valid_kwargs()
    bad_format["format"] = "skywriting"
    with pytest.raises(ValidationError):
        ContentCandidate(**bad_format)  # type: ignore[arg-type]


def test_rejects_extra_field() -> None:
    """An unknown extra top-level field is forbidden (extra='forbid'; §1.2 V-1)."""
    extra = _valid_kwargs()
    extra["llm_confidence"] = 0.97
    with pytest.raises(ValidationError):
        ContentCandidate(**extra)  # type: ignore[arg-type]


def test_rejects_empty_required_string() -> None:
    """An empty [req] string (copy) is rejected (min_length 1)."""
    empty = _valid_kwargs()
    empty["copy"] = ""
    with pytest.raises(ValidationError):
        ContentCandidate(**empty)  # type: ignore[arg-type]


def test_candidate_is_frozen() -> None:
    """The candidate is immutable once built — a proposal is not mutated (INV-2)."""
    cand = ContentCandidate(**_valid_kwargs())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        cand.copy_text = "rewritten"  # type: ignore[misc]


def test_audience_tag_enum_excludes_minors() -> None:
    """The AudienceTag enum holds exactly the four adult/leadership values (§9 V-3)."""
    assert {a.value for a in AudienceTag} == {
        "prospective_parent",
        "current_parent",
        "leadership",
        "general",
    }

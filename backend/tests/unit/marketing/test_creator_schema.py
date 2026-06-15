"""CreatorRecord + SentimentRecord schema tests — S6 §8 (LOCKED, OUT-4/OUT-5).

§8.1 CreatorRecord is AGGREGATE/SYNTHETIC creator-discovery data: adults-only
(`audienceSegment` has no minor segment), aggregate-only (`dataMode` is a CLOSED
set of `synthetic`/`aggregate` — `live_scrape` is NOT representable), and a
record with `isMinor=true` is BLOCKED at parse time (CONTENT_SPEC §9 V-3 /
INV-6 — fail closed). §8.2 SentimentRecord is PLACEHOLDER (`sourceMode` is
`placeholder`/`synthetic` only — never `live_feed`).

Per CLAUDE.md §4.2 (grounding gate V-3) every block rule gets a passing AND a
BLOCKING test case, proving fail-closed (INV-4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.schemas.content import Channel, GeneratedBy, Provenance
from app.marketing.schemas.discovery import (
    AudienceSegment,
    CreatorDataMode,
    CreatorRecord,
    Sentiment,
    SentimentRecord,
    SentimentSourceMode,
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-06-14T00:00:00Z")


def _creator(**overrides: object) -> CreatorRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "displayHandle": "@synthetic_parent_educator",
        "channel": Channel.INSTAGRAM,
        "audienceSegment": AudienceSegment.PARENTS,
        "fitScore": 0.82,
        "authenticityScore": 0.91,
        "dataMode": CreatorDataMode.AGGREGATE,
        "isMinor": False,
        "provenance": _provenance(),
    }
    base.update(overrides)
    return CreatorRecord(**base)  # type: ignore[arg-type]


def _sentiment(**overrides: object) -> SentimentRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "channel": Channel.X,
        "topic": "online gifted schooling",
        "sentiment": Sentiment.POSITIVE,
        "sourceMode": SentimentSourceMode.PLACEHOLDER,
        "observedAt": "2026-06-14T00:00:00Z",
        "provenance": _provenance(),
    }
    base.update(overrides)
    return SentimentRecord(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# §8.1 CreatorRecord — adults-only, aggregate-only, minor-block (INV-6).
# --------------------------------------------------------------------------- #
def test_creator_record_with_isMinor_true_is_blocked() -> None:
    """A CreatorRecord with isMinor=True RAISES — fail-closed (§9 V-3, INV-6)."""
    # The adults-only happy path validates.
    creator = _creator(isMinor=False)
    assert creator.is_minor is False

    # isMinor=True is BLOCKED at parse time.
    with pytest.raises(ValidationError):
        _creator(isMinor=True)


def test_creator_dataMode_never_live_scrape() -> None:
    """`dataMode` is CLOSED to synthetic/aggregate; `live_scrape` RAISES (OUT-4)."""
    assert _creator(dataMode="synthetic").data_mode is CreatorDataMode.SYNTHETIC
    assert _creator(dataMode="aggregate").data_mode is CreatorDataMode.AGGREGATE

    # `live_scrape` is not representable — the closed enum RAISES.
    with pytest.raises(ValidationError):
        _creator(dataMode="live_scrape")


def test_creator_audience_segment_has_no_minor() -> None:
    """`audienceSegment` is the adults-only closed set — no minor segment (INV-6)."""
    assert {m.value for m in AudienceSegment} == {"parents", "educators", "general"}
    for member in AudienceSegment:
        assert _creator(audienceSegment=member).audience_segment is member
    with pytest.raises(ValidationError):
        _creator(audienceSegment="students")


def test_creator_record_shape_and_closedness() -> None:
    """Required fields enforced, scores typed, unknown extras rejected (fail closed)."""
    creator = _creator()
    assert creator.fit_score == 0.82
    assert creator.authenticity_score == 0.91
    assert creator.display_handle
    assert creator.channel is Channel.INSTAGRAM
    assert creator.rationale is None

    # A missing required field RAISES.
    with pytest.raises(ValidationError):
        CreatorRecord(  # type: ignore[call-arg]
            id=uuid4(),
            displayHandle="@x",
            channel=Channel.INSTAGRAM,
            audienceSegment=AudienceSegment.PARENTS,
            fitScore=0.5,
            authenticityScore=0.5,
            dataMode=CreatorDataMode.AGGREGATE,
            # isMinor missing
            provenance=_provenance(),
        )
    # An unknown extra field is rejected (extra="forbid").
    with pytest.raises(ValidationError):
        _creator(unexpected="nope")


# --------------------------------------------------------------------------- #
# §8.2 SentimentRecord — placeholder, never live_feed (OUT-5).
# --------------------------------------------------------------------------- #
def test_sentiment_sourceMode_never_live_feed() -> None:
    """`sourceMode` is CLOSED to placeholder/synthetic; `live_feed` RAISES (OUT-5)."""
    assert _sentiment(sourceMode="placeholder").source_mode is SentimentSourceMode.PLACEHOLDER
    assert _sentiment(sourceMode="synthetic").source_mode is SentimentSourceMode.SYNTHETIC

    # `live_feed` is not representable — the closed enum RAISES.
    with pytest.raises(ValidationError):
        _sentiment(sourceMode="live_feed")


def test_sentiment_record_shape_and_closedness() -> None:
    """Required fields enforced, sentiment enum closed, optional score/excerpt."""
    rec = _sentiment(score=0.7, excerpt="Love this for gifted kids.")
    assert rec.sentiment is Sentiment.POSITIVE
    assert rec.score == 0.7
    assert rec.excerpt == "Love this for gifted kids."
    assert rec.topic == "online gifted schooling"

    # Optional fields default None.
    bare = _sentiment()
    assert bare.score is None
    assert bare.excerpt is None

    # sentiment is a CLOSED enum.
    with pytest.raises(ValidationError):
        _sentiment(sentiment="furious")
    for member in Sentiment:
        assert _sentiment(sentiment=member).sentiment is member

    # An unknown extra field is rejected.
    with pytest.raises(ValidationError):
        _sentiment(unexpected="nope")

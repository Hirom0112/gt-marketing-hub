"""Creator-discovery + sentiment records — AGGREGATE/PLACEHOLDER data (S6 §8).

§8 models the marketing-breadth discovery surfaces as typed, validated proposals
(INV-2). Two LOCKED records live here:

§8.1 `CreatorRecord` (FR-3.8, OUT-4) — creator-discovery data that is
AGGREGATE/SYNTHETIC by construction, enforcing INV-6 (no child-keyed targeting or
scraping of minors) at the TYPE level:
  * `audience_segment` is the adults-only closed set (`parents`/`educators`/
    `general`) — there is NO minor segment, so a child audience is not
    representable (§9 V-3 / COPPA-safe).
  * `data_mode` is CLOSED to `synthetic`/`aggregate` — `live_scrape` is NOT a
    member, so a scraping mode RAISES `pydantic.ValidationError` (OUT-4).
  * `is_minor` MUST be false: a `field_validator` REJECTS `is_minor=True` with a
    ValidationError, so a minor record is BLOCKED at parse time — fail closed
    (§9 V-3 / INV-6). (Building the SCORER over these records is a different
    agent; here we build only the SCHEMA + its validators.)

§8.2 `SentimentRecord` (FR-3.10, OUT-5) — PLACEHOLDER sentiment data: `excerpt`
is synthetic (no real-user PII, INV-1) and `source_mode` is CLOSED to
`placeholder`/`synthetic` — `live_feed` is NOT a member, so a live-feed mode
RAISES (OUT-5).

Pure data per CLAUDE.md §3: imports only `app.ai.schemas.content` (reusing the
LOCKED `Channel` / `Provenance`), pydantic and stdlib — no `anthropic` /
`langgraph` / I/O. CONTENT_SPEC uses camelCase wire names; attributes are
snake_case with pydantic aliases + `populate_by_name=True`, matching `geo.py`.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.schemas.content import Channel, Provenance

# --------------------------------------------------------------------------- #
# §8.1 CreatorRecord enums.
# --------------------------------------------------------------------------- #


class AudienceSegment(StrEnum):
    """`audienceSegment` enum (§8.1, LOCKED) — ADULTS ONLY; no minor segment.

    The closed set is exactly the adult audiences a creator may serve; a child
    segment is not representable, enforcing COPPA-safety at the type level
    (§9 V-3 / INV-6).
    """

    PARENTS = "parents"
    EDUCATORS = "educators"
    GENERAL = "general"


class CreatorDataMode(StrEnum):
    """`dataMode` enum (§8.1, LOCKED) — AGGREGATE/SYNTHETIC only (OUT-4/INV-6).

    `live_scrape` is intentionally NOT a member: scraping is not representable,
    so a `live_scrape` value RAISES `pydantic.ValidationError` (fail closed).
    """

    SYNTHETIC = "synthetic"
    AGGREGATE = "aggregate"


# --------------------------------------------------------------------------- #
# §8.2 SentimentRecord enums.
# --------------------------------------------------------------------------- #


class Sentiment(StrEnum):
    """`sentiment` enum (§8.2, LOCKED) — the polarity of an observed signal."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class SentimentSourceMode(StrEnum):
    """`sourceMode` enum (§8.2, LOCKED) — PLACEHOLDER/SYNTHETIC only (OUT-5).

    `live_feed` is intentionally NOT a member: a live feed is not representable,
    so a `live_feed` value RAISES `pydantic.ValidationError` (fail closed).
    """

    PLACEHOLDER = "placeholder"
    SYNTHETIC = "synthetic"


# --------------------------------------------------------------------------- #
# §8.1 CreatorRecord (FR-3.8, AGGREGATE/SYNTHETIC, OUT-4 / INV-6).
# --------------------------------------------------------------------------- #


class CreatorRecord(BaseModel):
    """`CreatorRecord` (§8.1, LOCKED) — aggregate/synthetic creator-discovery data.

    Frozen + `extra="forbid"`: immutable once parsed and rejects unknown fields,
    so a malformed payload fails closed (V-1, §9.2). `display_handle` is a
    SYNTHETIC handle — never a real minor's handle. Three INV-6 protections are
    enforced at the type level: `audience_segment` has no minor segment,
    `data_mode` cannot be `live_scrape`, and the `is_minor` validator BLOCKS any
    `is_minor=True` record (§9 V-3). `fit_score`/`authenticity_score` are 0–1.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    display_handle: str = Field(min_length=1, alias="displayHandle")
    channel: Channel
    audience_segment: AudienceSegment = Field(alias="audienceSegment")
    fit_score: float = Field(ge=0.0, le=1.0, alias="fitScore")
    authenticity_score: float = Field(ge=0.0, le=1.0, alias="authenticityScore")
    rationale: str | None = None
    data_mode: CreatorDataMode = Field(alias="dataMode")
    is_minor: bool = Field(alias="isMinor")
    provenance: Provenance

    @field_validator("is_minor")
    @classmethod
    def _reject_minor(cls, value: bool) -> bool:  # noqa: FBT001
        """BLOCK any `is_minor=True` record — fail closed (§9 V-3 / INV-6).

        No child-keyed creator record is representable: a true value RAISES a
        ValidationError rather than being persisted.
        """
        if value:
            raise ValueError("CreatorRecord blocked: minors are not targetable (INV-6, §9 V-3)")
        return value


# --------------------------------------------------------------------------- #
# §8.2 SentimentRecord (FR-3.10, PLACEHOLDER, OUT-5).
# --------------------------------------------------------------------------- #


class SentimentRecord(BaseModel):
    """`SentimentRecord` (§8.2, LOCKED) — placeholder/synthetic sentiment data.

    Frozen + `extra="forbid"`: immutable once parsed and rejects unknown fields,
    so a malformed payload fails closed (V-1, §9.2). `excerpt` is synthetic — no
    real-user PII (INV-1). `source_mode` cannot be `live_feed` (closed enum,
    OUT-5). `score` is the optional -1..1 polarity magnitude.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    channel: Channel
    topic: str = Field(min_length=1)
    sentiment: Sentiment
    score: float | None = Field(default=None, ge=-1.0, le=1.0)
    excerpt: str | None = None
    source_mode: SentimentSourceMode = Field(alias="sourceMode")
    observed_at: str = Field(min_length=1, alias="observedAt")
    provenance: Provenance

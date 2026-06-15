"""Marketing schema records — content-as-data contracts (CONTENT_SPEC §2–§8).

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: no `anthropic` / `langgraph` /
I/O imports. The pass/fail gate (`ValidationResult`, §9.6) lives in
`app/core/eval_gate.py`; records here only model the data so they CAN be gated.
"""

from __future__ import annotations

from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    GenTier,
    ImageArtifact,
    Stage,
    StageArtifact,
    VideoArtifact,
)
from app.marketing.schemas.discovery import (
    AudienceSegment,
    CreatorDataMode,
    CreatorRecord,
    Sentiment,
    SentimentRecord,
    SentimentSourceMode,
)
from app.marketing.schemas.geo import GeoContentPiece, GeoStructure
from app.marketing.schemas.scheduling import (
    DispatchMode,
    DispatchStatus,
    ScheduledPost,
)

__all__ = [
    "ArtifactStatus",
    "AudienceSegment",
    "ConceptArtifact",
    "CreatorDataMode",
    "CreatorRecord",
    "DispatchMode",
    "DispatchStatus",
    "GenTier",
    "GeoContentPiece",
    "GeoStructure",
    "ImageArtifact",
    "ScheduledPost",
    "Sentiment",
    "SentimentRecord",
    "SentimentSourceMode",
    "Stage",
    "StageArtifact",
    "VideoArtifact",
]

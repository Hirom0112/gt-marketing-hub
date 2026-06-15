"""Staged-pipeline artifacts — the concept→image→video content-as-data chain (S6 §4).

§4 models the staged content-production pipeline as data: one piece flows through
`concept → image → video`, each a typed, versioned, validated proposal (INV-2),
never a free-form blob — a malformed payload RAISES `pydantic.ValidationError`
rather than coercing into a write, and §9.2 Rule V-1 operates on exactly these
shapes (closed `stage`/`status`/`genTier` enums, `extra="forbid"`, every [req]
field enforced — fail closed).

**OUT-1**: the image and video stages are PLACEHOLDER in v1. No live media is
generated; each carries a synthetic `placeholderUri` stand-in and a
`costEstimateRef` STRING that POINTS at the TECH_STACK cost model — there is NO
hardcoded numeric price field anywhere on these records (INV-11). A live
`liveAssetUri` is optional and stays empty until post-v1.

The §4.1 `ConceptArtifact` is REAL in v1 (LLM-generated concept + copy). Like
S4's `ContentCandidate`, the spec field `copy` shadows `BaseModel.copy`, so the
Python attribute is `copy_text` with a `copy` wire alias (mirrors
`app/ai/schemas/content.py`).

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: imports only
`app.ai.schemas.content` (reusing the LOCKED `Provenance` / `HumanDecision`),
pydantic and stdlib — no `anthropic` / `langgraph` / I/O. Cross-record links
(`sourceCandidateRef`, `conceptRef`, `imageRef`, `validation`) are plain id refs,
not imported types, to avoid an import cycle. CONTENT_SPEC uses camelCase wire
names; the Python attributes are snake_case (the source of truth) with pydantic
aliases + `populate_by_name=True`, matching `geo.py` / `content.py`.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.content import HumanDecision, Provenance


class Stage(StrEnum):
    """`stage` enum (§4, LOCKED) — the pipeline stage a record belongs to."""

    CONCEPT = "concept"
    IMAGE = "image"
    VIDEO = "video"


class ArtifactStatus(StrEnum):
    """`status` enum (§4, LOCKED). `placeholder` = v1 image/video stand-in (OUT-1)."""

    PENDING = "pending"
    GENERATED = "generated"
    PLACEHOLDER = "placeholder"
    SELECTED = "selected"
    REJECTED = "rejected"


class GenTier(StrEnum):
    """`genTier` enum (§4.2, LOCKED) — image generation tier (optional)."""

    DRAFT = "draft"
    FINAL = "final"


class StageArtifact(BaseModel):
    """`StageArtifact` envelope (§4, LOCKED) — common to concept/image/video.

    Frozen + `extra="forbid"`: the proposal is immutable once parsed and rejects
    any unexpected field, so a malformed payload fails closed (V-1, §9.2) rather
    than being coerced into a write (INV-2). `pipeline_id` groups the
    concept→image→video records for one piece. `cost_estimate_ref` is a STRING
    pointer at the TECH_STACK cost model — NEVER a hardcoded numeric price
    (INV-11): no price/amount field exists on this envelope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    pipeline_id: UUID = Field(alias="pipelineId")
    stage: Stage
    status: ArtifactStatus
    # A pointer at the TECH_STACK cost model — never a hardcoded price (INV-11).
    cost_estimate_ref: str = Field(min_length=1, alias="costEstimateRef")
    provenance: Provenance
    decision: HumanDecision | None = None


class ConceptArtifact(StageArtifact):
    """`ConceptArtifact` (§4.1, LOCKED) — stage=concept, REAL in v1.

    The LLM-generated concept + copy for one piece. `copy` shadows
    `BaseModel.copy`, so the attribute is `copy_text` with a `copy` wire alias
    (mirrors S4's `ContentCandidate`). `validation` references the §9.6
    `ValidationResult` by id; the gate runs in `app/core/eval_gate.py` (A-10).
    """

    source_candidate_ref: UUID = Field(alias="sourceCandidateRef")
    concept: str = Field(min_length=1)
    # §4.1 names this `copy`; that shadows `BaseModel.copy`, so the attribute is
    # `copy_text` and the spec/wire name is kept as an alias (S4 parity).
    copy_text: str = Field(min_length=1, alias="copy")
    image_brief: str | None = Field(default=None, alias="imageBrief")
    validation: str = Field(min_length=1)


class ImageArtifact(StageArtifact):
    """`ImageArtifact` (§4.2, LOCKED) — stage=image, PLACEHOLDER in v1 (OUT-1).

    No live media is generated in v1: `placeholder_uri` is the required synthetic
    stand-in and `live_asset_uri` is optional/empty until post-v1. The cost is a
    `cost_estimate_ref` pointer (inherited), never a numeric price.
    """

    concept_ref: UUID = Field(alias="conceptRef")
    image_brief: str = Field(min_length=1, alias="imageBrief")
    placeholder_uri: str = Field(min_length=1, alias="placeholderUri")
    live_asset_uri: str | None = Field(default=None, alias="liveAssetUri")
    gen_tier: GenTier | None = Field(default=None, alias="genTier")
    watermark_note: str | None = Field(default=None, alias="watermarkNote")


class VideoArtifact(StageArtifact):
    """`VideoArtifact` (§4.3, LOCKED) — stage=video, PLACEHOLDER in v1 (OUT-1).

    No live media is generated in v1: `placeholder_uri` is the required synthetic
    stand-in and `live_asset_uri` is optional/empty until post-v1. `image_ref` is
    optional (a video may not derive from a specific image stage).
    """

    image_ref: UUID | None = Field(default=None, alias="imageRef")
    video_script: str = Field(min_length=1, alias="videoScript")
    duration_sec: float | None = Field(default=None, alias="durationSec")
    placeholder_uri: str = Field(min_length=1, alias="placeholderUri")
    live_asset_uri: str | None = Field(default=None, alias="liveAssetUri")

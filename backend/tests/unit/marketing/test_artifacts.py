"""StageArtifact envelope + concept/image/video tests — S6 §4 (LOCKED).

§4 is the staged content-production pipeline as data: one piece flows
concept → image → video, each a typed/validated record (INV-2). The image
and video stages are PLACEHOLDER in v1 (OUT-1): no live media is generated —
each carries a synthetic `placeholderUri` and a `costEstimateRef` that POINTS
at the TECH_STACK cost model (never a hardcoded numeric price). A live
`liveAssetUri` is optional and empty until post-v1.

Per CLAUDE.md §4.1 these are pure red→green schema tests: closed `stage`/
`status`/`genTier` enums (out-of-range RAISES), required fields enforced,
`extra="forbid"` rejects unknown fields — the records fail closed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.schemas.content import GeneratedBy, Provenance
from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    GenTier,
    ImageArtifact,
    Stage,
    VideoArtifact,
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.LLM, created_at="2026-06-14T00:00:00Z")


def _image(**overrides: object) -> ImageArtifact:
    base: dict[str, object] = {
        "id": uuid4(),
        "pipelineId": uuid4(),
        "stage": Stage.IMAGE,
        "status": ArtifactStatus.PLACEHOLDER,
        "costEstimateRef": "tech_stack:media_gen.image",
        "provenance": _provenance(),
        "conceptRef": uuid4(),
        "imageBrief": "A bright classroom of focused gifted learners, warm light.",
        "placeholderUri": "synthetic://placeholder/image/abc123.png",
    }
    base.update(overrides)
    return ImageArtifact(**base)  # type: ignore[arg-type]


def _video(**overrides: object) -> VideoArtifact:
    base: dict[str, object] = {
        "id": uuid4(),
        "pipelineId": uuid4(),
        "stage": Stage.VIDEO,
        "status": ArtifactStatus.PLACEHOLDER,
        "costEstimateRef": "tech_stack:media_gen.video",
        "provenance": _provenance(),
        "videoScript": "Open on a learner solving a proof; voiceover on mastery.",
        "placeholderUri": "synthetic://placeholder/video/def456.mp4",
    }
    base.update(overrides)
    return VideoArtifact(**base)  # type: ignore[arg-type]


def test_placeholder_artifacts_schema_valid() -> None:
    """Image/video PLACEHOLDER records validate; no hardcoded price; liveAssetUri opt.

    §4.2/§4.3 + OUT-1: v1 image/video are placeholders carrying a non-empty
    `placeholderUri` and a `costEstimateRef` STRING (a pointer at the cost
    model, never a numeric price). `liveAssetUri` is optional/empty in v1.
    """
    image = _image()
    video = _video()

    # status is placeholder in v1.
    assert image.status is ArtifactStatus.PLACEHOLDER
    assert video.status is ArtifactStatus.PLACEHOLDER

    # placeholderUri is a non-empty synthetic stand-in.
    assert image.placeholder_uri
    assert video.placeholder_uri

    # costEstimateRef is a STRING pointer at the cost model — NOT a numeric price.
    assert isinstance(image.cost_estimate_ref, str)
    assert isinstance(video.cost_estimate_ref, str)
    # There is no hardcoded price field anywhere on these records.
    assert "cost_estimate" in image.model_fields
    assert not any(
        name in image.model_fields
        for name in ("price", "cost", "cost_usd", "amount")
    )
    assert not any(
        name in video.model_fields
        for name in ("price", "cost", "cost_usd", "amount")
    )

    # liveAssetUri is optional/empty in v1.
    assert image.live_asset_uri is None
    assert video.live_asset_uri is None

    # stage is correct and the records carry their required refs.
    assert image.stage is Stage.IMAGE
    assert video.stage is Stage.VIDEO
    assert image.image_brief
    assert video.video_script

    # genTier (image) is a CLOSED enum and optional.
    assert image.gen_tier is None
    assert _image(genTier="draft").gen_tier is GenTier.DRAFT
    with pytest.raises(ValidationError):
        _image(genTier="ultra")


def test_image_brief_costref_required() -> None:
    """A missing required field (placeholderUri, costEstimateRef) RAISES (fail closed)."""
    with pytest.raises(ValidationError):
        ImageArtifact(  # type: ignore[call-arg]
            id=uuid4(),
            pipelineId=uuid4(),
            stage=Stage.IMAGE,
            status=ArtifactStatus.PLACEHOLDER,
            provenance=_provenance(),
            conceptRef=uuid4(),
            imageBrief="brief",
            # placeholderUri + costEstimateRef missing
        )
    # Empty placeholderUri RAISES (min_length=1).
    with pytest.raises(ValidationError):
        _image(placeholderUri="")
    # An unknown extra field is rejected (extra="forbid").
    with pytest.raises(ValidationError):
        _image(unexpected="nope")


def test_concept_artifact_is_real_in_v1() -> None:
    """ConceptArtifact (§4.1) is REAL in v1: concept/copy/validation required.

    `copy` shadows `BaseModel.copy`, so the attribute is `copy_text` with a
    `copy` wire alias — mirrors S4's ContentCandidate fix.
    """
    concept = ConceptArtifact(  # type: ignore[call-arg]
        id=uuid4(),
        pipelineId=uuid4(),
        stage=Stage.CONCEPT,
        status=ArtifactStatus.GENERATED,
        costEstimateRef="tech_stack:llm.concept",
        provenance=_provenance(),
        sourceCandidateRef=uuid4(),
        concept="Mastery-based learning for the profoundly gifted.",
        copy="Stop waiting on the class. Start moving at your pace.",
        validation="val-1",
    )
    assert concept.stage is Stage.CONCEPT
    assert concept.copy_text.startswith("Stop waiting")
    assert concept.validation == "val-1"
    assert concept.image_brief is None

    # `stage` is a CLOSED enum.
    with pytest.raises(ValidationError):
        ConceptArtifact(  # type: ignore[call-arg]
            id=uuid4(),
            pipelineId=uuid4(),
            stage="teaser",
            status=ArtifactStatus.GENERATED,
            costEstimateRef="tech_stack:llm.concept",
            provenance=_provenance(),
            sourceCandidateRef=uuid4(),
            concept="x",
            copy="y",
            validation="val-1",
        )

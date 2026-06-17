"""Staged-pipeline advance-guard tests — S6 §4 (LOCKED, cheapest-first).

§4 RULE (LOCKED): an artifact may only advance to the next (more expensive)
stage when the prior stage is `selected` by a human (FR-3.5) AND the prior stage
holds a passing `ValidationResult`. This enforces cheapest-first and prevents
spend on unvalidated concepts. Stage order: concept → image → video
(cheapest → costliest).

The guard is DETERMINISTIC and FAIL-CLOSED (INV-2/INV-3): `advance` RAISES a
typed `PipelineAdvanceBlocked` rather than silently advancing when the gate is
not satisfied — never spending on an unselected / unvalidated artifact.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.ai.schemas.content import GeneratedBy, Provenance
from app.core.eval_gate import RuleVerdict, ValidationResult
from app.marketing.pipeline import (
    PipelineAdvanceBlocked,
    advance,
    can_advance,
    next_stage,
)
from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    ImageArtifact,
    Stage,
    VideoArtifact,
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.LLM, created_at="2026-06-14T00:00:00Z")


def _passing() -> ValidationResult:
    return ValidationResult(
        v1_schema=RuleVerdict.PASS,
        v2_grounding=RuleVerdict.PASS,
        v3_coppa=RuleVerdict.PASS,
        v4_onbrand=RuleVerdict.PASS,
        passed=True,
    )


def _failing() -> ValidationResult:
    return ValidationResult(
        v1_schema=RuleVerdict.PASS,
        v2_grounding=RuleVerdict.FAIL,
        v3_coppa=RuleVerdict.PASS,
        v4_onbrand=RuleVerdict.PASS,
        passed=False,
        failed_rules=["v2_grounding"],
    )


def _concept(status: ArtifactStatus) -> ConceptArtifact:
    return ConceptArtifact(  # type: ignore[call-arg]
        id=uuid4(),
        pipelineId=uuid4(),
        stage=Stage.CONCEPT,
        status=status,
        costEstimateRef="tech_stack:llm.concept",
        provenance=_provenance(),
        sourceCandidateRef=uuid4(),
        concept="Mastery-based learning for the profoundly gifted.",
        copy="Stop waiting on the class. Start moving at your pace.",
        validation="val-1",
    )


def _image(status: ArtifactStatus) -> ImageArtifact:
    return ImageArtifact(  # type: ignore[call-arg]
        id=uuid4(),
        pipelineId=uuid4(),
        stage=Stage.IMAGE,
        status=status,
        costEstimateRef="tech_stack:media_gen.image",
        provenance=_provenance(),
        conceptRef=uuid4(),
        imageBrief="A bright classroom of focused gifted learners, warm light.",
        placeholderUri="synthetic://placeholder/image/abc123.png",
    )


def test_pipeline_blocks_advance_without_selection_and_validation() -> None:
    """A stage advances only when prior is human-`selected` AND validation passes (§4).

    Covers concept→image and image→video, both the allow and the two block
    paths (not-selected, failing-validation), plus that `advance` is fail-closed
    (RAISES, never silently advances) and that video has no next stage.
    """
    passing = _passing()
    failing = _failing()

    # (a) SELECTED + passing validation ⇒ can_advance True, advance → next stage.
    concept_ok = _concept(ArtifactStatus.SELECTED)
    assert can_advance(concept_ok, validation=passing) is True
    assert advance(concept_ok, validation=passing) is Stage.IMAGE

    image_ok = _image(ArtifactStatus.SELECTED)
    assert can_advance(image_ok, validation=passing) is True
    assert advance(image_ok, validation=passing) is Stage.VIDEO

    # (b) NOT selected (e.g. status=generated) + passing validation ⇒ False / RAISES.
    concept_unselected = _concept(ArtifactStatus.GENERATED)
    assert can_advance(concept_unselected, validation=passing) is False
    with pytest.raises(PipelineAdvanceBlocked):
        advance(concept_unselected, validation=passing)

    image_unselected = _image(ArtifactStatus.PLACEHOLDER)
    assert can_advance(image_unselected, validation=passing) is False
    with pytest.raises(PipelineAdvanceBlocked):
        advance(image_unselected, validation=passing)

    # (c) SELECTED + FAILING validation ⇒ False / RAISES (no spend on unvalidated).
    concept_failval = _concept(ArtifactStatus.SELECTED)
    assert can_advance(concept_failval, validation=failing) is False
    with pytest.raises(PipelineAdvanceBlocked):
        advance(concept_failval, validation=failing)

    # (d) next_stage walks concept→image→video→None (can't advance past video).
    assert next_stage(Stage.CONCEPT) is Stage.IMAGE
    assert next_stage(Stage.IMAGE) is Stage.VIDEO
    assert next_stage(Stage.VIDEO) is None


def test_advance_past_video_is_blocked() -> None:
    """A `selected` + passing video has no costlier stage ⇒ advance RAISES (§4)."""
    passing = _passing()
    # A selected, validated video still cannot advance — there is no next stage.
    video = VideoArtifact(  # type: ignore[call-arg]
        id=uuid4(),
        pipelineId=uuid4(),
        stage=Stage.VIDEO,
        status=ArtifactStatus.SELECTED,
        costEstimateRef="tech_stack:media_gen.video",
        provenance=_provenance(),
        videoScript="Open on a learner solving a proof; voiceover on mastery.",
        placeholderUri="synthetic://placeholder/video/def456.mp4",
    )
    with pytest.raises(PipelineAdvanceBlocked):
        advance(video, validation=passing)

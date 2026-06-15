"""Staged-pipeline advance guard — the cheapest-first spend gate (S6 §4, LOCKED).

§4 RULE (LOCKED): "an artifact may only advance to the next (more expensive)
stage when the prior stage is `selected` by a human (FR-3.5) AND the prior stage
holds a passing `ValidationResult`. This enforces cheapest-first and prevents
spend on unvalidated concepts." Stage order: concept → image → video
(cheapest → costliest).

This module is the DETERMINISTIC core of §4 (INV-2): it never calls an LLM,
never writes, never touches the wall clock — it only computes, from an artifact's
human-`selected` status and a passing `ValidationResult`, whether the costlier
next stage may be unlocked. The gate is FAIL-CLOSED (INV-3): :func:`advance`
RAISES :class:`PipelineAdvanceBlocked` rather than silently advancing, so media
spend can never happen on an unselected / unvalidated concept.

Pure per CLAUDE.md §3 / ARCHITECTURE.md §3: imports only the artifact schemas
(`app.marketing.schemas.artifacts`) and the `ValidationResult` verdict shape
(`app.core.eval_gate`) — no `anthropic` / `langgraph` / I/O / `datetime.now`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.marketing.schemas.artifacts import ArtifactStatus, Stage

if TYPE_CHECKING:
    from app.core.eval_gate import ValidationResult
    from app.marketing.schemas.artifacts import StageArtifact

# The pipeline ladder, cheapest → costliest (§4, LOCKED). Each stage maps to the
# next, costlier stage it may unlock; `video` (the costliest) maps to nothing.
_NEXT_STAGE: dict[Stage, Stage | None] = {
    Stage.CONCEPT: Stage.IMAGE,
    Stage.IMAGE: Stage.VIDEO,
    Stage.VIDEO: None,
}


class PipelineAdvanceBlocked(Exception):
    """Raised when :func:`advance` is called but the §4 gate is not satisfied.

    Fail-closed (INV-3): the guard never silently advances an unselected /
    unvalidated artifact, nor past the costliest stage — it RAISES so the caller
    cannot accidentally trigger media spend on an ungated concept.
    """


def next_stage(stage: Stage) -> Stage | None:
    """The next, costlier stage after `stage`, or ``None`` past the ladder (§4).

    concept → image → video → None. ``None`` means there is no costlier stage to
    advance into (a `video` is the end of the pipeline).
    """
    return _NEXT_STAGE[stage]


def can_advance(artifact: StageArtifact, *, validation: ValidationResult) -> bool:
    """Whether `artifact` may advance to the next (costlier) stage (§4, LOCKED).

    Returns ``True`` only when BOTH conditions hold (cheapest-first):

    * the artifact is human-`selected` (`status == ArtifactStatus.SELECTED`,
      FR-3.5), and
    * it holds a PASSING `ValidationResult` (`validation.passed is True`), and
    * there is a costlier stage to advance into (`next_stage(...)` is not
      ``None``).

    Any other state ⇒ ``False`` (no spend on an unselected / unvalidated /
    end-of-pipeline artifact). Pure and deterministic.
    """
    if artifact.status is not ArtifactStatus.SELECTED:
        return False
    if validation.passed is not True:
        return False
    return next_stage(artifact.stage) is not None


def advance(artifact: StageArtifact, *, validation: ValidationResult) -> Stage:
    """Advance `artifact` to the next stage, or RAISE if the gate blocks (§4).

    Returns the costlier next :class:`Stage` when :func:`can_advance` is
    satisfied; otherwise RAISES :class:`PipelineAdvanceBlocked` (fail-closed,
    INV-3) — it NEVER silently advances an unselected / unvalidated artifact or
    one already at the costliest stage. This prevents media spend on an ungated
    concept (§4 "prevents spend on unvalidated concepts").
    """
    if not can_advance(artifact, validation=validation):
        raise PipelineAdvanceBlocked(
            f"cannot advance {artifact.stage.value} artifact {artifact.id}: "
            f"requires status=selected (got {artifact.status.value}), a passing "
            f"ValidationResult (passed={validation.passed}), and a costlier next "
            f"stage (next={next_stage(artifact.stage)})"
        )
    nxt = next_stage(artifact.stage)
    # can_advance guarantees a non-None next stage; assert for the type checker.
    assert nxt is not None  # noqa: S101 — invariant established by can_advance.
    return nxt

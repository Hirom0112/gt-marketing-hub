"""Consolidated eval-suite runner — the green/red scoreboard (FR-4.5; ARCH §6).

One runner over all four FR-4.x evals — `nudge_trigger` (FR-4.1),
`doc_extraction` (FR-4.2), `message_safety_grounding` (FR-4.3), and
`geo_tracking` (FR-4.4) — folded into a single scoreboard: one `EvalRow` per
eval carrying its `eval_name`, a representative `score`, the params-derived
`threshold`, and the `passed` verdict, plus one `overall_green` roll-up.

The consolidation is **fail-closed** (INV-3): `overall_green = all(rows
passed)`, so a single red eval turns the whole scoreboard red — the same
discipline each per-eval gate enforces, lifted to the suite. This backs the
CI gate (`uv run pytest tests/evals/`) and its runtime equivalent
(`POST /evals/run` / `GET /evals`, ARCH §6).

Every threshold a row reports READS from `params` (INV-11) — never hardcoded;
a param drift moves the threshold a row reports and the verdict that follows.
The runner is **deterministic**: it consumes already-sampled geo observations
(the caller fixes the seed) and an injected `brand_judge`, takes no wall-clock,
and reaches no network.

Orchestration edge (CLAUDE §7): this module composes the metrics layer
(`app.evals.metrics`), the grounding gate (`app.core.eval_gate`), and the geo
eval (`app.evals.geo_tracking_eval`) over their data schemas. It imports NO
`anthropic`/`langgraph` — the gate's judge is injected by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.adapters.geo_sampling.base import GeoObservation
from app.ai.schemas.close_tips import CloseTip, CloseTipsProposal
from app.ai.schemas.enrollment_draft import Claim, DraftAction, EnrollmentDraftProposal
from app.core.eval_gate import BrandJudge, evaluate_message
from app.core.params import Params
from app.core.settings import Settings
from app.evals.geo_tracking_eval import evaluate_geo_tracking
from app.evals.metrics import evaluate_doc_extraction, evaluate_nudge

# Stable eval identifiers — the scoreboard keys the UI/API and tests pin on.
NUDGE_TRIGGER = "nudge_trigger"
DOC_EXTRACTION = "doc_extraction"
MESSAGE_SAFETY_GROUNDING = "message_safety_grounding"
CLOSE_TIPS = "close_tips"
GEO_TRACKING = "geo_tracking"

# The operator-facing audience for close-tips advice (a COPPA-safe audience — the
# tips are for the operator, never sent to a minor; a minor-targeting tip still
# trips V-3 via the body text patterns regardless).
_CLOSE_TIPS_AUDIENCE = "leadership"


class NudgeCounts(TypedDict):
    """The confusion-matrix counts the nudge eval scores over a golden set."""

    tp: int
    fp: int
    fn: int


class EvalRow(BaseModel):
    """One eval's line on the consolidated scoreboard (FR-4.5).

    Frozen: a verdict over one eval's golden inputs is an immutable record,
    never mutated after it is computed.

    Attributes:
        eval_name: The stable eval identifier (one of the four constants above).
        score: A single representative metric for the eval — precision for
            nudge, accuracy for doc/grounding, coverage_mean for geo. It is a
            display/summary number; `passed` is the authoritative verdict.
        threshold: The params-derived reference the row is judged against
            (INV-11) — never hardcoded.
        passed: The eval's gate verdict. For nudge this reflects the FULL
            precision-AND-recall gate even though `score`/`threshold` surface
            precision only; for geo it reflects sufficient-samples (`enabled`).
    """

    model_config = ConfigDict(frozen=True)

    eval_name: str
    score: float
    threshold: float
    passed: bool


class EvalSuiteResult(BaseModel):
    """The consolidated green/red scoreboard over all four evals (FR-4.5).

    Frozen. `overall_green` is fail-closed: True only when every row passed,
    so a single red eval turns the whole scoreboard red (INV-3).

    Attributes:
        rows: One `EvalRow` per eval, in suite order.
        overall_green: `all(r.passed for r in rows)` — the roll-up verdict.
    """

    model_config = ConfigDict(frozen=True)

    rows: list[EvalRow]
    overall_green: bool


def _draft_from_golden_row(row: Mapping[str, object]) -> EnrollmentDraftProposal:
    """Build an `EnrollmentDraftProposal` from one grounding-golden jsonl row.

    Mirrors `test_message_safety_grounding.py`: `action`/`family_id`/`body` map
    directly and each claim becomes a `Claim(text, source_ref)`.
    """
    claims_raw = row["claims"]
    assert isinstance(claims_raw, Sequence)
    return EnrollmentDraftProposal(
        action=DraftAction(str(row["action"])),
        family_id=UUID(str(row["family_id"])),
        body=str(row["body"]),
        claims=[Claim(text=str(c["text"]), source_ref=c["source_ref"]) for c in claims_raw],
    )


def _grounding_row(
    golden_drafts: Sequence[Mapping[str, object]],
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None,
) -> EvalRow:
    """Score message-safety/grounding accuracy over the golden draft set (FR-4.3).

    Reuses the exact scoring approach of
    `test_message_safety_grounding.py::test_draft_golden_set_meets_threshold`:
    run `evaluate_message` per row with the injected on-brand `brand_judge` (so
    V-4 doesn't deny the PASS rows) and the row's `audience`, then accuracy =
    fraction of rows where `result.passed == row["expected_passed"]`. `passed`
    is `accuracy >= min_grounding` (the params floor, INV-11).
    """
    min_grounding = params.eval_thresholds.message_safety_grounding.min_grounding
    if not golden_drafts:
        # No golden rows ⇒ no evidence the gate holds ⇒ fail closed (INV-3).
        return EvalRow(
            eval_name=MESSAGE_SAFETY_GROUNDING,
            score=0.0,
            threshold=min_grounding,
            passed=False,
        )

    correct = 0
    for row in golden_drafts:
        proposal = _draft_from_golden_row(row)
        audience = row.get("audience")
        result = evaluate_message(
            proposal,
            settings=settings,
            params=params,
            brand_judge=brand_judge,
            audience=audience if isinstance(audience, str) else None,
        )
        if result.passed == row["expected_passed"]:
            correct += 1

    accuracy = correct / len(golden_drafts)
    return EvalRow(
        eval_name=MESSAGE_SAFETY_GROUNDING,
        score=accuracy,
        threshold=min_grounding,
        passed=accuracy >= min_grounding,
    )


def _close_tips_from_golden_row(row: Mapping[str, object]) -> CloseTipsProposal:
    """Build a `CloseTipsProposal` from one close-tips golden jsonl row (S9 W5)."""
    tips_raw = row["tips"]
    assert isinstance(tips_raw, Sequence)
    return CloseTipsProposal(
        family_id=UUID(str(row["family_id"])),
        tips=[CloseTip(text=str(t["text"]), source_ref=t["source_ref"]) for t in tips_raw],
    )


def _close_tips_row(
    close_tips_golden: Sequence[Mapping[str, object]],
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None,
) -> EvalRow:
    """Score the close-tips grounding accuracy over the golden set (S9 W5; FR-4.3).

    Same approach as the grounding row: run `evaluate_message` per row (the
    close-tips proposal crosses the SAME canonical gate — A-10) with the injected
    on-brand judge and the operator audience, then accuracy = fraction of rows
    where `result.passed == row["expected_passed"]`. `passed` is
    `accuracy >= min_grounding` (the params `close_tips` floor, INV-11). An empty
    golden set ⇒ no evidence ⇒ fail closed (INV-3).
    """
    min_grounding = params.eval_thresholds.close_tips.min_grounding
    if not close_tips_golden:
        return EvalRow(eval_name=CLOSE_TIPS, score=0.0, threshold=min_grounding, passed=False)

    correct = 0
    for row in close_tips_golden:
        proposal = _close_tips_from_golden_row(row)
        result = evaluate_message(
            proposal,
            settings=settings,
            params=params,
            brand_judge=brand_judge,
            audience=_CLOSE_TIPS_AUDIENCE,
        )
        if result.passed == row["expected_passed"]:
            correct += 1

    accuracy = correct / len(close_tips_golden)
    return EvalRow(
        eval_name=CLOSE_TIPS,
        score=accuracy,
        threshold=min_grounding,
        passed=accuracy >= min_grounding,
    )


def run_suite(
    *,
    settings: Settings,
    params: Params,
    golden_drafts: Sequence[Mapping[str, object]],
    nudge_counts: NudgeCounts,
    doc_golden: tuple[Mapping[str, object], Mapping[str, object]],
    geo_observations: Sequence[GeoObservation],
    close_tips_golden: Sequence[Mapping[str, object]] = (),
    brand_judge: BrandJudge | None = None,
) -> EvalSuiteResult:
    """Run all four FR-4.x evals and fold them into one scoreboard (FR-4.5).

    Each eval contributes one `EvalRow`; `overall_green = all(rows passed)` —
    fail-closed, so a single red eval turns the whole scoreboard red (INV-3).
    Every threshold is read from `params` (INV-11). Deterministic: the caller
    supplies fixed geo observations (a fixed seed) and an injected judge; no
    wall-clock, no network.

    Args:
        settings: The env seam; `settings.llm_available` drives the grounding
            gate's V-4 "judge unavailable ⇒ deny" path.
        params: The validated params — supplies every eval threshold.
        golden_drafts: The grounding golden rows (the committed
            `enrollment_drafts.jsonl`), each scored via `evaluate_message`.
        nudge_counts: `{tp, fp, fn}` over the nudge golden set (FR-4.1).
        doc_golden: `(predicted, ground_truth)` field maps for doc extraction
            (FR-4.2).
        geo_observations: Already-sampled GEO observations (from the simulated
            adapter at a fixed seed) — at least `min_samples_per_prompt` runs
            for a non-red geo row (FR-4.4).
        close_tips_golden: The close-tips grounding golden rows (S9 W5); when
            non-empty a `close_tips` row is appended, scored via `evaluate_message`.
            Empty (the default) ⇒ no close-tips row (existing 4-row callers
            unchanged).
        brand_judge: An injected brand-conformance judge for the grounding
            gate's V-4; `None` ⇒ V-4 denies when no key (fail-closed).

    Returns:
        An `EvalSuiteResult` with one row per eval and the overall verdict.
    """
    # --- nudge_trigger (FR-4.1) ---------------------------------------------
    # `passed` reflects the FULL precision-AND-recall gate; `score`/`threshold`
    # surface precision/min_precision as the representative pair.
    nudge_eval = evaluate_nudge(
        tp=nudge_counts["tp"],
        fp=nudge_counts["fp"],
        fn=nudge_counts["fn"],
        params=params,
    )
    nudge_row = EvalRow(
        eval_name=NUDGE_TRIGGER,
        score=nudge_eval.precision,
        threshold=nudge_eval.min_precision,
        passed=nudge_eval.passed,
    )

    # --- doc_extraction (FR-4.2) --------------------------------------------
    predicted, ground_truth = doc_golden
    doc_eval = evaluate_doc_extraction(predicted, ground_truth, params=params)
    doc_row = EvalRow(
        eval_name=DOC_EXTRACTION,
        score=doc_eval.accuracy,
        threshold=doc_eval.min_accuracy,
        passed=doc_eval.passed,
    )

    # --- message_safety_grounding (FR-4.3) ----------------------------------
    grounding_row = _grounding_row(
        golden_drafts,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
    )

    # --- close_tips (S9 W5; FR-4.3) -----------------------------------------
    # The "how to close" tips grounding row — included ONLY when a close-tips golden
    # set is supplied, so existing 4-row callers are unchanged (the row is additive,
    # not a new always-on gate). Crosses the SAME canonical gate (A-10).
    close_tips_row = (
        _close_tips_row(
            close_tips_golden, settings=settings, params=params, brand_judge=brand_judge
        )
        if close_tips_golden
        else None
    )

    # --- geo_tracking (FR-4.4) ----------------------------------------------
    # `passed` reflects sufficient samples (`enabled`); `score` is coverage_mean
    # and `threshold` is the min_samples_per_prompt floor (as a float reference).
    geo_eval = evaluate_geo_tracking(geo_observations, params=params)
    geo_row = EvalRow(
        eval_name=GEO_TRACKING,
        score=geo_eval.coverage_mean,
        threshold=float(params.eval_thresholds.geo_tracking.min_samples_per_prompt),
        passed=geo_eval.enabled,
    )

    rows = [nudge_row, doc_row, grounding_row, geo_row]
    if close_tips_row is not None:
        rows.append(close_tips_row)
    return EvalSuiteResult(rows=rows, overall_green=all(r.passed for r in rows))

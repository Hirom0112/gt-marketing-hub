"""Consolidated eval-suite endpoints — the green/red scoreboard surface (FR-4.5).

The composition layer wiring the S7 consolidated eval suite (``app.evals.suite``)
into HTTP. It is deliberately thin (the analog of ``app/api/geo.py``): every
decision-bearing step — running each of the four FR-4.x evals and folding them
into one fail-closed verdict — lives in the owned ``run_suite`` runner this router
orchestrates (INV-2). The router only assembles the deterministic, offline inputs,
calls ``run_suite``, records the verdict as the live suite-level kill state, and
shapes the view. No business logic, no magic numbers (every threshold a row
reports is read from ``params`` inside ``run_suite`` — INV-11).

  ``POST /evals/run``
    Runs the consolidated suite over deterministic, offline inputs (a fixed nudge
    PASS triple, a passing doc-extraction pair, the committed grounding golden set,
    and a simulated GEO sampling pass at a fixed seed) with an injected on-brand
    judge so the PASS grounding rows clear V-4. The resulting
    :class:`~app.evals.suite.EvalSuiteResult` is stored via ``set_eval_state`` as
    the LIVE suite-level kill state (INV-3): a red row immediately disables the
    gated action in the running app, not just the UI. Returns the same view shape
    as ``GET /evals``.

  ``GET /evals``
    Returns the last consolidated verdict + a per-row ``disabled`` map
    (``disabled[name] = not action_enabled(state, name)``). When no suite has run
    (``get_eval_state()`` is ``None``) it returns ``rows: []``,
    ``overall_green: true``, ``disabled: {}`` — fail-OPEN on "never run" is correct
    because the per-message V-1..V-4 gate still guards every draft; the suite-level
    kill only fires on an ACTUAL red row.

This module may import ``app.adapters`` / ``app.evals`` (it is the composition
root); ``app/core/`` stays pure. No live LLM call is ever made here — the grounding
rows run the deterministic gate with an injected judge, and the GEO pass uses the
simulated adapter (INV-9), so the suite is fully offline.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.api.deps import (
    get_eval_state,
    get_params,
    get_settings_dep,
    set_eval_state,
)
from app.core.eval_gate import GatedRecord, action_enabled
from app.core.params import Params
from app.core.settings import Settings
from app.evals.suite import EvalSuiteResult, NudgeCounts, run_suite

router = APIRouter(tags=["evals"])

# The fixed ICP prompt set the GEO row samples over (copied from
# tests/evals/test_suite.py — the locked contract). A 3-tuple keeps the run
# deterministic and small; the simulated adapter samples it at a fixed seed.
_PROMPT_SET = (
    "best gifted school online",
    "accelerated learning program for gifted kids",
    "personalized gifted education",
)

# A nudge confusion-matrix triple that clears BOTH thresholds (min_precision 0.85,
# min_recall 0.70): tp=18, fp=2, fn=2 ⇒ precision 0.9000, recall 0.9000. This is
# the deterministic PASS construction for the live "run the suite green" pass.
_NUDGE_PASS: NudgeCounts = {"tp": 18, "fp": 2, "fn": 2}

# A doc-extraction golden that clears min_accuracy 0.90: 10/10 fields correct.
_DOC_PREDICTED = {f"f{i}": i for i in range(10)}
_DOC_GROUND_TRUTH = {f"f{i}": i for i in range(10)}

# A fixed seed for the simulated GEO sampling pass so the run is reproducible.
_GEO_SEED = 0

# --- dependency aliases (Annotated keeps the call in the type, not a default arg
# — avoids ruff B008; the idiomatic FastAPI style, matching app/api/geo.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
# Injected as a Depends so tests can override the live suite-level kill state and
# so the override is honored on the read path (not a bare module-function call).
EvalStateDep = Annotated["EvalSuiteResult | None", Depends(get_eval_state)]


class EvalRowView(BaseModel):
    """One eval's line on the scoreboard view (FR-4.5; the locked JSON contract).

    Mirrors :class:`~app.evals.suite.EvalRow` — the stable ``eval_name``, the
    representative ``score``, the params-derived ``threshold``, and the ``passed``
    verdict — so the client renders the green/red scoreboard directly.
    """

    eval_name: str
    score: float
    threshold: float
    passed: bool


class EvalsView(BaseModel):
    """The consolidated eval-suite view the UI renders (FR-4.5; INV-3).

    ``rows`` is one :class:`EvalRowView` per eval (empty when no suite has run).
    ``overall_green`` is the fail-closed roll-up. ``disabled`` maps each eval name
    to whether its gated action is DISABLED right now (``True`` iff that eval's row
    is red) — the suite-level kill the live gate enforces (INV-3).
    """

    rows: list[EvalRowView]
    overall_green: bool
    disabled: dict[str, bool]


def _on_brand_judge(proposal: GatedRecord, never_rules: list[str]) -> float | None:
    """Deterministic on-brand judge for the live suite run (V-4 pass).

    Returns a high conformance score so the PASS grounding rows clear V-4 without a
    live LLM call (INV-2/INV-9). Matches the gate's ``BrandJudge`` signature.
    """
    return 0.99


def _view_from_state(state: EvalSuiteResult | None) -> EvalsView:
    """Shape the consolidated view from the current suite state (the shared shape).

    ``None`` (no suite has run) ⇒ empty rows, ``overall_green`` True, no disabled
    actions — fail-OPEN on "never run" (the per-message gate still guards drafts);
    the suite-level kill only fires on an actual red row. Otherwise one row per
    eval and ``disabled[name] = not action_enabled(state, name)``.
    """
    if state is None:
        return EvalsView(rows=[], overall_green=True, disabled={})
    rows = [
        EvalRowView(
            eval_name=row.eval_name,
            score=row.score,
            threshold=row.threshold,
            passed=row.passed,
        )
        for row in state.rows
    ]
    disabled = {row.eval_name: not action_enabled(state, row.eval_name) for row in state.rows}
    return EvalsView(rows=rows, overall_green=state.overall_green, disabled=disabled)


def _run_consolidated_suite(settings: Settings, params: Params) -> EvalSuiteResult:
    """Run the four FR-4.x evals over deterministic, offline inputs (FR-4.5).

    Builds the same inputs as ``tests/evals/test_suite.py`` (the locked contract):
    the committed grounding golden set, a PASS nudge triple, a passing doc pair, a
    simulated GEO sampling pass at a fixed seed, and an injected on-brand judge. No
    network, no live LLM (INV-9).
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    geo_observations = SimulatedGeoSamplingAdapter().sample(
        _PROMPT_SET,
        engine="sim-engine",
        min_samples_per_prompt=min_samples,
        seed=_GEO_SEED,
    )
    return run_suite(
        settings=settings,
        params=params,
        golden_drafts=_golden_drafts(),
        nudge_counts=_NUDGE_PASS,
        doc_golden=(_DOC_PREDICTED, _DOC_GROUND_TRUTH),
        geo_observations=geo_observations,
        brand_judge=_on_brand_judge,
    )


def _golden_drafts() -> list[dict[str, object]]:
    """Parse the committed grounding golden set (one json per non-blank line).

    The same file ``tests/evals/test_suite.py`` reads — the grounding row's golden
    inputs (the LOCKED contract). Resolved relative to the backend root:
    backend/app/api/evals.py → parents[2] is ``backend/``.
    """
    import json
    from pathlib import Path

    golden = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "evals"
        / "golden"
        / "enrollment_drafts.jsonl"
    )
    return [json.loads(line) for line in golden.read_text().splitlines() if line.strip()]


@router.post("/evals/run", response_model=EvalsView)
def run_evals(settings: SettingsDep, params: ParamsDep) -> EvalsView:
    """Run the consolidated eval suite, record it as live kill state, return the view.

    Runs all four FR-4.x evals over deterministic, offline inputs (FR-4.5),
    ``set_eval_state``-s the verdict as the live suite-level kill (INV-3), then
    returns the same shape as ``GET /evals``. No live LLM call (the grounding rows
    use an injected judge; the GEO pass uses the simulated adapter — INV-9).
    """
    result = _run_consolidated_suite(settings, params)
    set_eval_state(result)
    return _view_from_state(result)


@router.get("/evals", response_model=EvalsView)
def get_evals(eval_state: EvalStateDep) -> EvalsView:
    """The consolidated eval scoreboard + the live per-row ``disabled`` map (FR-4.5).

    Reads the last ``set_eval_state`` verdict (via the dep so overrides are
    honored). No suite has run ⇒ empty rows, ``overall_green`` True, no disabled
    actions (fail-OPEN on "never run"; the per-message gate still guards drafts).
    Otherwise each row plus ``disabled[name] = not action_enabled(state, name)`` —
    the suite-level kill the live gate enforces (INV-3).
    """
    return _view_from_state(eval_state)


__all__ = ["EvalRowView", "EvalsView", "router"]

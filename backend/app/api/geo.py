"""GEO tracking endpoints — coverage vs the 0% baseline + lift (FR-3.7/4.4; ARCH §6).

The composition layer wiring the S5 GEO core into HTTP. It is deliberately thin
(the marketing analog of ``app/api/funding.py``): every decision-bearing step —
the repeated sampling and the coverage/variance verdict — lives in an owned module
it orchestrates (INV-2). This router only assembles the prompt set, calls the
dep'd adapter, evaluates, shapes the view, and (for the action) logs to the audit
spine. No business logic, no magic numbers (``min_samples_per_prompt`` comes from
``params`` — INV-11).

  ``GET /geo``
    A DEFAULT repeated-sampling pass over the seeded GEO prompt set
    (``[p.target_prompt for p in generate_geo_content_pieces()]``) using the dep'd
    SIMULATED adapter (INV-9) with ``min_samples_per_prompt`` from params and a
    FIXED default seed — so ``curl /geo`` is stable and returns ``baseline: 0.0``
    plus a computed ``lift``. Evaluated via :func:`evaluate_geo_tracking`; a
    read-only view, nothing is logged.

  ``POST /geo/sample``
    The "trigger a fresh sampling run" action. Accepts an optional body
    (``prompt_set`` / ``engine`` / ``seed``, all optional, defaulting to the seed
    prompt set / the default engine / the default seed), runs the same pass,
    evaluates, and LOGS the run + its eval to the §10 observability log (NFR-6),
    labeled as a ``geo_tracking`` subject. Returns the same view shape.

The tracking eval (coverage/variance) is a DIFFERENT path from the V-1..V-4
message gate (``evaluate_message``); this module never touches that gate, so the
§10 backlog note about ``eval_gate.py`` mislabeling a ``GeoContentPiece`` does not
apply here. ``enabled=False`` (insufficient samples) ⇒ the GEO action is disabled,
fail-closed (INV-3).

This module may import ``app.adapters`` / ``app.data`` / ``app.evals`` /
``app.observability`` (it is the composition root); ``app/core/`` stays pure. No
live engine call is ever made here — the simulated adapter is offline (INV-9).
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.geo_sampling.base import GeoSamplingAdapter
from app.api.deps import (
    get_geo_sampling_adapter_dep,
    get_observability_log,
    get_params,
)
from app.core.params import Params
from app.data.synthetic import generate_geo_content_pieces
from app.evals.geo_tracking_eval import GeoTrackingResult, evaluate_geo_tracking
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["geo"])

# The §10 flow + schema version surfaced on each logged sampling run, and the eval
# name attached to it. Labeled "geo_tracking" so the audit records the coverage
# tracking subject explicitly — NOT the V-1..V-4 message gate (eval_gate.py).
GEO_FLOW = "geo_tracking"
GEO_SCHEMA_VERSION = "1"
GEO_EVAL_NAME = "geo_tracking"

# Composition-layer defaults for the deterministic GET pass (NOT domain tunables —
# `min_samples_per_prompt` is the params-owned tunable, read per request). The seed
# keeps `curl /geo` stable; the engine is the simulated-engine label surfaced in
# the view. They describe the simulation harness, not live behaviour (no live
# engine in v1, INV-9).
_DEFAULT_SEED = 0
_DEFAULT_ENGINE = "simulated"

# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/funding.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
GeoAdapterDep = Annotated[GeoSamplingAdapter, Depends(get_geo_sampling_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]


class GeoTrackingView(BaseModel):
    """The GEO tracking view the UI builds to (FR-3.7/4.4; the locked JSON contract).

    Carries the :class:`GeoTrackingResult` verdict (coverage vs the 0% baseline,
    lift, variance, the run count + fail-closed flags) plus the ``prompt_set`` that
    was sampled and the ``engine`` label used — so the client knows exactly which
    ICP prompts and which simulated engine produced the coverage.
    """

    coverage_mean: float
    baseline: float
    lift: float
    variance: float
    sample_count: int
    insufficient_samples: bool
    enabled: bool
    prompt_set: list[str]
    engine: str


class GeoSampleRequest(BaseModel):
    """An optional override for a fresh ``POST /geo/sample`` run.

    All fields optional: an empty body defaults to the seeded GEO prompt set, the
    default simulated engine, and the default seed — so the action is a one-click
    "sample again" with stable, reproducible results. ``min_samples_per_prompt`` is
    never accepted here; it is params-owned (INV-11).
    """

    prompt_set: list[str] | None = None
    engine: str | None = None
    seed: int | None = None


def _default_prompt_set() -> list[str]:
    """The seeded GEO ICP prompts — the target_prompts of the GEO content seeds."""
    return [piece.target_prompt for piece in generate_geo_content_pieces()]


def _run_tracking(
    prompt_set: list[str],
    engine: str,
    seed: int,
    adapter: GeoSamplingAdapter,
    params: Params,
) -> tuple[GeoTrackingResult, GeoTrackingView]:
    """Sample → evaluate → shape the view (the one orchestration both routes share).

    ``min_samples_per_prompt`` is read from ``params`` (INV-11), never hardcoded.
    Returns both the raw eval result (for logging) and the assembled view.
    """
    min_samples = params.eval_thresholds.geo_tracking.min_samples_per_prompt
    observations = adapter.sample(
        prompt_set,
        engine,
        min_samples_per_prompt=min_samples,
        seed=seed,
    )
    result = evaluate_geo_tracking(observations, params=params)
    view = GeoTrackingView(
        coverage_mean=result.coverage_mean,
        baseline=result.baseline,
        lift=result.lift,
        variance=result.variance,
        sample_count=result.sample_count,
        insufficient_samples=result.insufficient_samples,
        enabled=result.enabled,
        prompt_set=prompt_set,
        engine=engine,
    )
    return result, view


@router.get("/geo", response_model=GeoTrackingView)
def get_geo(params: ParamsDep, adapter: GeoAdapterDep) -> GeoTrackingView:
    """GEO coverage vs the 0% baseline + lift — a deterministic default pass (FR-3.7).

    Runs the default repeated-sampling pass over the seeded GEO prompt set with the
    fixed default seed, so the response is stable (``baseline: 0.0`` + a computed
    ``lift``). Read-only — nothing is logged. ``enabled=False`` ⇒ the GEO action is
    disabled (insufficient samples, INV-3).
    """
    _, view = _run_tracking(_default_prompt_set(), _DEFAULT_ENGINE, _DEFAULT_SEED, adapter, params)
    return view


@router.post("/geo/sample", response_model=GeoTrackingView)
def post_geo_sample(
    request: GeoSampleRequest,
    params: ParamsDep,
    adapter: GeoAdapterDep,
    log: LogDep,
) -> GeoTrackingView:
    """Trigger a fresh repeated-sampling run, log it, return the view (FR-4.4; NFR-6).

    Defaults each field to the seed prompt set / default engine / default seed, so
    an empty body is a reproducible "sample again". The run + its tracking eval are
    appended to the §10 audit log labeled as a ``geo_tracking`` subject (NOT the
    V-1..V-4 message gate). ``enabled`` rides through fail-closed (INV-3).
    """
    prompt_set = request.prompt_set if request.prompt_set is not None else _default_prompt_set()
    engine = request.engine if request.engine is not None else _DEFAULT_ENGINE
    seed = request.seed if request.seed is not None else _DEFAULT_SEED

    result, view = _run_tracking(prompt_set, engine, seed, adapter, params)

    # Log the sampling run + its tracking eval to the audit spine (NFR-6), labeled
    # geo_tracking so the audit records the coverage subject correctly. The payload
    # is the view (the coverage verdict + the sampled prompts/engine), not a message
    # candidate — this eval is the coverage/variance path, not evaluate_message.
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=GEO_FLOW,
        schema_version=GEO_SCHEMA_VERSION,
        payload=view.model_dump(mode="json"),
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name=GEO_EVAL_NAME,
        passed=result.enabled,
        score=result.coverage_mean,
    )

    return view

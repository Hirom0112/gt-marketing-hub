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
from pydantic import BaseModel, Field

from app.adapters.geo_sampling.base import GeoSamplingAdapter
from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.api.deps import (
    get_geo_sampling_adapter_dep,
    get_observability_log,
    get_params,
    get_settings_dep,
)
from app.core.eval_gate import evaluate_message
from app.core.params import Params
from app.core.settings import Settings
from app.data.synthetic import generate_geo_content_pieces
from app.evals.geo_tracking_eval import GeoTrackingResult, evaluate_geo_tracking
from app.marketing.geo import build_geo_piece, validate_competitor_set
from app.marketing.schemas.geo import GeoStructure
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

# The generate-to-win flywheel name for the §10 audit subject (NFR-6) — distinct
# from the geo_tracking sampling subject and from the V-1..V-4 enrollment gate.
GEO_GENERATE_FLOW = "geo_generate"
GEO_GENERATE_EVAL_NAME = "message_safety_grounding"

# The PROCESS-SHARED published-prompt registry (FR-3.7). A GEO piece published via
# POST /geo/generate records its target prompt → the params-derived GT cite-bucket
# band here; every subsequently-built SimulatedGeoSamplingAdapter consults it so
# coverage on the won prompt RISES on the next GET /geo or /geo/sample (the
# cross-request flywheel). In-memory, per-process (INV-9) — no live engine, no I/O.
# Held in a one-slot list so reset_published_registry can rebind for test isolation.
_published_registry: list[dict[str, int]] = [{}]


def reset_published_registry() -> None:
    """Clear the process-shared published-prompt registry (test isolation only).

    The flywheel state is per-process and append-only by nature; tests need a
    clean registry per case so a prior generate-to-win does not leak coverage
    into an unrelated assertion. Production never calls this.
    """
    _published_registry[0] = {}


def _bind_registry(adapter: GeoSamplingAdapter) -> GeoSamplingAdapter:
    """Bind the process-shared published registry onto a simulated adapter.

    The §7 registry builds a FRESH SimulatedGeoSamplingAdapter per request; for the
    flywheel to persist across requests, every such adapter must read the same
    published-prompt registry. A non-simulated adapter (a test stub) is returned
    untouched — the binding is a property of the simulated impl only (INV-9).
    """
    if isinstance(adapter, SimulatedGeoSamplingAdapter):
        return SimulatedGeoSamplingAdapter(published_registry=_published_registry[0])
    return adapter


# --- dependency aliases (Annotated keeps the call in the type, not a default arg —
# avoids ruff B008; the idiomatic FastAPI style, matching app/api/funding.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
GeoAdapterDep = Annotated[GeoSamplingAdapter, Depends(get_geo_sampling_adapter_dep)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


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
    # GT-vs-competitor citation share (FR-3.7; growth-strategy Bet 3): the ~3%-GT
    # vs ~50%-competitor leadership view the board renders as share bars.
    gt_citation_share: float = 0.0
    competitor_citation_share: dict[str, float] = Field(default_factory=dict)


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


class GeoGenerateRequest(BaseModel):
    """The generate-to-win action body (FR-3.7): generate content to WIN a prompt.

    ``target_prompt`` is the AI-search prompt GT wants to start being cited in.
    ``structure`` optionally picks the §7.1 structured form (definition / faq /
    comparison_table); default DEFINITION. ``body`` is an optional override used
    by tests to drive the BLOCKED path (a banned-claim body must fail-closed);
    omitted ⇒ the deterministic curated/template body is generated. ``engine`` /
    ``seed`` default to the simulation harness defaults. No live LLM (INV-9).
    """

    target_prompt: str = Field(min_length=1)
    structure: GeoStructure | None = None
    body: str | None = None
    engine: str | None = None
    seed: int | None = None


class GeoGenerateView(GeoTrackingView):
    """The generate-to-win result the UI builds to (FR-3.7), extends the track view.

    Adds the flywheel outcome on top of the re-sampled coverage view:
    ``published`` (the gate-passed piece entered the simulated corpus and moved
    coverage), ``blocked`` (the grounding gate BLOCKED it — INV-4 fail-closed,
    nothing published), and ``failed_rules`` (the V-1..V-4 rules that failed, for
    the operator + the audit log). On a block, the coverage figures reflect the
    UNCHANGED prompt (no lift), proving fail-closed.
    """

    published: bool
    blocked: bool
    failed_rules: list[str] = Field(default_factory=list)


def _default_prompt_set() -> list[str]:
    """The seeded GEO ICP prompts — the target_prompts of the GEO content seeds.

    Phase-1 marketing: prefers the IMPORT-provenance GEO pieces derived from
    GT's OWN SEO/GEO strategy (`load_geo_content_pieces`), so the tracked prompt
    set is the uncontested prompts GT actually wants to win, plus the synthetic
    §11.5 seeds as a stable base. Deduplicated, deterministic order.
    """
    from app.data.library_ingest import load_geo_content_pieces

    prompts: list[str] = []
    for piece in (*load_geo_content_pieces(), *generate_geo_content_pieces()):
        if piece.target_prompt not in prompts:
            prompts.append(piece.target_prompt)
    return prompts


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
        gt_citation_share=result.gt_citation_share,
        competitor_citation_share=result.competitor_citation_share,
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
    _, view = _run_tracking(
        _default_prompt_set(), _DEFAULT_ENGINE, _DEFAULT_SEED, _bind_registry(adapter), params
    )
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

    result, view = _run_tracking(prompt_set, engine, seed, _bind_registry(adapter), params)

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


@router.post("/geo/generate", response_model=GeoGenerateView)
def post_geo_generate(
    request: GeoGenerateRequest,
    params: ParamsDep,
    adapter: GeoAdapterDep,
    settings: SettingsDep,
    log: LogDep,
) -> GeoGenerateView:
    """Generate content to WIN a target prompt, gate it, publish, re-sample (FR-3.7).

    The generate-to-win flywheel (distinct from /geo/sample's re-sample):

    1. Build a :class:`GeoContentPiece` for ``target_prompt`` — structure-first
       curated prose (or the test-supplied ``body`` override), the LOCKED
       gifted-school competitor set, empty ``claims_text`` (gate-clean). No live
       LLM — deterministic template (INV-9).
    2. Validate the competitor set (``validate_competitor_set``, INV-6).
    3. Route the piece's text through the REAL grounding gate
       (``evaluate_message`` with an INJECTED always-pass V-4 judge, mirroring
       ``library_ingest.load_library_assets``) — a banned-claim body is BLOCKED,
       never softened (INV-4). The verdict is logged (NFR-6).
    4. On PASS: publish the prompt into the process-shared registry at the
       params-derived GT cite band, then re-sample so coverage on that prompt
       visibly RISES (lift > 0). On BLOCK: publish NOTHING and re-sample the
       UNCHANGED prompt (no lift) — fail-closed.

    The piece is a proposal (INV-2); the deterministic core (publish + sample +
    eval) owns the coverage write. The view is scoped to the target prompt.
    """
    structure = request.structure if request.structure is not None else GeoStructure.DEFINITION
    engine = request.engine if request.engine is not None else _DEFAULT_ENGINE
    seed = request.seed if request.seed is not None else _DEFAULT_SEED

    # 1. Build the piece (curated prose or the test body override). LOCKED set, INV-6.
    piece = build_geo_piece(
        target_prompt=request.target_prompt,
        structure=structure,
        body_override=request.body,
    )
    # 2. Competitor-set guard (INV-6): refuse a piece outside the gifted-school set.
    competitor_ok = validate_competitor_set(piece.competitor_set)

    # 3. Real grounding gate with an injected always-pass V-4 judge (these are GT's
    #    OWN on-brand pieces; the import-path V-4 stand-in, not a live LLM — INV-9).
    threshold = params.eval_thresholds.message_safety_grounding.min_grounding
    pass_judge = lambda _record, _never: threshold  # noqa: E731
    verdict = evaluate_message(
        piece,
        settings=settings,
        params=params,
        brand_judge=pass_judge,
        audience="general",  # GEO targets parents/general — a COPPA-safe audience (V-3).
    )

    published = bool(verdict.passed and competitor_ok)
    failed_rules = list(verdict.failed_rules)
    if not competitor_ok and "competitor_set" not in failed_rules:
        failed_rules.append("competitor_set")

    # 4. PASS ⇒ publish at the params-derived lift band (INV-11), so re-sampling
    #    raises coverage on the won prompt. BLOCK ⇒ publish nothing (fail-closed).
    if published:
        SimulatedGeoSamplingAdapter(published_registry=_published_registry[0]).publish(
            request.target_prompt,
            published_cite_buckets=params.geo.published_cite_buckets,
        )

    # Re-sample the (now possibly won) prompt to show the moved/unchanged coverage.
    result, track_view = _run_tracking(
        [request.target_prompt], engine, seed, _bind_registry(adapter), params
    )

    view = GeoGenerateView(
        **track_view.model_dump(),
        published=published,
        blocked=not published,
        failed_rules=failed_rules,
    )

    # Log the generated piece + its grounding verdict to the audit spine (NFR-6),
    # labeled geo_generate so the audit records the flywheel subject explicitly.
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow=GEO_GENERATE_FLOW,
        schema_version=GEO_SCHEMA_VERSION,
        payload={
            "target_prompt": request.target_prompt,
            "geo_structure": piece.geo_structure.value,
            "published": published,
            "failed_rules": failed_rules,
        },
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name=GEO_GENERATE_EVAL_NAME,
        passed=published,
        score=result.coverage_mean,
    )

    return view

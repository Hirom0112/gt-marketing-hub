"""Leadership scoreboard aggregation tests (FR-6.1; NFR-6; ARCH §10).

The scoreboard is a PURE, deterministic rollup over the append-only audit log
(`app/observability/log_store.py`): proposals + their evals + their decisions.
It surfaces a cross-funnel summary the P2 leadership view renders — enrollment
funnel counts, marketing/GEO coverage lift vs the 0% baseline
(`params.geo.baseline_coverage`), and per-eval pass/fail with an overall
green/red. It fabricates nothing the log does not carry (be honest), and the
same log always yields the same scoreboard (determinism).

Drift-aware: the GEO baseline is read from `params`, never hardcoded, so a
param change is caught here.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.core.scoreboard import (
    EvalSummary,
    MarketingSummary,
    Scoreboard,
    build_scoreboard,
)

from app.core.params import Params, load_params
from app.observability.log_store import (
    DecisionAction,
    InMemoryObservabilityLog,
)

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# Stable UUIDs so the build is fully deterministic (no uuid4 in the test path).
PID_DRAFT_APPROVE = UUID("00000000-0000-0000-0000-0000000000a1")
PID_DRAFT_APPROVE_2 = UUID("00000000-0000-0000-0000-0000000000a2")
PID_DRAFT_REJECT = UUID("00000000-0000-0000-0000-0000000000a3")
PID_DRAFT_EDIT = UUID("00000000-0000-0000-0000-0000000000a4")
PID_DRAFT_UNDECIDED = UUID("00000000-0000-0000-0000-0000000000a5")
PID_GEO = UUID("00000000-0000-0000-0000-0000000000b1")

# Enrollment-draft flow + GEO flow/eval tokens — the exact values the live
# routes log (app/api/ai_actions.py, app/api/geo.py). The aggregation keys off
# these, so the test pins them.
DRAFT_FLOW = "enrollment_draft"
GEO_FLOW = "geo_tracking"
GEO_EVAL_NAME = "geo_tracking"
GROUNDING_EVAL_NAME = "message_safety_grounding"

# A known GEO coverage score; lift == coverage - baseline(0.0) == coverage.
GEO_COVERAGE = 0.6


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _seed_log() -> InMemoryObservabilityLog:
    """A deterministic cross-funnel audit mix.

    Enrollment: 4 enrollment-draft proposals + 1 GEO proposal.
      - PID_DRAFT_APPROVE   : grounding eval pass  → human APPROVE
      - PID_DRAFT_APPROVE_2 : grounding eval pass  → human APPROVE
      - PID_DRAFT_REJECT    : grounding eval FAIL  → human DISCARD
      - PID_DRAFT_EDIT      : grounding eval pass  → human EDIT
      - PID_DRAFT_UNDECIDED : grounding eval pass  → (no decision yet)
    GEO: one geo_tracking proposal with a geo_tracking eval pass carrying a
    coverage score of GEO_COVERAGE.
    """
    log = InMemoryObservabilityLog()

    # --- enrollment-draft proposals + grounding evals + human decisions -----
    log.log_proposal(proposal_id=PID_DRAFT_APPROVE, flow=DRAFT_FLOW, schema_version="1", payload={})
    log.log_eval(proposal_id=PID_DRAFT_APPROVE, eval_name=GROUNDING_EVAL_NAME, passed=True)
    log.log_decision(proposal_id=PID_DRAFT_APPROVE, human="director", action=DecisionAction.APPROVE)

    log.log_proposal(
        proposal_id=PID_DRAFT_APPROVE_2, flow=DRAFT_FLOW, schema_version="1", payload={}
    )
    log.log_eval(proposal_id=PID_DRAFT_APPROVE_2, eval_name=GROUNDING_EVAL_NAME, passed=True)
    log.log_decision(
        proposal_id=PID_DRAFT_APPROVE_2, human="director", action=DecisionAction.APPROVE
    )

    log.log_proposal(proposal_id=PID_DRAFT_REJECT, flow=DRAFT_FLOW, schema_version="1", payload={})
    # A blocked proposal stays logged with its FAILING eval (INV-4 audit side).
    log.log_eval(proposal_id=PID_DRAFT_REJECT, eval_name=GROUNDING_EVAL_NAME, passed=False)
    log.log_decision(proposal_id=PID_DRAFT_REJECT, human="director", action=DecisionAction.DISCARD)

    log.log_proposal(proposal_id=PID_DRAFT_EDIT, flow=DRAFT_FLOW, schema_version="1", payload={})
    log.log_eval(proposal_id=PID_DRAFT_EDIT, eval_name=GROUNDING_EVAL_NAME, passed=True)
    log.log_decision(
        proposal_id=PID_DRAFT_EDIT,
        human="director",
        action=DecisionAction.EDIT,
        edited_payload={"body": "tweaked"},
    )

    log.log_proposal(
        proposal_id=PID_DRAFT_UNDECIDED, flow=DRAFT_FLOW, schema_version="1", payload={}
    )
    log.log_eval(proposal_id=PID_DRAFT_UNDECIDED, eval_name=GROUNDING_EVAL_NAME, passed=True)
    # No decision logged for this one — funnel must NOT count it as approved/rejected.

    # --- GEO sampling proposal + geo_tracking eval carrying a coverage score -
    log.log_proposal(proposal_id=PID_GEO, flow=GEO_FLOW, schema_version="1", payload={})
    log.log_eval(
        proposal_id=PID_GEO,
        eval_name=GEO_EVAL_NAME,
        passed=True,
        score=GEO_COVERAGE,
    )

    return log


def test_scoreboard_cross_funnel() -> None:
    """Aggregation surfaces real logged funnel counts, GEO lift, and eval status."""
    log = _seed_log()
    params = _params()
    baseline = params.geo.baseline_coverage  # 0.0 — drift-aware, not hardcoded.

    board = build_scoreboard(log, params=params)

    assert isinstance(board, Scoreboard)

    # --- Enrollment funnel: counts derived from logged proposals/decisions ----
    enr = board.enrollment
    assert enr.draft_proposals == 4  # 4 enrollment_draft proposals (GEO excluded)
    assert enr.approved == 2  # two APPROVE decisions
    assert enr.rejected == 1  # one DISCARD decision
    assert enr.edited == 1  # one EDIT decision
    assert enr.undecided == 1  # one draft with no decision yet

    # --- Marketing / GEO lift vs the 0% baseline from params ------------------
    assert isinstance(board.marketing, MarketingSummary)
    assert board.marketing.geo_baseline == baseline
    assert board.marketing.geo_coverage == GEO_COVERAGE
    # lift == measured coverage - baseline (off-0%).
    assert board.marketing.geo_lift == GEO_COVERAGE - baseline

    # --- Eval status: per-eval pass/fail + overall green/red ------------------
    assert isinstance(board.evals, EvalSummary)
    # geo_tracking passed; message_safety_grounding had at least one FAIL.
    assert board.evals.passed[GEO_EVAL_NAME] is True
    assert board.evals.passed[GROUNDING_EVAL_NAME] is False
    # Overall is RED iff ANY eval failed — one grounding eval failed.
    assert board.evals.overall_green is False

    # --- Determinism: same log ⇒ same scoreboard ------------------------------
    assert build_scoreboard(log, params=params) == board


def test_scoreboard_all_green_no_geo() -> None:
    """No GEO eval ⇒ 0.0 lift off the 0% baseline (not an error); all-pass ⇒ green."""
    log = InMemoryObservabilityLog()
    params = _params()

    log.log_proposal(proposal_id=PID_DRAFT_APPROVE, flow=DRAFT_FLOW, schema_version="1", payload={})
    log.log_eval(proposal_id=PID_DRAFT_APPROVE, eval_name=GROUNDING_EVAL_NAME, passed=True)
    log.log_decision(proposal_id=PID_DRAFT_APPROVE, human="director", action=DecisionAction.APPROVE)

    board = build_scoreboard(log, params=params)

    assert board.enrollment.draft_proposals == 1
    assert board.enrollment.approved == 1
    assert board.enrollment.rejected == 0
    # No geo_tracking eval logged ⇒ coverage absent, lift 0.0 off the 0% baseline.
    assert board.marketing.geo_coverage is None
    assert board.marketing.geo_lift == 0.0
    assert board.marketing.geo_baseline == params.geo.baseline_coverage
    # Only a passing eval logged ⇒ overall green.
    assert board.evals.passed[GROUNDING_EVAL_NAME] is True
    assert board.evals.overall_green is True

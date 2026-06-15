"""Leadership scoreboard — pure deterministic rollup over the audit log (FR-6.1).

The P2 leadership view renders one cross-funnel summary: *how is enrollment
flowing, is marketing/GEO lifting off the 0% baseline, and are the evals green?*
This module computes that summary as a pure aggregation over the append-only
NFR-6 audit spine (`app/observability/log_store.py`) — proposals + their evals +
their decisions. It is the function the future `GET /scoreboard` route calls; the
route is wired by a separate API agent and this module never touches `api/`.

Honesty (the LOCKED design): the scoreboard surfaces ONLY what the log actually
carries. It does not fabricate funnel stages the log never recorded — the
enrollment funnel is exactly "drafts proposed → human approved / edited /
discarded / undecided", because that is what the audit chain holds (an
``enrollment_draft`` proposal, its grounding eval, and the human's
:class:`~app.observability.log_store.DecisionAction`).

GEO lift is measured against ``params.geo.baseline_coverage`` (the 0% baseline,
INV-11) — never a hardcoded zero — so the "off-0%" signal stays drift-aware. The
coverage itself comes from logged ``geo_tracking`` evals, whose ``score`` carries
the repeated-sampling coverage mean (see ``app/api/geo.py``). If no GEO eval was
ever logged, lift is ``0.0`` off the baseline — that is a clean zero, not an error.

Purity (CLAUDE.md §3): this is deterministic core. It imports only the
:class:`~app.observability.log_store.ObservabilityLog` interface + record types,
:class:`~app.core.params.Params`, pydantic, and stdlib — no
``anthropic``/``langgraph``/``app.ai``/``app.adapters``, no network, no
``datetime.now``/``uuid4``. It reads the log through its public query API
(``list_proposals`` + ``get_audit``); reading the in-memory store is not I/O. Same
log ⇒ same scoreboard.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.core.params import Params
from app.observability.log_store import (
    DecisionAction,
    EvalRecord,
    ObservabilityLog,
)

# The `proposals.flow` token the enrollment-draft action logs (app/api/ai_actions.py
# DRAFT_FLOW). The funnel counts proposals carrying this flow — GEO/content/seam
# proposals carry different flow tokens and are excluded from the enrollment funnel.
ENROLLMENT_DRAFT_FLOW: Final = "enrollment_draft"

# The `evals.eval_name` the GEO sampling run logs (app/api/geo.py GEO_EVAL_NAME);
# its `score` is the repeated-sampling coverage mean. The marketing rollup reads
# GEO coverage from this eval name only.
GEO_EVAL_NAME: Final = "geo_tracking"


class EnrollmentSummary(BaseModel):
    """Enrollment-funnel counts derived from logged drafts + human decisions.

    Only stages the audit chain actually records (the LOCKED design's honesty
    rule): every ``enrollment_draft`` proposal is one ``draft_proposals``, and the
    human's verdict on it routes into exactly one of approved/edited/rejected, or
    ``undecided`` if no decision was logged yet. ``approved + edited + rejected +
    undecided == draft_proposals`` always holds.
    """

    model_config = ConfigDict(frozen=True)

    draft_proposals: int = Field(ge=0)
    approved: int = Field(ge=0)
    edited: int = Field(ge=0)
    rejected: int = Field(ge=0)
    undecided: int = Field(ge=0)


class MarketingSummary(BaseModel):
    """Marketing/GEO coverage lift vs the 0% baseline (FR-6.1; INV-11).

    ``geo_coverage`` is the latest logged ``geo_tracking`` eval score (the
    repeated-sampling coverage mean), or ``None`` when no GEO eval was logged.
    ``geo_baseline`` is ``params.geo.baseline_coverage`` (the 0% baseline).
    ``geo_lift`` is ``coverage - baseline`` — the off-0% signal — and is ``0.0``
    (not an error) when no coverage was logged.
    """

    model_config = ConfigDict(frozen=True)

    geo_coverage: float | None = None
    geo_baseline: float
    geo_lift: float


class EvalSummary(BaseModel):
    """Per-eval pass/fail rollup + overall green/red (INV-3 audit side).

    ``passed`` maps each logged ``eval_name`` to whether EVERY logged instance of
    it passed (an eval is "passing" only if no logged run of it failed — a single
    failed run flips it red, matching the fail-closed audit). ``overall_green`` is
    ``True`` iff no logged eval failed at all.
    """

    model_config = ConfigDict(frozen=True)

    passed: dict[str, bool] = Field(default_factory=dict)
    overall_green: bool


class Scoreboard(BaseModel):
    """The cross-funnel leadership summary the P2 view renders (FR-6.1).

    A frozen aggregate of the three sub-summaries; deterministic in the audit log
    (same log ⇒ equal ``Scoreboard``). This is the value the ``GET /scoreboard``
    route returns; building it performs no I/O.
    """

    model_config = ConfigDict(frozen=True)

    enrollment: EnrollmentSummary
    marketing: MarketingSummary
    evals: EvalSummary


def _all_evals(log: ObservabilityLog) -> list[EvalRecord]:
    """Every logged eval, in proposal-append then eval-append order (deterministic).

    Reads through the public query API only: ``list_proposals`` gives the append
    order, ``get_audit`` gives each proposal's eval chain. No private state access.
    """
    evals: list[EvalRecord] = []
    for proposal in log.list_proposals():
        audit = log.get_audit(proposal.proposal_id)
        if audit is not None:
            evals.extend(audit.evals)
    return evals


def _enrollment_summary(log: ObservabilityLog) -> EnrollmentSummary:
    """Roll the enrollment-draft funnel from logged proposals + their decisions.

    Counts only ``enrollment_draft`` proposals (the funnel subject). For each, the
    LATEST logged decision routes it: APPROVE → approved, EDIT → edited, DISCARD →
    rejected; a draft with no decision is ``undecided``. (The audit chain is
    append-only, so "latest" is the last appended decision — an edit-then-approve
    history resolves to its final human verdict.)
    """
    draft_proposals = 0
    approved = 0
    edited = 0
    rejected = 0
    undecided = 0

    for proposal in log.list_proposals():
        if proposal.flow != ENROLLMENT_DRAFT_FLOW:
            continue
        draft_proposals += 1
        audit = log.get_audit(proposal.proposal_id)
        decisions = audit.decisions if audit is not None else []
        if not decisions:
            undecided += 1
            continue
        action = decisions[-1].action
        if action is DecisionAction.APPROVE:
            approved += 1
        elif action is DecisionAction.EDIT:
            edited += 1
        else:  # DecisionAction.DISCARD
            rejected += 1

    return EnrollmentSummary(
        draft_proposals=draft_proposals,
        approved=approved,
        edited=edited,
        rejected=rejected,
        undecided=undecided,
    )


def _marketing_summary(log: ObservabilityLog, *, params: Params) -> MarketingSummary:
    """Roll GEO coverage lift vs the 0% baseline from logged geo_tracking evals.

    Coverage is the latest logged ``geo_tracking`` eval ``score`` (the
    repeated-sampling coverage mean). Lift is ``coverage - baseline`` where the
    baseline is ``params.geo.baseline_coverage`` (drift-aware, INV-11). No GEO eval
    or a scoreless one ⇒ coverage ``None`` and lift ``0.0`` (off the 0% baseline).
    """
    baseline = params.geo.baseline_coverage
    coverage: float | None = None
    # Iterate in append order; keep the latest geo_tracking score so a re-sampled
    # run supersedes an earlier one.
    for record in _all_evals(log):
        if record.eval_name == GEO_EVAL_NAME and record.score is not None:
            coverage = record.score

    lift = coverage - baseline if coverage is not None else 0.0
    return MarketingSummary(geo_coverage=coverage, geo_baseline=baseline, geo_lift=lift)


def _eval_summary(log: ObservabilityLog) -> EvalSummary:
    """Roll per-eval pass/fail + overall green/red from logged eval records.

    An eval name is ``True`` only if EVERY logged run of it passed; a single failed
    run flips it (and the overall) red — the fail-closed audit (INV-3). With no
    evals logged, ``overall_green`` is ``True`` (nothing failed).
    """
    passed: dict[str, bool] = {}
    for record in _all_evals(log):
        # AND-accumulate: once an eval name has a failed run, it stays False.
        prior = passed.get(record.eval_name, True)
        passed[record.eval_name] = prior and record.passed

    overall_green = all(passed.values())
    return EvalSummary(passed=passed, overall_green=overall_green)


def build_scoreboard(log: ObservabilityLog, *, params: Params) -> Scoreboard:
    """Aggregate the audit log into the cross-funnel leadership scoreboard (FR-6.1).

    Pure and deterministic: reads ``log`` through its public query API and ``params``
    for the GEO baseline, and returns a frozen :class:`Scoreboard`. Same log + params
    ⇒ equal scoreboard. No I/O, no wall-clock, no randomness.

    Args:
        log: The append-only NFR-6 audit spine to roll up (proposals/evals/decisions).
        params: Loaded params — supplies ``geo.baseline_coverage`` (the 0% baseline
            GEO lift is measured against, INV-11).

    Returns:
        The :class:`Scoreboard` the ``GET /scoreboard`` route renders for leadership.
    """
    return Scoreboard(
        enrollment=_enrollment_summary(log),
        marketing=_marketing_summary(log, params=params),
        evals=_eval_summary(log),
    )

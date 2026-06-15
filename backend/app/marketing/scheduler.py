"""Scheduler dispatch gate — the simulated-only send gate (S6 §6, LOCKED).

§6 RULE (LOCKED): "a `ScheduledPost` cannot enter `queued`/`simulated_sent`
unless `validation` is a passing `ValidationResult` AND
`approval.decision = approve`. A `blocked` validation forces
`dispatchStatus = blocked`." `dispatchMode` is ALWAYS `simulated` in v1 (OUT-2).

This module is the DETERMINISTIC core of §6 (INV-2): given a `ScheduledPost`
and its `ValidationResult`, it decides whether the post may queue for a
SIMULATED send, and produces the simulated receipt. It is FAIL-CLOSED
(INV-3/INV-4): no send happens without a passing validation AND a human
`approve`; a failing/un-approved post is forced to `blocked`, never to
`simulated_sent`. Per INV-9 / OUT-2 every v1 dispatch is SIMULATED — a `live`
post is REJECTED (:class:`LiveDispatchRejected`); v1 never dispatches live.

Pure per CLAUDE.md §3 / ARCHITECTURE.md §3: imports only the scheduling schema
(`app.marketing.schemas.scheduling`), the review verdict enum
(`app.ai.schemas.content.Decision`) and the `ValidationResult` shape
(`app.core.eval_gate`) — no `anthropic` / `langgraph` / I/O / network /
`datetime.now` / `uuid4`. The simulated receipt is derived DETERMINISTICALLY
from the post id (no wall-clock, no random), so the same post yields the same
receipt every run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.schemas.content import Decision
from app.marketing.schemas.scheduling import DispatchMode, DispatchStatus

if TYPE_CHECKING:
    from app.core.eval_gate import ValidationResult
    from app.marketing.schemas.scheduling import ScheduledPost

# The deterministic simulated-receipt template (INV-9): a synthetic stand-in for
# a real provider send-id, derived from the post id so it is stable across runs
# (no wall-clock / uuid4 — purity, determinism). The `simulated://` scheme makes
# it unmistakably a simulation, never a live provider receipt.
_SIMULATED_RECEIPT_PREFIX = "simulated://dispatch/"


class LiveDispatchRejected(Exception):
    """Raised when a `live`-mode post is passed to the gate (INV-9 / OUT-2).

    v1 NEVER dispatches live: the only valid `dispatch_mode` is `simulated`. A
    `live` post reaching the gate is a contract violation, so the gate RAISES
    rather than risk a real external send (fail-closed).
    """


def _require_simulated(post: ScheduledPost) -> None:
    """Reject a non-simulated post — v1 dispatch is simulated-only (INV-9/OUT-2)."""
    if post.dispatch_mode is not DispatchMode.SIMULATED:
        raise LiveDispatchRejected(
            f"scheduled post {post.id} has dispatch_mode={post.dispatch_mode.value}; "
            f"v1 dispatch is SIMULATED-only (INV-9, OUT-2) — live sends are not permitted"
        )


def gate_dispatch(post: ScheduledPost, *, validation: ValidationResult) -> DispatchStatus:
    """The §6 dispatch verdict: `QUEUED` if cleared to simulate-send, else `BLOCKED`.

    Returns :attr:`DispatchStatus.QUEUED` (ready to simulate-send) only when BOTH
    hold (fail-closed, INV-3/INV-4):

    * `validation.passed is True` (a passing `ValidationResult`), and
    * `post.approval.decision == approve` (a human approval).

    Any other state ⇒ :attr:`DispatchStatus.BLOCKED` (a failing validation or a
    non-approve decision forces `blocked`). A `live`-mode post RAISES
    :class:`LiveDispatchRejected` first (INV-9 / OUT-2) — v1 never dispatches
    live. Pure and deterministic.
    """
    _require_simulated(post)
    if validation.passed is not True:
        return DispatchStatus.BLOCKED
    if post.approval.decision is not Decision.APPROVE:
        return DispatchStatus.BLOCKED
    return DispatchStatus.QUEUED


def _simulated_receipt(post: ScheduledPost) -> str:
    """A deterministic synthetic send-receipt for `post` (no wall-clock/uuid4).

    Derived purely from the post id so the same post yields the same receipt on
    every run — auditable and stable, never a live provider id (INV-9).
    """
    return f"{_SIMULATED_RECEIPT_PREFIX}{post.id}"


def simulate_send(post: ScheduledPost, *, validation: ValidationResult) -> ScheduledPost:
    """Simulate the dispatch of `post`, gated by §6 (returns a NEW `ScheduledPost`).

    Runs :func:`gate_dispatch`. When it returns `QUEUED`, returns a copy of the
    post with `dispatch_status = simulated_sent` and a deterministic synthetic
    `simulated_result` receipt. When it returns `BLOCKED`, returns a copy with
    `dispatch_status = blocked` and `simulated_result = None` — NEVER
    `simulated_sent` (fail-closed: no send without passing validation AND
    approval, INV-3/INV-4). A `live`-mode post RAISES
    :class:`LiveDispatchRejected` (INV-9 / OUT-2). Pure and deterministic — the
    input post is frozen, so a new record is returned via `model_copy`.
    """
    verdict = gate_dispatch(post, validation=validation)
    if verdict is DispatchStatus.QUEUED:
        return post.model_copy(
            update={
                "dispatch_status": DispatchStatus.SIMULATED_SENT,
                "simulated_result": _simulated_receipt(post),
            }
        )
    return post.model_copy(
        update={
            "dispatch_status": DispatchStatus.BLOCKED,
            "simulated_result": None,
        }
    )

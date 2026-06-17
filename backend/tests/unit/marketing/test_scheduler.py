"""Scheduler dispatch-gate tests — S6 §6 (LOCKED, simulated-only, fail-closed).

§6 RULE (LOCKED): a `ScheduledPost` cannot enter `queued`/`simulated_sent`
unless `validation` is a passing `ValidationResult` AND
`approval.decision == approve`. A `blocked` validation forces
`dispatchStatus = blocked`. `dispatchMode` is ALWAYS `simulated` in v1 (OUT-2).

The gate is DETERMINISTIC and FAIL-CLOSED (INV-3/INV-4/INV-9): no send happens
without passing validation AND approval, a `live` post is REJECTED (v1 never
dispatches live), and `simulate_send` produces a deterministic synthetic
receipt (no wall-clock / uuid4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.ai.schemas.content import Channel, Decision, GeneratedBy, HumanDecision, Provenance
from app.core.eval_gate import RuleVerdict, ValidationResult
from app.marketing.scheduler import LiveDispatchRejected, gate_dispatch, simulate_send
from app.marketing.schemas.scheduling import (
    DispatchMode,
    DispatchStatus,
    ScheduledPost,
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.HUMAN, created_at="2026-06-14T00:00:00Z")


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


def _post(
    *,
    approval: Decision = Decision.APPROVE,
    dispatch_mode: DispatchMode = DispatchMode.SIMULATED,
    dispatch_status: DispatchStatus = DispatchStatus.QUEUED,
) -> ScheduledPost:
    return ScheduledPost(  # type: ignore[call-arg]
        id=uuid4(),
        channel=Channel.INSTAGRAM,
        scheduledFor="2026-06-20T15:00:00Z",
        dispatchMode=dispatch_mode,
        dispatchStatus=dispatch_status,
        validation="val-1",
        approval=HumanDecision(decision=approval),
        provenance=_provenance(),
    )


def test_scheduled_post_blocked_without_passing_validation_and_approval() -> None:
    """A post reaches `queued`/`simulated_sent` only with passing validation AND approve (§6).

    Covers: (a) passing + approve ⇒ QUEUED, simulate_send ⇒ simulated_sent +
    receipt; (b) failing validation ⇒ BLOCKED, never sent; (c) approval≠approve
    ⇒ BLOCKED; (d) a `live` post RAISES (v1 simulated-only, INV-9/OUT-2).
    """
    passing = _passing()
    failing = _failing()

    # (a) passing validation + approval=approve ⇒ gate QUEUED; send ⇒ simulated_sent.
    ready = _post(approval=Decision.APPROVE)
    assert gate_dispatch(ready, validation=passing) is DispatchStatus.QUEUED
    sent = simulate_send(ready, validation=passing)
    assert sent.dispatch_status is DispatchStatus.SIMULATED_SENT
    assert sent.simulated_result  # a non-empty synthetic receipt
    assert sent.dispatch_mode is DispatchMode.SIMULATED

    # (b) failing validation ⇒ BLOCKED; send ⇒ blocked, NEVER simulated_sent.
    failval = _post(approval=Decision.APPROVE)
    assert gate_dispatch(failval, validation=failing) is DispatchStatus.BLOCKED
    blocked = simulate_send(failval, validation=failing)
    assert blocked.dispatch_status is DispatchStatus.BLOCKED
    assert blocked.dispatch_status is not DispatchStatus.SIMULATED_SENT
    assert blocked.simulated_result is None

    # (c) approval != approve (reject) ⇒ BLOCKED even with passing validation.
    rejected = _post(approval=Decision.REJECT)
    assert gate_dispatch(rejected, validation=passing) is DispatchStatus.BLOCKED
    blocked2 = simulate_send(rejected, validation=passing)
    assert blocked2.dispatch_status is DispatchStatus.BLOCKED
    assert blocked2.simulated_result is None

    # A pending (un-decided) approval is also blocked.
    pending = _post(approval=Decision.PENDING)
    assert gate_dispatch(pending, validation=passing) is DispatchStatus.BLOCKED

    # (d) a `dispatch_mode=live` post RAISES — v1 never dispatches live (OUT-2/INV-9).
    live_post = _post(approval=Decision.APPROVE, dispatch_mode=DispatchMode.LIVE)
    with pytest.raises(LiveDispatchRejected):
        gate_dispatch(live_post, validation=passing)
    with pytest.raises(LiveDispatchRejected):
        simulate_send(live_post, validation=passing)


def test_simulate_send_receipt_is_deterministic() -> None:
    """The synthetic receipt is deterministic — no wall-clock/uuid4 (pure)."""
    passing = _passing()
    post = _post(approval=Decision.APPROVE)
    first = simulate_send(post, validation=passing)
    second = simulate_send(post, validation=passing)
    assert first.simulated_result == second.simulated_result
    # The receipt references the post id so the audit log can correlate it.
    assert str(post.id) in (first.simulated_result or "")

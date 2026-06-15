"""ScheduledPost schema tests — S6 §6 (FR-3.6, dispatch SIMULATED, OUT-2).

§6 ScheduledPost is the scheduling content-as-data record: dispatch is ALWAYS
simulated in v1 (`dispatchMode`), and `dispatchStatus` is a CLOSED set of
queued/simulated_sent/failed/blocked. The LOCKED queueing RULE (cannot enter
queued/simulated_sent unless validation passes AND approval=approve) is enforced
by the SCHEDULER agent's logic, NOT this schema — here we lock the shape + the
closed enums only.

Per CLAUDE.md §4.1 a pure red→green schema test: closed enums RAISE out of range,
required fields enforced, `extra="forbid"` rejects unknown fields (fail closed).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.schemas.content import Channel, Decision, GeneratedBy, HumanDecision, Provenance
from app.marketing.schemas.scheduling import (
    DispatchMode,
    DispatchStatus,
    ScheduledPost,
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.HUMAN, created_at="2026-06-14T00:00:00Z")


def _post(**overrides: object) -> ScheduledPost:
    base: dict[str, object] = {
        "id": uuid4(),
        "channel": Channel.INSTAGRAM,
        "scheduledFor": "2026-06-20T15:00:00Z",
        "dispatchMode": DispatchMode.SIMULATED,
        "dispatchStatus": DispatchStatus.QUEUED,
        "validation": "val-1",
        "approval": HumanDecision(decision=Decision.APPROVE),
        "provenance": _provenance(),
    }
    base.update(overrides)
    return ScheduledPost(**base)  # type: ignore[arg-type]


def test_scheduled_post_schema_valid() -> None:
    """A simulated ScheduledPost validates; required refs/approval present."""
    post = _post()
    assert post.dispatch_mode is DispatchMode.SIMULATED
    assert post.dispatch_status is DispatchStatus.QUEUED
    assert post.validation == "val-1"
    assert post.approval.decision is Decision.APPROVE
    assert post.scheduled_for == "2026-06-20T15:00:00Z"

    # asset/candidate refs are optional.
    assert post.asset_ref is None
    assert post.candidate_ref is None
    assert post.simulated_result is None


def test_dispatch_enums_are_closed() -> None:
    """`dispatchMode`/`dispatchStatus` are CLOSED — out-of-range RAISES (OUT-2)."""
    assert _post(dispatchMode="simulated").dispatch_mode is DispatchMode.SIMULATED
    assert _post(dispatchMode="live").dispatch_mode is DispatchMode.LIVE
    with pytest.raises(ValidationError):
        _post(dispatchMode="broadcast")

    for member in DispatchStatus:
        assert _post(dispatchStatus=member).dispatch_status is member
    with pytest.raises(ValidationError):
        _post(dispatchStatus="sent")


def test_scheduled_post_required_and_closed() -> None:
    """Missing required field RAISES; unknown extra rejected (fail closed)."""
    with pytest.raises(ValidationError):
        ScheduledPost(  # type: ignore[call-arg]
            id=uuid4(),
            channel=Channel.INSTAGRAM,
            scheduledFor="2026-06-20T15:00:00Z",
            dispatchMode=DispatchMode.SIMULATED,
            dispatchStatus=DispatchStatus.QUEUED,
            # validation + approval missing
            provenance=_provenance(),
        )
    with pytest.raises(ValidationError):
        _post(unexpected="nope")

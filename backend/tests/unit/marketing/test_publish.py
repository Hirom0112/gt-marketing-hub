"""Publish fan-out tests — one gated dispatch per channel + per-platform caps.

The publish-monitor slice fans a single :class:`PublishRequest` out to one
:class:`ScheduledPost` per channel, gating EACH with the existing §6
``simulate_send`` (INV-2/INV-4) and enforcing a params-driven per-platform daily
CAP (INV-8 posture, INV-11). These assert: (a) a clean request dispatches every
channel with deterministic receipts; (b) an over-cap channel is forced ``blocked``
while the rest send; (c) a failing validation blocks ALL channels (fail-closed);
(d) post ids are deterministic; (e) caps are read from params, not hardcoded.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.ai.schemas.content import (
    Channel,
    Decision,
    GeneratedBy,
    HumanDecision,
    Provenance,
)
from app.core.eval_gate import RuleVerdict, ValidationResult
from app.core.params import load_params
from app.marketing.publish import _post_id, plan_publish
from app.marketing.schemas.publish import MirrorStatus, PublishRequest
from app.marketing.schemas.scheduling import DispatchStatus

_EXAMPLE_PARAMS = Path(__file__).resolve().parents[4] / "params" / "params.example.yaml"


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


def _request(*channels: Channel) -> PublishRequest:
    return PublishRequest(  # type: ignore[call-arg]
        id=uuid4(),
        channels=tuple(channels),
        body="GT School is mastery-based, with TEFA funding for families.",
        scheduledFor="2026-06-20T15:00:00Z",
        campaignTheme="tefa_affordability",
    )


def _caps() -> dict[str, int]:
    return {"instagram": 5, "tiktok": 3, "x": 10, "linkedin": 3}


def _plan(request: PublishRequest, *, validation: ValidationResult, **kw):  # type: ignore[no-untyped-def]
    return plan_publish(
        request,
        validation=validation,
        validation_ref="val-1",
        approval=HumanDecision(decision=Decision.APPROVE),
        provenance=_provenance(),
        daily_caps=_caps(),
        **kw,
    )


def test_fan_out_dispatches_every_channel_with_receipts() -> None:
    """A clean request ⇒ one simulated_sent post per channel, each with a receipt."""
    request = _request(Channel.INSTAGRAM, Channel.X, Channel.LINKEDIN)
    posts, monitor = _plan(request, validation=_passing())

    assert len(posts) == 3
    assert {p.channel for p in posts} == {Channel.INSTAGRAM, Channel.X, Channel.LINKEDIN}
    assert all(p.dispatch_status is DispatchStatus.SIMULATED_SENT for p in posts)
    assert all(p.simulated_result for p in posts)

    assert monitor.request_id == request.id
    assert len(monitor.dispatches) == 3
    assert all(d.dispatch_status is DispatchStatus.SIMULATED_SENT for d in monitor.dispatches)
    assert all(d.mirror_status is MirrorStatus.PENDING for d in monitor.dispatches)
    assert all(not d.capped for d in monitor.dispatches)


def test_over_cap_channel_is_blocked_others_send() -> None:
    """A channel already at its daily cap is forced blocked; the rest still send."""
    request = _request(Channel.TIKTOK, Channel.X)
    # tiktok cap is 3; pretend 3 already went today ⇒ tiktok must block, x sends.
    posts, monitor = _plan(request, validation=_passing(), prior_counts={"tiktok": 3})

    by_channel = {d.channel: d for d in monitor.dispatches}
    assert by_channel[Channel.TIKTOK].dispatch_status is DispatchStatus.BLOCKED
    assert by_channel[Channel.TIKTOK].capped is True
    assert by_channel[Channel.TIKTOK].simulated_result is None
    assert by_channel[Channel.TIKTOK].mirror_status is MirrorStatus.SKIPPED

    assert by_channel[Channel.X].dispatch_status is DispatchStatus.SIMULATED_SENT
    assert by_channel[Channel.X].capped is False
    assert by_channel[Channel.X].mirror_status is MirrorStatus.PENDING


def test_failing_validation_blocks_all_channels() -> None:
    """A failing validation blocks every channel — fail-closed (INV-3/INV-4)."""
    request = _request(Channel.INSTAGRAM, Channel.X, Channel.LINKEDIN)
    posts, monitor = _plan(request, validation=_failing())

    assert all(p.dispatch_status is DispatchStatus.BLOCKED for p in posts)
    assert all(p.simulated_result is None for p in posts)
    assert all(d.capped is False for d in monitor.dispatches)  # blocked by gate, not cap
    assert all(d.mirror_status is MirrorStatus.SKIPPED for d in monitor.dispatches)


def test_post_ids_are_deterministic() -> None:
    """Per-channel post ids derive from (request id, channel) — stable across runs."""
    request = _request(Channel.INSTAGRAM, Channel.X)
    posts_a, _ = _plan(request, validation=_passing())
    posts_b, _ = _plan(request, validation=_passing())

    assert [p.id for p in posts_a] == [p.id for p in posts_b]
    assert posts_a[0].id == _post_id(request.id, Channel.INSTAGRAM)
    assert posts_a[0].id != posts_a[1].id  # different channel ⇒ different id


def test_caps_are_read_from_params_not_hardcoded() -> None:
    """The per-platform caps live in params.scheduler.daily_caps (INV-11)."""
    params = load_params(_EXAMPLE_PARAMS)
    caps = params.scheduler.daily_caps
    for channel in params.scheduler.publish_channels:
        assert caps[channel] >= 1  # every publish channel has a positive cap
    assert set(params.scheduler.publish_channels) <= {c.value for c in Channel}

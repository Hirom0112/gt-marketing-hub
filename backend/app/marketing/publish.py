"""Deterministic publish fan-out + monitor aggregation (publish-monitor slice, FR-3.6).

The cockpit is the OBSERVABILITY PLANE: a single :class:`PublishRequest` is fanned
out to one :class:`~app.marketing.schemas.scheduling.ScheduledPost` per target
channel, each gated INDEPENDENTLY by the existing §6 scheduler gate
(:func:`app.marketing.scheduler.simulate_send` — INV-2/INV-4), with a per-platform
daily CAP (params-driven, INV-11; INV-8 governance posture) that forces an
over-cap channel to ``blocked`` rather than letting it dispatch. The per-platform
outcomes are aggregated into a :class:`PublishMonitor` that drives BOTH screens —
the cockpit dashboard and (via the GT Social Post mirror, a separate adapter step)
the HubSpot monitor.

Pure core (CLAUDE.md §3): imports only the schemas, the §6 gate, and stdlib — no
``anthropic`` / ``langgraph`` / I/O / network / ``datetime.now`` / ``uuid4``.
Per-channel post ids are derived DETERMINISTICALLY (``uuid5`` over the request id
+ channel), so a request yields the same posts and receipts on every run —
auditable and stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from app.marketing.scheduler import simulate_send
from app.marketing.schemas.publish import (
    MirrorStatus,
    PlatformDispatch,
    PublishMonitor,
    PublishRequest,
)
from app.marketing.schemas.scheduling import DispatchMode, DispatchStatus, ScheduledPost

if TYPE_CHECKING:
    from app.ai.schemas.content import Channel, HumanDecision, Provenance
    from app.core.eval_gate import ValidationResult

# Fixed namespace for deriving a per-channel ScheduledPost id from the request id
# + channel token (uuid5 — deterministic, never uuid4). A constant seam (one home),
# not a tunable: the same (request, channel) always maps to the same post id so the
# audit log and the HubSpot mirror correlate across runs.
_POST_NAMESPACE = UUID("3f2504e0-4f89-41d3-9a0c-0305e82c3301")


def _post_id(request_id: UUID, channel: Channel) -> UUID:
    """Derive the deterministic ScheduledPost id for ``channel`` of ``request_id``."""
    return uuid5(_POST_NAMESPACE, f"{request_id}:{channel.value}")


def plan_publish(
    request: PublishRequest,
    *,
    validation: ValidationResult,
    validation_ref: str,
    approval: HumanDecision,
    provenance: Provenance,
    daily_caps: dict[str, int],
    prior_counts: dict[str, int] | None = None,
) -> tuple[list[ScheduledPost], PublishMonitor]:
    """Fan ``request`` out to one gated ScheduledPost per channel + a monitor view.

    For each channel in ``request.channels`` (in order): build a SIMULATED-mode
    ScheduledPost with a deterministic id, then settle its dispatch status:

    * **Over the per-platform daily cap** (``prior_counts[channel] >=
      daily_caps[channel]``) ⇒ forced :attr:`DispatchStatus.BLOCKED`, no receipt,
      ``capped=True`` (INV-8 governance posture — never silently overspend a
      platform quota). The cap counter is NOT incremented for a capped channel.
    * **Otherwise** ⇒ the §6 gate decides (:func:`simulate_send`): passing
      validation AND ``approval==approve`` ⇒ ``simulated_sent`` + a deterministic
      receipt and the channel's running count increments; any other state ⇒
      ``blocked`` (fail-closed, INV-3/INV-4).

    A dispatched post is marked ``MirrorStatus.PENDING`` (eligible for the HubSpot
    GT Social Post mirror); a blocked/capped post is ``MirrorStatus.SKIPPED``
    (nothing to mirror). The flip to ``MIRRORED`` happens in the adapter step.

    Args:
        request: the publish intent (content + target channels + schedule time).
        validation: the §9.6 ValidationResult the gate consumes for every channel.
        validation_ref: the id ref recorded on each ScheduledPost (audit link).
        approval: the human review decision applied to every channel's post.
        provenance: the provenance stamped on every channel's post.
        daily_caps: per-channel-token max simulated dispatches/day (from params).
        prior_counts: per-channel-token dispatches already made today (defaults 0).

    Returns:
        ``(posts, monitor)`` — the settled ScheduledPosts (one per channel, in
        request order) and the aggregate PublishMonitor for the dashboard.
    """
    counts = dict(prior_counts or {})
    posts: list[ScheduledPost] = []
    dispatches: list[PlatformDispatch] = []

    for channel in request.channels:
        post = ScheduledPost(
            id=_post_id(request.id, channel),
            assetRef=request.asset_ref,
            candidateRef=request.candidate_ref,
            channel=channel,
            scheduledFor=request.scheduled_for,
            dispatchMode=DispatchMode.SIMULATED,
            dispatchStatus=DispatchStatus.QUEUED,
            validation=validation_ref,
            approval=approval,
            provenance=provenance,
        )

        cap = daily_caps.get(channel.value)
        used = counts.get(channel.value, 0)
        capped = cap is not None and used >= cap

        if capped:
            settled = post.model_copy(
                update={
                    "dispatch_status": DispatchStatus.BLOCKED,
                    "simulated_result": None,
                }
            )
        else:
            settled = simulate_send(post, validation=validation)
            if settled.dispatch_status is DispatchStatus.SIMULATED_SENT:
                counts[channel.value] = used + 1

        sent = settled.dispatch_status is DispatchStatus.SIMULATED_SENT
        dispatches.append(
            PlatformDispatch(
                post_id=settled.id,
                channel=channel,
                dispatch_status=settled.dispatch_status,
                simulated_result=settled.simulated_result,
                capped=capped,
                mirror_status=MirrorStatus.PENDING if sent else MirrorStatus.SKIPPED,
            )
        )
        posts.append(settled)

    monitor = PublishMonitor(request_id=request.id, dispatches=tuple(dispatches))
    return posts, monitor

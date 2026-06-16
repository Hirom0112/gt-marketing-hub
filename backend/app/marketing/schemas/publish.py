"""Publish fan-out + dual-screen monitor records (publish-monitor slice, FR-3.6).

A :class:`PublishRequest` is the cockpit's intent to dispatch ONE content piece
across N social channels at once. The deterministic fan-out
(:mod:`app.marketing.publish`) expands it into one
:class:`~app.marketing.schemas.scheduling.ScheduledPost` per channel — each gated
INDEPENDENTLY by the existing §6 scheduler gate (INV-2/INV-4) — and aggregates the
per-platform outcomes into a :class:`PublishMonitor`.

The cockpit is the OBSERVABILITY PLANE (source of truth, full per-platform
tracking); HubSpot is the deployment + SECOND monitoring screen via the GT Social
Post mirror (a separate adapter step). Per INV-9 / OUT-2 every dispatch is
SIMULATED in v1; the live deploy rides the existing ``CRM_MODE`` seam.

Pure data per CLAUDE.md §3: imports only the LOCKED content enums, the scheduling
dispatch enum, pydantic and stdlib — no ``anthropic`` / ``langgraph`` / I/O.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.content import Channel
from app.marketing.schemas.scheduling import DispatchStatus


class MirrorStatus(StrEnum):
    """GT Social Post HubSpot-mirror state — the SECOND-screen monitor signal.

    The pure fan-out sets ``pending`` for a dispatched post (eligible to mirror)
    and ``skipped`` for a blocked/capped one (nothing to mirror). The HubSpot
    adapter step flips ``pending`` → ``mirrored`` once the GT Social Post custom
    object is written/updated (simulated by default, live under ``CRM_MODE=live``).
    """

    PENDING = "pending"
    MIRRORED = "mirrored"
    SKIPPED = "skipped"


class PublishRequest(BaseModel):
    """One intent to publish a content piece across N channels (§6, FR-3.6).

    Frozen + ``extra='forbid'``: an immutable request that fails closed on an
    unknown field (V-1, §9.2). ``channels`` is the target set (a subset of the
    LOCKED :class:`Channel` enum); the fan-out produces one ScheduledPost per
    channel. ``asset_ref`` / ``candidate_ref`` link the source content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    channels: tuple[Channel, ...] = Field(min_length=1)
    body: str = Field(min_length=1)
    asset_ref: UUID | None = Field(default=None, alias="assetRef")
    candidate_ref: UUID | None = Field(default=None, alias="candidateRef")
    scheduled_for: str = Field(min_length=1, alias="scheduledFor")
    campaign_theme: str | None = Field(default=None, alias="campaignTheme")


class PlatformDispatch(BaseModel):
    """Per-platform status row for the monitor dashboard (frozen).

    One row per target channel: the derived ScheduledPost id, the §6 dispatch
    verdict, the simulated receipt (``None`` when blocked), whether the
    per-platform daily CAP forced the block, and the HubSpot mirror state.
    """

    model_config = ConfigDict(frozen=True)

    post_id: UUID
    channel: Channel
    dispatch_status: DispatchStatus
    simulated_result: str | None = None
    capped: bool = False
    mirror_status: MirrorStatus = MirrorStatus.PENDING


class PublishMonitor(BaseModel):
    """Aggregate dual-screen view of one PublishRequest fan-out (frozen).

    ``dispatches`` is the per-platform tracking that drives the cockpit dashboard;
    ``hubspot_object_id`` is the id of the mirrored GT Social Post custom object
    once written (``None`` until the adapter step runs / when nothing to mirror).
    """

    model_config = ConfigDict(frozen=True)

    request_id: UUID
    dispatches: tuple[PlatformDispatch, ...]
    hubspot_object_id: str | None = None

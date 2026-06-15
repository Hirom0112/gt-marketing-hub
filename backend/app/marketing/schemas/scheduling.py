"""`ScheduledPost` — the scheduling content-as-data record (S6 §6, FR-3.6).

§6 models a scheduled outbound post as a typed, validated proposal (INV-2). Per
INV-9 / OUT-2 every v1 dispatch is SIMULATED: `dispatch_mode` is always
`simulated` in v1 (the `live` member exists only for the post-v1 adapter swap),
and `dispatch_status` is a CLOSED set of `queued`/`simulated_sent`/`failed`/
`blocked`.

The LOCKED queueing RULE — a post cannot enter `queued`/`simulated_sent` unless
its `validation` passes AND `approval.decision == approve` — is enforced by the
SCHEDULER agent's logic (a separate package), NOT this schema. This module locks
only the SHAPE + the closed enums; an out-of-range value RAISES
`pydantic.ValidationError` (V-1, §9.2 — fail closed).

Pure data per CLAUDE.md §3: imports only `app.ai.schemas.content` (reusing the
LOCKED `Channel` / `HumanDecision` / `Provenance`), pydantic and stdlib — no
`anthropic` / `langgraph` / I/O. `asset_ref` / `candidate_ref` / `validation`
are plain id refs (no import cycle). CONTENT_SPEC uses camelCase wire names;
attributes are snake_case with pydantic aliases + `populate_by_name=True`.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.content import Channel, HumanDecision, Provenance


class DispatchMode(StrEnum):
    """`dispatchMode` enum (§6, LOCKED) — always `simulated` in v1 (OUT-2/INV-9)."""

    SIMULATED = "simulated"
    LIVE = "live"


class DispatchStatus(StrEnum):
    """`dispatchStatus` enum (§6, LOCKED) — the simulated dispatch lifecycle."""

    QUEUED = "queued"
    SIMULATED_SENT = "simulated_sent"
    FAILED = "failed"
    BLOCKED = "blocked"


class ScheduledPost(BaseModel):
    """`ScheduledPost` (§6, LOCKED, FR-3.6) — one scheduled outbound post.

    Frozen + `extra="forbid"`: immutable once parsed and rejects unknown fields,
    so a malformed payload fails closed (V-1, §9.2). `validation` references the
    §9.6 `ValidationResult` by id; the gate runs in `app/core/eval_gate.py`
    (A-10). `approval` is a required `HumanDecision` audit record — but enforcing
    that approval==approve before queuing is the SCHEDULER agent's job, not this
    schema's (this only defines the shape + closed enums).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    asset_ref: UUID | None = Field(default=None, alias="assetRef")
    candidate_ref: UUID | None = Field(default=None, alias="candidateRef")
    channel: Channel
    scheduled_for: str = Field(min_length=1, alias="scheduledFor")
    dispatch_mode: DispatchMode = Field(alias="dispatchMode")
    dispatch_status: DispatchStatus = Field(alias="dispatchStatus")
    simulated_result: str | None = Field(default=None, alias="simulatedResult")
    validation: str = Field(min_length=1)
    approval: HumanDecision
    provenance: Provenance

"""The social-posting boundary — interface + post/receipt models (ARCH §7.4).

§7.4 (authoritative):

    interface SocialAdapter:
      schedule(post, when) -> QueueRef    # backend-held queue, no native scheduling
      publish(post) -> PostResult         # simulated

"v1: simulated — the backend holds the queue (no native platform scheduling), and
``publish`` records a simulated receipt; no live send (OUT-2)." All v1 sends are
simulated (``SOCIAL_POST_MODE=simulate``, ``SEND_MODE=simulate``).

INV-9: like every external boundary, this is an interface with two impls —
Simulated (v1) and Production (go-live) — selected by config in
:mod:`app.adapters.registry`. Live posting is OUT in v1 (PROJECT §7, OUT-2); the
simulated impl is a pure, offline source with no network client at all, so "no
live send" is a structural property. This module imports nothing from
``anthropic`` and keeps ``core/`` untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class SocialPost(BaseModel):
    """A post to schedule or publish (§7.4).

    A small typed input: a target ``channel`` and the post ``body``. Frozen — a
    post is an immutable request, not mutable state.

    Attributes:
        channel: The target social channel identifier (e.g. ``instagram``).
        body: The post text/caption to schedule or publish.
    """

    model_config = ConfigDict(frozen=True)

    channel: str
    body: str


class QueueRef(BaseModel):
    """A backend-held queue reference for a scheduled post (§7.4).

    The backend holds the queue (no native platform scheduling); this is the
    receipt of an enqueue. ``simulated`` is always True in v1 — no live platform
    was contacted. Frozen — a queue ref is an immutable receipt.

    Attributes:
        queue_id: Synthetic, deterministic id of the backend queue entry.
        scheduled_for: The requested send time (echoed back; opaque string).
        simulated: Always True in v1 — no native platform scheduling occurred.
    """

    model_config = ConfigDict(frozen=True)

    queue_id: str
    scheduled_for: str
    simulated: bool = True


class PostResult(BaseModel):
    """A simulated publish receipt (§7.4).

    The result of a (simulated) ``publish``: ``simulated`` is always True in v1
    and ``post_id`` is a synthetic receipt id — **no live send** occurred (OUT-2).
    Frozen — a receipt is an immutable record.

    Attributes:
        post_id: Synthetic, deterministic id of the simulated published post.
        simulated: Always True in v1 — no live send occurred.
    """

    model_config = ConfigDict(frozen=True)

    post_id: str
    simulated: bool = True


class SocialAdapter(ABC):
    """The social-posting external boundary (§7.4).

    Two impls — Simulated (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. The marketing/scheduler layer depends only on
    this interface, never on a concrete platform client.
    """

    @abstractmethod
    def schedule(self, post: SocialPost, when: str) -> QueueRef:
        """Enqueue ``post`` for ``when`` in the backend-held queue (§7.4)."""

    @abstractmethod
    def publish(self, post: SocialPost) -> PostResult:
        """(Simulated in v1) publish ``post``, returning a receipt (§7.4)."""

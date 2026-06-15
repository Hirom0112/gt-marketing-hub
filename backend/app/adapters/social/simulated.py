"""Simulated SocialAdapter — synthetic, offline, no live send (INV-9, OUT-2).

The v1 impl of the §7.4 boundary. ``schedule`` records a backend-held queue entry
and ``publish`` records a simulated receipt — both derive their synthetic ids
**deterministically** from the post (and time), and **no live send** ever occurs
(OUT-2). There is **no network client** here by construction, so "no live send"
(INV-9) holds structurally, provable from the source text alone (no live
transport to mock).

Determinism without shared entropy: each synthetic id is a salted
``hashlib.blake2b`` digest of the post fields (the same technique as
:mod:`app.adapters.funding.simulated`) — no PRNG global state, no v4 GUIDs, no
wall-clock. The same post (and time) always yields the same queue/receipt id
across calls and fresh instances.
"""

from __future__ import annotations

import hashlib

from app.adapters.social.base import PostResult, QueueRef, SocialAdapter, SocialPost


def _synthetic_id(kind: str, *parts: str) -> str:
    """Deterministic synthetic id from ``(kind, *parts)``.

    A salted BLAKE2b digest gives a stable id with no shared entropy state —
    pure, no I/O, reproducible across processes (no PRNG/GUID/clock).
    """
    key = ":".join((kind, *parts)).encode()
    return hashlib.blake2b(key, digest_size=8).hexdigest()


class SimulatedSocialAdapter(SocialAdapter):
    """Offline synthetic source for social posting (INV-9, OUT-2: no live send).

    No network client exists on this class — "no live send" is therefore a
    structural property, not a configured behaviour. ``schedule``/``publish``
    return synthetic, deterministic receipts; nothing is ever sent to a platform.
    """

    def schedule(self, post: SocialPost, when: str) -> QueueRef:
        """Enqueue ``post`` for ``when`` in the backend-held queue (§7.4).

        Returns a synthetic, deterministic :class:`QueueRef` keyed on the post and
        time — no native platform scheduling, no live send (OUT-2).
        """
        queue_id = _synthetic_id("queue", post.channel, post.body, when)
        return QueueRef(queue_id=queue_id, scheduled_for=when, simulated=True)

    def publish(self, post: SocialPost) -> PostResult:
        """(Simulated) publish ``post`` (§7.4).

        Returns a synthetic, deterministic :class:`PostResult` receipt keyed on
        the post — ``simulated=True``, no live send (OUT-2).
        """
        post_id = _synthetic_id("post", post.channel, post.body)
        return PostResult(post_id=post_id, simulated=True)

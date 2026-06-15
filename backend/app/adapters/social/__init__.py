"""Social-posting adapter package — the §7.4 boundary (OUT-2, INV-9).

A ``SocialAdapter`` interface with a ``SimulatedSocialAdapter`` that holds the
queue in the backend (no native platform scheduling) and records deterministic
**simulated** receipts, with no network client — **no live send** (OUT-2). v1
ships only the simulated impl; ``live`` is reserved and fails loud in
:mod:`app.adapters.registry`.
"""

from app.adapters.social.base import (
    PostResult,
    QueueRef,
    SocialAdapter,
    SocialPost,
)
from app.adapters.social.simulated import SimulatedSocialAdapter

__all__ = [
    "PostResult",
    "QueueRef",
    "SimulatedSocialAdapter",
    "SocialAdapter",
    "SocialPost",
]

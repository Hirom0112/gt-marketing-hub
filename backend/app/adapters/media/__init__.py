"""Media-gen adapter package — the §7.3 boundary (OUT-1, INV-9).

A ``MediaGenAdapter`` interface with a ``PlaceholderMediaGenAdapter`` that returns
deterministic **stub** image/video refs over synthetic data, with no network
client and no gen SDK — **no live gen, $0 spend** (OUT-1). v1 ships only the
placeholder impl; ``live`` is reserved and fails loud in
:mod:`app.adapters.registry`.
"""

from app.adapters.media.base import (
    ImageRef,
    MediaGenAdapter,
    MediaSpec,
    MediaTier,
    VideoRef,
)
from app.adapters.media.placeholder import PlaceholderMediaGenAdapter

__all__ = [
    "ImageRef",
    "MediaGenAdapter",
    "MediaSpec",
    "MediaTier",
    "PlaceholderMediaGenAdapter",
    "VideoRef",
]

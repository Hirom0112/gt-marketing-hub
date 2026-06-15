"""Placeholder MediaGenAdapter — synthetic, offline, $0 spend (INV-9, OUT-1).

The v1 impl of the §7.3 boundary. It returns **stub** image/video refs whose
``placeholder_uri`` is derived **deterministically** from the spec — no live
generation, **$0 spend** (OUT-1). There is **no network client and no gen SDK**
here by construction, so "no live gen" (INV-9) holds structurally, provable from
the source text alone (no live transport to mock).

Determinism without shared entropy: the synthetic asset id is a salted
``hashlib.blake2b`` digest of ``(kind, brief, tier)`` (the same technique as
:mod:`app.adapters.funding.simulated`) — no PRNG global state, no v4 GUIDs, no
wall-clock. The cost figure is **not** here: each ref carries a
``cost_estimate_ref`` STRING pointing at the TECH_STACK §6 cost model (INV-11),
so the result shape itself proves $0 spend.
"""

from __future__ import annotations

import hashlib

from app.adapters.media.base import ImageRef, MediaGenAdapter, MediaSpec, VideoRef

# The synthetic placeholder asset namespace + the pointer (a STRING, not a price)
# into the TECH_STACK §6 cost model. Both are fixtures of the v1 simulation, not
# tunables governing live behaviour (no live gen exists in v1), so they live with
# the simulation they define — and crucially `_COST_ESTIMATE_REF` is a doc pointer,
# never a dollar figure (INV-11, OUT-1: $0 spend).
_PLACEHOLDER_SCHEME = "placeholder://gt-media"
_COST_ESTIMATE_REF = "TECH_STACK.md#6-cost-model:media-gen"


def _asset_id(kind: str, spec: MediaSpec) -> str:
    """Deterministic synthetic asset id from ``(kind, brief, tier)``.

    A salted BLAKE2b digest gives a stable id with no shared entropy state —
    pure, no I/O, reproducible across processes (no PRNG/GUID/clock).
    """
    key = f"{kind}:{spec.brief}:{spec.tier}".encode()
    return hashlib.blake2b(key, digest_size=8).hexdigest()


class PlaceholderMediaGenAdapter(MediaGenAdapter):
    """Offline synthetic source for media generation (INV-9, OUT-1: $0 spend).

    No network client and no gen SDK exist on this class — "no live gen" is
    therefore a structural property, not a configured behaviour. Both methods
    return placeholder refs derived deterministically from the spec; no asset is
    ever generated and no cost is ever incurred.
    """

    def generate_image(self, spec: MediaSpec) -> ImageRef:
        """Return a placeholder :class:`ImageRef` for ``spec`` (cheapest-first, §7.3).

        Stub only — no live gen, $0 spend. The ``placeholder_uri`` is derived
        deterministically from the spec; the cost is a STRING pointer into the
        §6 cost model, never a numeric price (OUT-1, INV-11).
        """
        asset = _asset_id("image", spec)
        return ImageRef(
            placeholder_uri=f"{_PLACEHOLDER_SCHEME}/image/{asset}.png",
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=True,
        )

    def generate_video(self, spec: MediaSpec) -> VideoRef:
        """Return a placeholder :class:`VideoRef` for ``spec`` (winner-gated, §7.3).

        Stub only — no live gen, $0 spend; deterministic ``placeholder_uri`` and a
        cost-model STRING pointer, never a price (OUT-1, INV-11).
        """
        asset = _asset_id("video", spec)
        return VideoRef(
            placeholder_uri=f"{_PLACEHOLDER_SCHEME}/video/{asset}.mp4",
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=True,
        )

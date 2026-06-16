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

# Synthetic render-hint catalogues. A dashboard needs *something* to lay out, so a
# placeholder ref carries a deterministic ``"WxH format"`` hint picked from these
# fixtures by the asset digest — NOT a tunable governing live behaviour (no live
# gen exists in the placeholder), and emphatically not a price (OUT-1, INV-11);
# they're fixtures of the v1 simulation, so they live with the simulation.
_IMAGE_DIMENSIONS = ("1024x1024", "1024x768", "1080x1350", "1200x628")
_VIDEO_DIMENSIONS = ("1280x720", "1080x1920", "1920x1080")
_IMAGE_FORMAT = "png"
_VIDEO_FORMAT = "mp4"


def _digest(kind: str, spec: MediaSpec) -> bytes:
    """Salted BLAKE2b digest of ``(kind, brief, tier)`` — the shared entropy source.

    Pure, no I/O, reproducible across processes (no PRNG/GUID/clock); both the
    asset id and the synthetic render hint derive from this same digest so a spec
    maps to one stable placeholder.
    """
    key = f"{kind}:{spec.brief}:{spec.tier}".encode()
    return hashlib.blake2b(key, digest_size=8).digest()


def _asset_id(kind: str, spec: MediaSpec) -> str:
    """Deterministic synthetic asset id from ``(kind, brief, tier)`` (hex digest)."""
    return _digest(kind, spec).hex()


def _render_hint(kind: str, spec: MediaSpec, dimensions: tuple[str, ...], fmt: str) -> str:
    """A deterministic synthetic ``"WxH format"`` hint for a dashboard to render.

    The dimension is chosen from ``dimensions`` by the asset digest (stable per
    spec, no entropy state); ``fmt`` is the kind's file format. A STRING only — no
    numeric price ever enters the ref shape (OUT-1, INV-11).
    """
    index = _digest(kind, spec)[0] % len(dimensions)
    return f"{dimensions[index]} {fmt}"


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
            placeholder_uri=f"{_PLACEHOLDER_SCHEME}/image/{asset}.{_IMAGE_FORMAT}",
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=True,
            brief=spec.brief,
            render_hint=_render_hint("image", spec, _IMAGE_DIMENSIONS, _IMAGE_FORMAT),
        )

    def generate_video(self, spec: MediaSpec) -> VideoRef:
        """Return a placeholder :class:`VideoRef` for ``spec`` (winner-gated, §7.3).

        Stub only — no live gen, $0 spend; deterministic ``placeholder_uri`` and a
        cost-model STRING pointer, never a price (OUT-1, INV-11).
        """
        asset = _asset_id("video", spec)
        return VideoRef(
            placeholder_uri=f"{_PLACEHOLDER_SCHEME}/video/{asset}.{_VIDEO_FORMAT}",
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=True,
            brief=spec.brief,
            render_hint=_render_hint("video", spec, _VIDEO_DIMENSIONS, _VIDEO_FORMAT),
        )

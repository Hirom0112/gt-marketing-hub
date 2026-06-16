"""The media-gen boundary — interface + spec/ref models (ARCHITECTURE.md §7.3).

§7.3 (authoritative):

    interface MediaGenAdapter:
      generate_image(spec) -> ImageRef   # cheapest-first draft tier
      generate_video(spec) -> VideoRef   # gated to winners

"v1: placeholder — returns stub refs, no live gen, $0 spend (OUT-1)." Cost
figures live in the TECH_STACK cost model (§6), **not** here: a ref carries a
``cost_estimate_ref`` STRING pointing at that model, never a hardcoded price
(INV-11) — so $0-spend is provable from the result shape itself.

INV-9: like every external boundary, this is an interface with two impls —
Placeholder (v1) and Production (go-live) — selected by config in
:mod:`app.adapters.registry`. Live image/video generation is OUT in v1 (PROJECT
§7, OUT-1); the placeholder impl is a pure, offline source with no network client
and no gen SDK at all, so "no live gen, $0 spend" is a structural property. This
module imports nothing from ``anthropic`` and keeps ``core/`` untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

MediaTier = Literal["draft", "winner"]


class MediaSpec(BaseModel):
    """A request for one generated media asset (§7.3).

    A small typed input: a free-text ``brief`` plus an optional ``tier``
    (``draft`` is the cheapest-first default for images; ``winner`` is the gated
    tier for video). Frozen — a spec is an immutable request, not mutable state.

    Attributes:
        brief: The creative brief describing the asset to generate.
        tier: The cost tier — ``draft`` (cheapest-first) or ``winner`` (gated).
    """

    model_config = ConfigDict(frozen=True)

    brief: str
    tier: MediaTier = "draft"


class ImageRef(BaseModel):
    """A reference to a generated image (§7.3).

    In v1 this is a **placeholder** stand-in: ``is_placeholder`` is True and
    ``placeholder_uri`` points at a synthetic asset, never a live-generated file.
    The live impl (:class:`app.adapters.media.live_adapter.LiveMediaGenAdapter`)
    reuses this same shape with ``is_placeholder=False`` and the real generated
    asset URL carried in the optional ``asset_url`` field.
    ``cost_estimate_ref`` is a STRING pointing at the TECH_STACK §6 cost model —
    there is **no numeric price** here, so $0 spend is structural (OUT-1, INV-11).
    Frozen — a generated ref is an immutable record, not a mutable row.

    Attributes:
        placeholder_uri: Stand-in URI for the image — a synthetic placeholder URI
            in v1; on the live path it mirrors ``asset_url`` so existing readers
            keyed on this field keep working.
        asset_url: The real generated asset URL on the live path; ``None`` for a
            placeholder ref (so ``asset_url is None`` ⇔ no live gen occurred).
        cost_estimate_ref: A pointer (string) into the TECH_STACK §6 cost model;
            never a hardcoded dollar figure.
        is_placeholder: True for a placeholder ref (v1 default); False on the
            live path.
    """

    model_config = ConfigDict(frozen=True)

    placeholder_uri: str
    asset_url: str | None = None
    cost_estimate_ref: str
    is_placeholder: bool = True
    # Dashboard-render hints so a placeholder shows something meaningful (still $0,
    # deterministic, no network). Both are STRINGS — no numeric price ever enters
    # the ref shape (OUT-1, INV-11). ``brief`` echoes the request; ``render_hint``
    # is a deterministic synthetic ``"WxH format"`` descriptor (e.g. "1024x1024 png").
    brief: str | None = None
    render_hint: str | None = None


class VideoRef(BaseModel):
    """A reference to a generated video (§7.3).

    The video analogue of :class:`ImageRef`: a placeholder stand-in gated to the
    ``winner`` tier in production, carrying a ``cost_estimate_ref`` string and no
    numeric price ($0 spend in v1). The live impl reuses this shape with
    ``is_placeholder=False`` and the real ``asset_url``. Frozen.

    Attributes:
        placeholder_uri: Stand-in URI for the video — a synthetic placeholder URI
            in v1; on the live path it mirrors ``asset_url``.
        asset_url: The real generated asset URL on the live path; ``None`` for a
            placeholder ref.
        cost_estimate_ref: A pointer (string) into the TECH_STACK §6 cost model.
        is_placeholder: True for a placeholder ref (v1 default); False on the
            live path.
    """

    model_config = ConfigDict(frozen=True)

    placeholder_uri: str
    asset_url: str | None = None
    cost_estimate_ref: str
    is_placeholder: bool = True
    # Dashboard-render hints (strings — never a numeric price; OUT-1, INV-11).
    # ``brief`` echoes the request; ``render_hint`` is a deterministic synthetic
    # ``"WxH format"`` descriptor (e.g. "1280x720 mp4").
    brief: str | None = None
    render_hint: str | None = None


class MediaGenAdapter(ABC):
    """The media-gen external boundary (§7.3).

    Two impls — Placeholder (v1) and Production (go-live) — selected by config in
    :mod:`app.adapters.registry`. The marketing/content layer depends only on this
    interface, never on a concrete gen client.
    """

    @abstractmethod
    def generate_image(self, spec: MediaSpec) -> ImageRef:
        """Return a (placeholder, in v1) image ref for ``spec`` — cheapest-first (§7.3)."""

    @abstractmethod
    def generate_video(self, spec: MediaSpec) -> VideoRef:
        """Return a (placeholder, in v1) video ref for ``spec`` — winner-gated (§7.3)."""

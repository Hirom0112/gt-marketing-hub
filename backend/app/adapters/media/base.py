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
    ``cost_estimate_ref`` is a STRING pointing at the TECH_STACK §6 cost model —
    there is **no numeric price** here, so $0 spend is structural (OUT-1, INV-11).
    Frozen — a generated ref is an immutable record, not a mutable row.

    Attributes:
        placeholder_uri: Synthetic stand-in URI for the (un-generated) image.
        cost_estimate_ref: A pointer (string) into the TECH_STACK §6 cost model;
            never a hardcoded dollar figure.
        is_placeholder: Always True in v1 — no live gen occurred.
    """

    model_config = ConfigDict(frozen=True)

    placeholder_uri: str
    cost_estimate_ref: str
    is_placeholder: bool = True


class VideoRef(BaseModel):
    """A reference to a generated video (§7.3).

    The video analogue of :class:`ImageRef`: a placeholder stand-in gated to the
    ``winner`` tier in production, carrying a ``cost_estimate_ref`` string and no
    numeric price ($0 spend in v1). Frozen.

    Attributes:
        placeholder_uri: Synthetic stand-in URI for the (un-generated) video.
        cost_estimate_ref: A pointer (string) into the TECH_STACK §6 cost model.
        is_placeholder: Always True in v1 — no live gen occurred.
    """

    model_config = ConfigDict(frozen=True)

    placeholder_uri: str
    cost_estimate_ref: str
    is_placeholder: bool = True


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

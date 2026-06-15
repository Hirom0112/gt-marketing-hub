"""Placeholder MediaGenAdapter — FR-3.x media, OUT-1, INV-9 (ARCHITECTURE.md §7.3).

Media generation is **out** in v1: no live image/video gen, **$0 spend** (OUT-1).
§7.3:

    interface MediaGenAdapter:
      generate_image(spec) -> ImageRef   # cheapest-first draft tier
      generate_video(spec) -> VideoRef   # gated to winners

"v1: placeholder — returns stub refs, no live gen, $0 spend." Award/cost figures
live in the TECH_STACK cost model (§6), **not** the adapter — the placeholder
carries a ``cost_estimate_ref`` STRING, never a hardcoded price (INV-11).

These are the §4.1-adapter-scope RED tests:

- ``generate_image``/``generate_video`` return frozen placeholder refs with a
  non-empty ``placeholder_uri``, ``is_placeholder=True``, and a
  ``cost_estimate_ref`` string — and carry **no numeric price** field ($0 spend).
- Deterministic for a given spec (no ``random``/``uuid4``/wall-clock).
- The placeholder impl is a pure, offline source — it imports no http client and
  no ``anthropic``; "no live gen" is structural (INV-9).
- The registry returns the placeholder impl under the v1 default
  (``MEDIA_GEN_MODE=placeholder``); ``live`` fails **loud**
  (``NotImplementedError``) — never a silent live gen / overspend.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import BaseModel, ValidationError

from app.adapters.media.base import (
    ImageRef,
    MediaGenAdapter,
    MediaSpec,
    VideoRef,
)
from app.adapters.media.placeholder import PlaceholderMediaGenAdapter
from app.adapters.registry import get_media_gen_adapter


def _no_numeric_price(model: BaseModel) -> None:
    """Assert a media ref carries no numeric price field — $0 spend (OUT-1)."""
    for value in model.model_dump().values():
        assert not isinstance(value, (int, float)) or isinstance(value, bool), (
            f"media ref must carry no numeric price; got {value!r}"
        )


def test_generate_image_returns_placeholder_ref() -> None:
    """``generate_image`` ⇒ frozen placeholder ``ImageRef``; deterministic, no price."""
    adapter = PlaceholderMediaGenAdapter()
    assert isinstance(adapter, MediaGenAdapter)

    spec = MediaSpec(brief="a sunlit classroom of gifted learners", tier="draft")
    ref = adapter.generate_image(spec)

    assert isinstance(ref, ImageRef)
    assert ref.is_placeholder is True
    assert ref.placeholder_uri  # non-empty synthetic stand-in
    assert isinstance(ref.cost_estimate_ref, str) and ref.cost_estimate_ref
    _no_numeric_price(ref)

    # Deterministic for a given spec (no random/uuid4/wall-clock).
    assert adapter.generate_image(spec) == ref
    assert PlaceholderMediaGenAdapter().generate_image(spec) == ref

    # Frozen — a generated ref is an immutable record, not a mutable row.
    with pytest.raises(ValidationError):
        ref.placeholder_uri = "x"  # type: ignore[misc]

    # Derivation, not a constant: a different brief yields a different ref.
    other = adapter.generate_image(MediaSpec(brief="something else entirely"))
    assert other != ref


def test_generate_video_returns_placeholder_ref() -> None:
    """``generate_video`` ⇒ frozen placeholder ``VideoRef``; deterministic, no price."""
    adapter = PlaceholderMediaGenAdapter()

    spec = MediaSpec(brief="a 15s reel celebrating a winning post", tier="winner")
    ref = adapter.generate_video(spec)

    assert isinstance(ref, VideoRef)
    assert ref.is_placeholder is True
    assert ref.placeholder_uri
    assert isinstance(ref.cost_estimate_ref, str) and ref.cost_estimate_ref
    _no_numeric_price(ref)

    assert adapter.generate_video(spec) == ref
    assert PlaceholderMediaGenAdapter().generate_video(spec) == ref

    # Image and video refs for the same brief are distinct kinds.
    image = adapter.generate_image(spec)
    assert image.placeholder_uri != ref.placeholder_uri


def test_placeholder_is_not_a_live_gen_call() -> None:
    """Structural INV-9/OUT-1: the module is a pure, offline source — no live gen.

    It imports no http client and no ``anthropic``/media SDK — there is no gen
    endpoint to call, so "$0 spend, no live gen" is provable from the source text.
    """
    module = importlib.import_module("app.adapters.media.placeholder")
    source = inspect.getsource(module)

    forbidden = (
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "anthropic",
        "random",
        "uuid4",
    )
    for token in forbidden:
        assert token not in source, f"placeholder media adapter must not reference {token!r}"


def test_registry_returns_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 default ⇒ placeholder impl; a future live mode fails loud (no overspend)."""
    monkeypatch.setenv("MEDIA_GEN_MODE", "placeholder")
    adapter = get_media_gen_adapter()
    assert isinstance(adapter, PlaceholderMediaGenAdapter)
    assert isinstance(adapter, MediaGenAdapter)

    monkeypatch.setenv("MEDIA_GEN_MODE", "live")
    with pytest.raises(NotImplementedError):
        get_media_gen_adapter()

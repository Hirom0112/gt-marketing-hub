"""W1 — LiveMediaGenAdapter + INV-8 fail-closed budget/kill-switch (TDD red→green).

These are the §4.1-adapter-scope tests for the **production** ``MediaGenAdapter``
impl. They run against a ``httpx.MockTransport`` — **no real network, no live
provider call**. The adapter is the same interface the marketing layer already
consumes (``MediaGenAdapter``), so a passing run proves the §7.3 seam is swappable
with zero core changes (INV-9).

Coverage:

- Happy path: a live image + video gen returns a NON-placeholder ref carrying the
  real ``asset_url`` (``is_placeholder=False``), and the ref still carries no
  numeric price (``cost_estimate_ref`` stays a STRING; OUT-1/INV-11).
- INV-8 fail-closed: the per-run cap and the kill switch force a degrade to the
  placeholder ($0, ``is_placeholder=True``) and make **no provider call** — proving
  no silent overspend.
- The enriched placeholder stays $0 / deterministic / ``is_placeholder=True`` and
  now carries dashboard-render hints (still strings, no price).

The provider token is a synthetic, inert-fragment fake (never a real secret), and
no real-PII string appears, so the PII-scan gate stays green (INV-1).
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.media.base import ImageRef, MediaGenAdapter, MediaSpec, VideoRef
from app.adapters.media.live_adapter import LiveMediaGenAdapter
from app.adapters.media.placeholder import PlaceholderMediaGenAdapter

# A fake provider token, assembled from inert fragments so the literal does not
# match a secret-scan signature. It is not a real secret.
_TOKEN = "r8" + "_" + "test" + "-" + "synthetic-fake-token-value"
# Model identifiers come from config (ENV) in production — here they are fixed test
# fixtures, NOT hardcoded in the adapter (INV-11).
_IMAGE_MODEL = "test-org/test-image-model"
_VIDEO_MODEL = "test-org/test-video-model"
_IMAGE_ASSET_URL = "https://replicate.delivery/test/generated-image.png"
_VIDEO_ASSET_URL = "https://replicate.delivery/test/generated-video.mp4"


def _no_numeric_price(model: BaseModel) -> None:
    """Assert a media ref carries no numeric price field — $0-provable shape (OUT-1)."""
    for value in model.model_dump().values():
        assert not isinstance(value, (int, float)) or isinstance(value, bool), (
            f"media ref must carry no numeric price; got {value!r}"
        )


class _FakeProvider:
    """Records requests and answers Replicate's /v1/predictions with a fixed output.

    Returns the image-model output as a LIST of URLs (image models commonly do) and
    the video-model output as a bare URL STRING — exercising both shapes the
    adapter's url-extractor must normalise.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        import json

        body = json.loads(request.content) if request.content else {}
        version = body.get("version")
        if version == _IMAGE_MODEL:
            return httpx.Response(
                201, json={"id": "pred-img", "status": "succeeded", "output": [_IMAGE_ASSET_URL]}
            )
        if version == _VIDEO_MODEL:
            return httpx.Response(
                201, json={"id": "pred-vid", "status": "succeeded", "output": _VIDEO_ASSET_URL}
            )
        return httpx.Response(404, json={"message": f"unknown model {version!r}"})


def _adapter(
    fake: _FakeProvider, *, cap: int = 10, kill_switch: bool = False
) -> LiveMediaGenAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(fake.handler), base_url="https://api.replicate.com"
    )
    return LiveMediaGenAdapter(
        client=client,
        token=_TOKEN,
        image_model=_IMAGE_MODEL,
        video_model=_VIDEO_MODEL,
        gens_per_run_cap=cap,
        kill_switch=kill_switch,
    )


# ===========================================================================
# Happy path — live image + video return a non-placeholder ref w/ asset URL
# ===========================================================================


def test_live_generate_image_returns_real_asset_ref() -> None:
    """A live image gen returns a non-placeholder ImageRef carrying the real URL."""
    fake = _FakeProvider()
    adapter = _adapter(fake)
    assert isinstance(adapter, MediaGenAdapter)

    ref = adapter.generate_image(MediaSpec(brief="a sunlit classroom", tier="draft"))

    assert isinstance(ref, ImageRef)
    assert ref.is_placeholder is False
    assert ref.asset_url == _IMAGE_ASSET_URL
    assert ref.placeholder_uri == _IMAGE_ASSET_URL  # mirrors asset_url for legacy readers
    assert isinstance(ref.cost_estimate_ref, str) and ref.cost_estimate_ref
    _no_numeric_price(ref)  # still no numeric price in the ref (OUT-1, INV-11)

    # Exactly one provider call was made, keyed on the configured image model.
    assert len(fake.requests) == 1
    assert fake.requests[0].url.path == "/v1/predictions"


def test_live_generate_video_returns_real_asset_ref() -> None:
    """A live video gen returns a non-placeholder VideoRef carrying the real URL."""
    fake = _FakeProvider()
    adapter = _adapter(fake)

    ref = adapter.generate_video(MediaSpec(brief="a 15s winning reel", tier="winner"))

    assert isinstance(ref, VideoRef)
    assert ref.is_placeholder is False
    assert ref.asset_url == _VIDEO_ASSET_URL
    assert ref.placeholder_uri == _VIDEO_ASSET_URL
    _no_numeric_price(ref)
    assert len(fake.requests) == 1


def test_live_adapter_uses_injected_model_ids_not_hardcoded() -> None:
    """The provider payload's ``version`` is the INJECTED model id (INV-11), not a literal."""
    import json

    fake = _FakeProvider()
    adapter = _adapter(fake)

    adapter.generate_image(MediaSpec(brief="x"))
    adapter.generate_video(MediaSpec(brief="y"))

    versions = [json.loads(r.content)["version"] for r in fake.requests]
    assert versions == [_IMAGE_MODEL, _VIDEO_MODEL]


# ===========================================================================
# INV-8 — cost cap / kill switch force degrade with NO provider call (fail closed)
# ===========================================================================


def test_cap_exhausted_degrades_to_placeholder_no_network() -> None:
    """BLOCK: the (cap+1)th gen degrades to the placeholder and makes NO provider call.

    With cap=1, the first image gen is live; the second is over budget and must
    fall back to a $0 placeholder ref — never a silent overspend (INV-8).
    """
    fake = _FakeProvider()
    adapter = _adapter(fake, cap=1)

    first = adapter.generate_image(MediaSpec(brief="first"))
    assert first.is_placeholder is False
    assert len(fake.requests) == 1  # the one live call

    second = adapter.generate_image(MediaSpec(brief="second"))
    assert second.is_placeholder is True  # degraded to placeholder ($0)
    assert second.asset_url is None
    # Fail closed: NO additional provider call was made for the over-budget gen.
    assert len(fake.requests) == 1


def test_kill_switch_degrades_to_placeholder_no_network() -> None:
    """BLOCK: the kill switch forces every gen onto the placeholder, NO provider call.

    A killed adapter must never reach the network — image and video both degrade to
    $0 placeholder refs and the fake provider records zero requests (INV-8).
    """
    fake = _FakeProvider()
    adapter = _adapter(fake, kill_switch=True)

    image = adapter.generate_image(MediaSpec(brief="should not gen"))
    video = adapter.generate_video(MediaSpec(brief="should not gen either"))

    assert image.is_placeholder is True and image.asset_url is None
    assert video.is_placeholder is True and video.asset_url is None
    _no_numeric_price(image)
    _no_numeric_price(video)
    assert fake.requests == []  # fail closed before the network


def test_live_adapter_fails_loud_without_token() -> None:
    """Misconfig: constructing a live adapter with no token raises (INV-9, fail loud)."""
    client = httpx.Client(
        transport=httpx.MockTransport(_FakeProvider().handler),
        base_url="https://api.replicate.com",
    )
    with pytest.raises(ValueError):
        LiveMediaGenAdapter(
            client=client,
            token="",
            image_model=_IMAGE_MODEL,
            video_model=_VIDEO_MODEL,
            gens_per_run_cap=10,
        )


def test_live_adapter_fails_loud_without_model_ids() -> None:
    """Misconfig: a missing model id raises at construction (INV-11/INV-9)."""
    client = httpx.Client(
        transport=httpx.MockTransport(_FakeProvider().handler),
        base_url="https://api.replicate.com",
    )
    with pytest.raises(ValueError):
        LiveMediaGenAdapter(
            client=client,
            token=_TOKEN,
            image_model="",
            video_model=_VIDEO_MODEL,
            gens_per_run_cap=10,
        )


# ===========================================================================
# The enriched placeholder stays $0 / deterministic / is_placeholder=True
# ===========================================================================


def test_enriched_placeholder_is_zero_cost_and_deterministic() -> None:
    """The enriched placeholder stays $0, deterministic, and ``is_placeholder=True``."""
    adapter = PlaceholderMediaGenAdapter()
    spec = MediaSpec(brief="a sunlit classroom of gifted learners", tier="draft")

    ref = adapter.generate_image(spec)

    assert ref.is_placeholder is True
    assert ref.asset_url is None  # no live asset on the placeholder path
    _no_numeric_price(ref)  # the render hints are STRINGS, never a price
    # Enrichment: the brief is echoed and a synthetic render hint is present.
    assert ref.brief == spec.brief
    assert ref.render_hint and ref.render_hint.endswith("png")
    # Still deterministic across instances (no entropy/clock).
    assert PlaceholderMediaGenAdapter().generate_image(spec) == ref


def test_enriched_placeholder_video_render_hint() -> None:
    """The placeholder video ref carries a deterministic mp4 render hint, still $0."""
    adapter = PlaceholderMediaGenAdapter()
    spec = MediaSpec(brief="a 15s reel celebrating a winning post", tier="winner")

    ref = adapter.generate_video(spec)

    assert ref.is_placeholder is True
    assert ref.brief == spec.brief
    assert ref.render_hint and ref.render_hint.endswith("mp4")
    _no_numeric_price(ref)
    assert PlaceholderMediaGenAdapter().generate_video(spec) == ref

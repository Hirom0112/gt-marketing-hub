"""Production MediaGenAdapter — real image/video gen behind the §7.3 seam (INV-8/9).

This is the **Production** half of the §7.3 :class:`MediaGenAdapter` seam. It calls
a real provider HTTP API (Replicate) to generate image/video assets, returning the
SAME :class:`ImageRef` / :class:`VideoRef` shape the placeholder emits — only with
``is_placeholder=False`` and the real asset URL carried in the new optional
``asset_url`` field. The placeholder impl
(:class:`app.adapters.media.placeholder.PlaceholderMediaGenAdapter`) stays the v1
default; this one is selected only when ``MEDIA_GEN_MODE=live`` with a token, a
budget, and no kill switch (the registry decides that — not this class). ``core/``
and the marketing layer change zero lines: they depend on the
:class:`MediaGenAdapter` interface, never on this class.

It mirrors the HubSpot live adapter's safety posture (INV-8 hard cap + kill switch
+ fail-loud-on-misconfig):

1. **Cap + kill-switch (INV-8).** A per-run generation budget; the (cap+1)th
   generate call **degrades to the placeholder** rather than calling the provider —
   never a silent overspend. The injected ``kill_switch`` flag forces every call
   onto the placeholder path immediately. Both degradations return a real
   ``is_placeholder=True`` ref (the $0 shape), so a caller/UI always gets a usable
   stand-in instead of a crash, while live spend is provably bounded.
2. **No numeric price in the ref (INV-11/OUT-1).** Like the placeholder, the ref
   carries a ``cost_estimate_ref`` STRING pointing at the TECH_STACK §6 cost model,
   never a dollar figure — so the result shape itself never asserts a spend.
3. **Model ids from config, not literals (INV-11).** The Replicate image/video
   model identifiers are injected (sourced from ENV per TECH_STACK §5), never
   hardcoded here.

The HTTP client is **injected** so the adapter never opens a socket under test
(tests pass an ``httpx.Client`` wired to a ``httpx.MockTransport``). When the cost
cap trips or the kill switch is on, **no provider call is made at all** (fail
closed before the network).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.media.base import ImageRef, MediaGenAdapter, MediaSpec, VideoRef
from app.adapters.media.placeholder import PlaceholderMediaGenAdapter

logger = logging.getLogger(__name__)

# Replicate's synchronous prediction surface. ``Prefer: wait`` asks the API to
# hold the connection until the prediction settles and return the final output
# inline, so we don't have to poll. This is the provider's own fixed route, not a
# tunable (INV-11 does not apply to a third party's API path).
_PREDICTIONS = "/v1/predictions"

# The cost-model pointer (a STRING, never a price) shared with the placeholder —
# the ref shape carries this, never a dollar figure (INV-11, OUT-1). Kept in sync
# with the placeholder's value so both impls point at the same §6 cost-model home.
_COST_ESTIMATE_REF = "TECH_STACK.md#6-cost-model:media-gen"


class LiveMediaGenAdapter(MediaGenAdapter):
    """Production ``MediaGenAdapter`` — real Replicate image/video gen (INV-8/9).

    Args:
        client: An injected ``httpx.Client`` (tests pass one wired to a
            ``MockTransport``). Its ``base_url`` should be the provider host
            (e.g. ``https://api.replicate.com``).
        token: The provider API token (Bearer auth).
        image_model: The Replicate image-model identifier (e.g.
            ``"black-forest-labs/flux-1.1-pro"``) — sourced from ENV, never a
            literal here (INV-11; the model is a tunable, not a constant).
        video_model: The Replicate video-model identifier (e.g.
            ``"minimax/video-01"``) — likewise from ENV (INV-11).
        gens_per_run_cap: The INV-8 per-run generation budget. The (cap+1)th
            generate call degrades to the placeholder instead of calling the
            provider — never a silent overspend.
        kill_switch: INV-8 kill switch. When ``True``, every generate call goes
            straight to the placeholder path and no provider call is ever made.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        token: str,
        image_model: str,
        video_model: str,
        gens_per_run_cap: int,
        kill_switch: bool = False,
    ) -> None:
        if not token:
            # Fail loud on misconfig (INV-9): a live adapter with no token can
            # only ever 401 — surface it at construction, not mid-run.
            raise ValueError(
                "LiveMediaGenAdapter requires a provider token; none supplied. "
                "Fail loud on misconfig rather than calling the provider unauthenticated "
                "(INV-9). Use MEDIA_GEN_MODE=placeholder when no token is configured."
            )
        if not image_model or not video_model:
            raise ValueError(
                "LiveMediaGenAdapter requires image_model and video_model identifiers "
                "(MEDIA_GEN_IMAGE_MODEL / MEDIA_GEN_VIDEO_MODEL); none supplied. Model ids "
                "are config, never hardcoded (INV-11)."
            )
        self._client = client
        self._image_model = image_model
        self._video_model = video_model
        self._cap = gens_per_run_cap
        self._kill_switch = kill_switch
        self._gens_made = 0
        # A placeholder instance owns the degrade path — one canonical home for the
        # $0 stub shape (INV-11), so a budget/kill-switch degrade is identical to
        # the v1 default rather than a forked, drifting stub here.
        self._placeholder = PlaceholderMediaGenAdapter()
        self._client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------ budget
    def _budget_available(self) -> bool:
        """True only when a live generate is permitted: kill switch off AND under cap.

        Checked BEFORE any provider call, so an exhausted budget or an engaged
        kill switch never reaches the network (fail closed; INV-8).
        """
        return not self._kill_switch and self._gens_made < self._cap

    # ------------------------------------------------------------------ I/O
    def _predict(self, model: str, prompt: str) -> str:
        """Run one budgeted provider prediction; return the generated asset URL.

        Increments the per-run counter (the budget guard already proved capacity).
        A non-2xx response raises via ``raise_for_status``. The provider returns
        ``output`` either as a bare URL string or a list of URLs (image models
        commonly return a list); both are normalised to a single URL.
        """
        self._gens_made += 1
        payload = {"version": model, "input": {"prompt": prompt}}
        response = self._client.post(_PREDICTIONS, json=payload, headers={"Prefer": "wait"})
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return _extract_asset_url(body)

    # --------------------------------------------------------------- interface
    def generate_image(self, spec: MediaSpec) -> ImageRef:
        """Generate a real image for ``spec`` — cheapest-first draft tier (§7.3).

        Degrades to the placeholder ``ImageRef`` (``is_placeholder=True``, $0) when
        the kill switch is on or the per-run budget is exhausted — never a silent
        overspend, never a crash (INV-8). On the live path the ref carries the real
        ``asset_url`` and ``is_placeholder=False``; ``cost_estimate_ref`` stays a
        STRING (no numeric price; INV-11/OUT-1).
        """
        if not self._budget_available():
            logger.info(
                "media-gen image: degrading to placeholder (kill_switch=%s, gens_made=%d, cap=%d) "
                "— never a silent overspend (INV-8).",
                self._kill_switch,
                self._gens_made,
                self._cap,
            )
            return self._placeholder.generate_image(spec)

        asset_url = self._predict(self._image_model, spec.brief)
        return ImageRef(
            placeholder_uri=asset_url,
            asset_url=asset_url,
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=False,
        )

    def generate_video(self, spec: MediaSpec) -> VideoRef:
        """Generate a real video for ``spec`` — winner-gated tier (§7.3).

        Same INV-8 posture as :meth:`generate_image`: degrade to the placeholder
        ``VideoRef`` ($0, ``is_placeholder=True``) when the kill switch is on or the
        budget is exhausted; otherwise return a live ref with the real ``asset_url``
        and ``is_placeholder=False`` (``cost_estimate_ref`` stays a string).
        """
        if not self._budget_available():
            logger.info(
                "media-gen video: degrading to placeholder (kill_switch=%s, gens_made=%d, cap=%d) "
                "— never a silent overspend (INV-8).",
                self._kill_switch,
                self._gens_made,
                self._cap,
            )
            return self._placeholder.generate_video(spec)

        asset_url = self._predict(self._video_model, spec.brief)
        return VideoRef(
            placeholder_uri=asset_url,
            asset_url=asset_url,
            cost_estimate_ref=_COST_ESTIMATE_REF,
            is_placeholder=False,
        )


def _extract_asset_url(body: dict[str, Any]) -> str:
    """Normalise a Replicate prediction body's ``output`` to a single asset URL.

    Replicate returns ``output`` as a bare URL string (video models) or a list of
    URLs (image models commonly return one or more). A missing/empty output is a
    provider contract breach — fail loud (INV-9) rather than emit an empty ref.
    """
    output = body.get("output")
    if isinstance(output, str) and output:
        return output
    if isinstance(output, list) and output and isinstance(output[0], str):
        return output[0]
    raise RuntimeError(
        f"provider returned no usable asset URL in prediction output: {output!r}. "
        "Fail loud rather than emit an empty media ref (INV-9)."
    )

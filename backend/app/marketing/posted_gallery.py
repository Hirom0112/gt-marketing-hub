"""Posted-content gallery — kept assets as posts grouped by platform (FR-3.4; §5).

The marketing library is also a POSTED-CONTENT GALLERY: every kept + validated
:class:`~app.ai.schemas.brand.LibraryAsset` that carries a social platform tag (GT's
own proven captions, distilled from the real public posts — synthetic-shaped, INV-1)
is a "post" we made, grouped by the platform it came FROM. Each post surfaces:

* ``platform`` — the origin platform tag (the "WHERE"): facebook / instagram / x/twitter
  / tiktok / youtube. This is the ORIGINAL platform tag, not the collapsed
  :class:`~app.ai.schemas.content.Channel` (the ingest maps FB/YouTube → INSTAGRAM, so
  the channel field loses the true origin — the tag keeps it).
* ``caption`` — the asset ``body`` (the WORDS posted with the picture).
* ``image_ref`` — a PLACEHOLDER reference (media-gen isn't wired yet; a real image feed
  is a future wire-up). Never empty so the gallery always renders a tile.
* ``posted_at`` — a DETERMINISTIC SYNTHETIC date. The import provenance ts is fixed, so
  a stable hash of the id backdates each post into the params window before the import
  epoch, giving the "most recent" sort a believable spread (no real publish-time feed).
* ``value`` — a DETERMINISTIC SYNTHETIC value, the "most valuable" sort key: a stable
  hash of the id mapped into the params ``[value_min, value_max]`` band. There is NO
  per-post engagement metric today (a real engagement feed is a future wire-up); this is
  a documented placeholder, the same posture as the work-queue value spread.

Both ``value`` and ``posted_at`` read their band/window from ``params`` (INV-11) — never
a code literal — so a drifted param moves the result and the tests fail.

Pure core (CLAUDE.md §3): imports only the schemas, the typed params, pydantic and
stdlib (``hashlib`` / ``datetime``) — no ``anthropic`` / ``langgraph`` / network /
``datetime.now`` / ``uuid4``. Read-only over already-kept assets; it writes nothing.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from app.ai.schemas.brand import LibraryAsset
from app.core.params import Params

# The closed set of origin-platform tags the ingest stamps on a social COPY asset
# (CONTENT_SPEC §5 ingest). The presence of one of these in `tags` is what makes a
# kept asset a "posted" gallery item — its true origin platform (the "WHERE"). FB /
# YouTube collapse onto the INSTAGRAM Channel, so the tag is the only faithful origin.
_PLATFORM_TAGS: tuple[str, ...] = (
    "instagram",
    "x/twitter",
    "youtube",
    "facebook",
    "tiktok",
)

# The fixed import epoch the synthetic posted_at backdates FROM (mirrors
# `library_ingest._IMPORT_TS`). A constant seam (one home), not a tunable: the window
# WIDTH is the tunable (`params.posted_gallery.posted_within_days`); the anchor is the
# import provenance instant the assets were distilled at.
_IMPORT_EPOCH = datetime(2026, 6, 15, tzinfo=UTC)

# The image placeholder scheme. Media-gen isn't wired (OUT-1 / STATE "wire real media
# generation"), so a kept post has no generated image yet; the gallery renders a stable
# placeholder ref keyed on the asset id so the tile is deterministic and never blank.
_IMAGE_PLACEHOLDER_PREFIX = "placeholder://posted-gallery/"


def _stable_unit(asset_id: str, salt: str) -> float:
    """A deterministic float in [0,1) from the asset id + a salt (no randomness).

    A SHA-256 digest of ``{salt}:{asset_id}`` mapped from its first 8 bytes into the
    unit interval — stable across runs/processes (never ``random`` / ``uuid4``), and
    the salt decorrelates the value and posted_at hashes so they spread independently.
    """
    digest = hashlib.sha256(f"{salt}:{asset_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def posted_platform(asset: LibraryAsset) -> str | None:
    """The post's origin platform tag (the "WHERE"), or ``None`` if not a social post.

    Returns the first platform tag present in ``asset.tags`` (the true origin, not the
    collapsed :class:`Channel`). An asset with no platform tag is not a posted gallery
    item (e.g. a website page) and returns ``None``.
    """
    tags = set(asset.tags)
    for platform in _PLATFORM_TAGS:
        if platform in tags:
            return platform
    return None


def gallery_value(asset_id: str, params: Params) -> float:
    """Deterministic synthetic per-post value in the params band (the most-valuable key).

    No per-post engagement feed is tracked yet (a real feed is a future wire-up), so
    value is a stable hash of ``asset_id`` mapped into
    ``[posted_gallery.value_min, posted_gallery.value_max]`` (INV-11 — the band is
    params-homed, never a literal). A documented placeholder, the same posture as the
    work-queue value spread; deterministic, so the "most valuable" sort is stable.
    """
    cfg = params.posted_gallery
    unit = _stable_unit(asset_id, "value")
    return round(cfg.value_min + unit * (cfg.value_max - cfg.value_min), 2)


def posted_at(asset_id: str, params: Params) -> str:
    """Deterministic synthetic posted-at date (ISO ``YYYY-MM-DD``) for the recent sort.

    The import provenance ts is fixed, so posted_at is also a placeholder: a stable hash
    backdates the post by ``[1, posted_gallery.posted_within_days]`` days from the fixed
    import epoch (INV-11 — the window is params-homed). Deterministic, so the "most
    recent" sort is stable. A real publish-time feed is a future wire-up.
    """
    window = params.posted_gallery.posted_within_days
    unit = _stable_unit(asset_id, "posted_at")
    days_ago = 1 + int(unit * window)
    return (_IMPORT_EPOCH - timedelta(days=days_ago)).date().isoformat()


def _image_ref(asset: LibraryAsset) -> str:
    """A stable, non-empty image placeholder ref (media-gen not wired yet, OUT-1)."""
    return f"{_IMAGE_PLACEHOLDER_PREFIX}{asset.id}"


class PostItem(BaseModel):
    """One posted-content gallery card — the picture, the words, and WHERE (frozen).

    ``image_ref`` is a PLACEHOLDER (media-gen isn't wired yet); ``value`` and
    ``posted_at`` are deterministic synthetic placeholders (no real engagement /
    publish-time feed). ``platform`` is the origin tag (the "WHERE"), not the collapsed
    channel.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    platform: str
    asset_type: str
    caption: str
    image_ref: str
    posted_at: str
    value: float


class PlatformGroup(BaseModel):
    """A platform tile — the platform and how many posts it holds (frozen)."""

    model_config = ConfigDict(frozen=True)

    platform: str
    count: int


class GalleryView(BaseModel):
    """The posted-content gallery view (frozen).

    ``groups`` are the platform tiles (with per-platform counts) shown when no platform
    is drilled into; ``posts`` is the post grid for the active platform (empty groups
    when a platform filter is set — the "click into Facebook" drill). Both empty on no
    data (degrade cleanly, never an error).
    """

    model_config = ConfigDict(frozen=True)

    groups: list[PlatformGroup]
    posts: list[PostItem]


# The supported sort keys for the gallery grid. "most_valuable" sorts by the synthetic
# value desc; "most_recent" by the synthetic posted_at desc. Any other value falls back
# to most_recent (a stable, sensible default — never an error).
_VALID_SORTS = ("most_valuable", "most_recent")


def build_gallery(
    assets: list[LibraryAsset],
    *,
    params: Params,
    platform: str | None = None,
    sort: str = "most_recent",
) -> GalleryView:
    """Build the posted-content gallery from kept assets (FR-3.4; read-only).

    Keeps only assets that carry a social platform tag (a "post" we made), projects each
    into a :class:`PostItem` (caption + image placeholder + deterministic synthetic
    posted_at + value), and either:

    * groups them into per-platform :class:`PlatformGroup` tiles (when ``platform`` is
      ``None`` — the gallery landing), with no flat ``posts`` requested yet; or
    * filters to a single ``platform`` and returns its sorted ``posts`` grid (the "click
      into Facebook" drill), with no groups.

    ``sort`` orders the drilled grid: ``most_valuable`` by value desc, ``most_recent``
    (default / fallback) by posted_at desc; ties broken by id for a stable order. Empty
    input degrades cleanly to empty groups + posts (never raises).
    """
    posts: list[PostItem] = []
    for asset in assets:
        origin = posted_platform(asset)
        if origin is None:
            continue
        posts.append(
            PostItem(
                id=asset.id,
                platform=origin,
                asset_type=asset.asset_type.value,
                caption=asset.body or "",
                image_ref=_image_ref(asset),
                posted_at=posted_at(asset.id, params),
                value=gallery_value(asset.id, params),
            )
        )

    if platform is None:
        # Gallery landing: per-platform tiles with counts (sorted by count desc, then
        # platform for a stable order). No flat post grid until a platform is drilled.
        counts: dict[str, int] = {}
        for post in posts:
            counts[post.platform] = counts.get(post.platform, 0) + 1
        groups = [
            PlatformGroup(platform=name, count=count)
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return GalleryView(groups=groups, posts=[])

    # Drill into one platform: only its posts, sorted by the requested key.
    drilled = [post for post in posts if post.platform == platform]
    sort_key = sort if sort in _VALID_SORTS else "most_recent"
    if sort_key == "most_valuable":
        drilled.sort(key=lambda p: (-p.value, p.id))
    else:
        drilled.sort(key=lambda p: (p.posted_at, p.id), reverse=True)
    return GalleryView(groups=[], posts=drilled)

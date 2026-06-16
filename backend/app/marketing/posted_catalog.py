"""Posted-CATALOG reader — the REAL posted gallery source (FR-3.4; INV-1 exception).

The marketing gallery's real source is GT's own scraped public marketing catalog: a
``catalog/catalog.csv`` (one row per post) plus real media under ``social/<platform>/``,
living at an EXTERNAL, env-configured path (``GT_POSTED_CATALOG_ROOT``). Surfacing GT's own
public posts inside GT's own internal cockpit is a **documented, scoped INV-1 exception**
(ASSUMPTIONS); the hard boundary is that **nothing real is ever committed** — this module
reads the catalog + serves media AT RUNTIME from the external path, while every test runs
against a tiny SYNTHETIC fixture CSV (`tests/.../fixtures/catalog.csv`).

Each catalog row becomes a :class:`PostedCatalogItem`:

* ``platform`` — the post's origin platform, case-normalised to a key
  (``instagram`` / ``facebook`` / ``x/twitter`` / ``youtube`` / ``tiktok``); ``X`` and
  ``Twitter`` collapse onto ``x/twitter``. An unknown label lower-cases through unchanged.
* ``caption`` — the words posted with the media.
* ``asset_type`` — ``video`` for ``.mp4/.mov/.webm`` media, else ``image`` (from the
  ``media_file`` extension).
* ``media_ref`` — the SERVED url ``"/posted-media/" + media_file`` (the static mount in
  ``app.main``); the browser resolves it against the API base.
* ``posted_at`` — the catalog timestamp itself (a REAL publish time, not a synthetic
  backdate — this path has the true dates).
* ``likes`` / ``views`` / ``comments`` — ints; an EMPTY cell reads as 0.
* ``value`` — the REAL engagement composite (:func:`catalog_value`): a params-weighted sum
  of likes/views/comments (INV-11 — the weights live in ``params``, never a code literal).
* ``url`` — the original post url (the "View original" deep link).

Pure-ish (CLAUDE §3): imports only the typed params, pydantic, and stdlib (``csv`` /
``pathlib``); reads a file the caller names, writes nothing, no network / ``datetime.now`` /
``uuid4``. A missing catalog reads as empty (degrade cleanly, never raises).
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.core.params import Params

# Where the catalog CSV lives under the configured scrape root.
_CATALOG_RELPATH = ("catalog", "catalog.csv")

# The static-mount prefix the served media_ref is built on (mirrors app.main's mount).
_MEDIA_PREFIX = "/posted-media/"

# Media extensions that mark a post as a video; everything else is an image.
_VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mov", ".webm"})

# Raw catalog platform label (lower-cased) → the normalised origin-platform key the
# gallery + frontend share. Labels not listed lower-case through unchanged (graceful).
_PLATFORM_KEYS: dict[str, str] = {
    "instagram": "instagram",
    "facebook": "facebook",
    "x": "x/twitter",
    "twitter": "x/twitter",
    "x/twitter": "x/twitter",
    "youtube": "youtube",
    "tiktok": "tiktok",
}


def _normalise_platform(raw: str) -> str:
    """Normalise a catalog platform label to a stable lower-case key.

    ``X`` and ``Twitter`` collapse onto ``x/twitter`` (the same key the library gallery
    uses); a known label maps through the table; an unknown label lower-cases unchanged.
    """
    key = raw.strip().lower()
    return _PLATFORM_KEYS.get(key, key)


def _asset_type(media_file: str) -> str:
    """``video`` for a video extension, else ``image`` (from the media_file suffix)."""
    return "video" if Path(media_file).suffix.lower() in _VIDEO_EXTS else "image"


def _as_int(raw: str | None) -> int:
    """Parse an engagement count; an empty/blank/absent cell counts as 0."""
    if raw is None or raw.strip() == "":
        return 0
    return int(raw.strip())


def catalog_value(*, likes: int, views: int, comments: int, params: Params) -> float:
    """The REAL engagement composite — the most-valuable sort key (INV-11).

    ``like_weight·likes + view_weight·views + comment_weight·comments`` with the three
    weights read from ``params.posted_gallery.engagement`` (never a code literal — a
    drifted weight moves the ranking and the test fails). Zero engagement ⇒ 0.0.
    """
    cfg = params.posted_gallery.engagement
    return round(
        likes * cfg.like_weight + views * cfg.view_weight + comments * cfg.comment_weight, 2
    )


class PostedCatalogItem(BaseModel):
    """One real posted-catalog post (frozen) — the picture, the words, and the metrics.

    ``media_ref`` is a SERVED url (``/posted-media/<media_file>``); ``value`` is the real
    engagement composite. Read-only projection of a catalog row — never a state write.
    """

    model_config = ConfigDict(frozen=True)

    platform: str
    caption: str
    asset_type: str
    media_ref: str
    posted_at: str
    likes: int
    views: int
    comments: int
    value: float
    url: str


def _catalog_path(root: Path) -> Path:
    """The catalog CSV path under the configured scrape ``root``."""
    return root.joinpath(*_CATALOG_RELPATH)


def read_posted_catalog(root: Path, *, params: Params) -> list[PostedCatalogItem]:
    """Read the real posted catalog under ``root`` into posts (FR-3.4; read-only).

    Parses ``<root>/catalog/catalog.csv`` row by row into :class:`PostedCatalogItem`s,
    normalising the platform, deriving asset_type + the served media_ref, treating empty
    engagement cells as 0, and computing the params-weighted engagement ``value``. A
    missing catalog (or root) reads as an empty list — never raises (degrade cleanly).
    """
    path = _catalog_path(root)
    if not path.is_file():
        return []

    posts: list[PostedCatalogItem] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            media_file = (row.get("media_file") or "").strip()
            likes = _as_int(row.get("likes"))
            views = _as_int(row.get("views_plays"))
            comments = _as_int(row.get("comments"))
            posts.append(
                PostedCatalogItem(
                    platform=_normalise_platform(row.get("platform") or ""),
                    caption=(row.get("caption") or "").strip(),
                    asset_type=_asset_type(media_file),
                    media_ref=_MEDIA_PREFIX + media_file,
                    posted_at=(row.get("date") or "").strip(),
                    likes=likes,
                    views=views,
                    comments=comments,
                    value=catalog_value(likes=likes, views=views, comments=comments, params=params),
                    url=(row.get("url") or "").strip(),
                )
            )
    return posts

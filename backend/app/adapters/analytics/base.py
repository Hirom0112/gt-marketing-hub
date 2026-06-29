"""The website-analytics (GA4) boundary — interface + window/snapshot models (Module 13).

The §7-style external boundary for Google Analytics (GA4). The website-analytics
layer depends ONLY on this interface, never on a concrete GA4 client, and only ever
sees AGGREGATES — sessions/pageviews/bounce/duration by site and page, channel and
campaign session counts, download counts, and conversion-path session counts. There is
deliberately **no per-person, per-session, or child-keyed field** anywhere in these
models (INV-6: no minor targeting; P-4). Every count is an aggregate.

INV-9: like every external boundary, this is an interface with (at least) two impls —
Simulated (v1, stood-in: no live GA4 credentials in this portal) and a future GA4
Data-API impl — selected by config in :mod:`app.adapters.registry`. The v1 simulated
impl is a pure, offline source over synthetic data with no network client at all, so
"no live read" is a STRUCTURAL property, provable from the source text alone. This
module imports nothing from ``app.core`` / ``app.ai`` and keeps ``core/`` untouched.

``source_mode`` is surfaced on every snapshot (``simulated`` in v1; never ``ga4_live``
until a real GA4 impl is wired) so the UI labels provenance honestly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

SourceMode = Literal["simulated", "ga4_live"]


class AnalyticsWindow(BaseModel):
    """The reporting window to read analytics over (an inclusive ``start``/``end``).

    Frozen — a window is an immutable request, not mutable state. The opaque date
    strings mirror :class:`app.adapters.sentiment.base.SentimentWindow`.
    """

    model_config = ConfigDict(frozen=True)

    start: str
    end: str


class _Aggregate(BaseModel):
    """Base for every aggregate read row — frozen, aggregate-only (INV-6)."""

    model_config = ConfigDict(frozen=True)


class SiteMetric(_Aggregate):
    """Per-site session/engagement aggregates (one GA4 property).

    Attributes:
        site: The property label (a ``params.website.sites`` token).
        sessions: Total sessions this window (aggregate).
        users: Distinct users this window (aggregate count, not keyed).
        new_users: First-time users (aggregate).
        returning_users: Returning users (aggregate).
        bounce_rate: Share of single-page sessions (0..1).
        avg_session_duration_s: Mean session duration in seconds.
        pageviews: Total pageviews this window (aggregate).
    """

    site: str
    sessions: int
    users: int
    new_users: int
    returning_users: int
    bounce_rate: float
    avg_session_duration_s: float
    pageviews: int


class PageMetric(_Aggregate):
    """Per-page aggregates across both sites (the subpage table; 13b).

    Attributes:
        page_path: The page path (e.g. ``/tuition``).
        site: The property the page lives on.
        page_type: A ``params.website.page_types`` token (landing/blog/resource/...).
        pageviews: Total pageviews this window (aggregate).
        prev_pageviews: Pageviews the PRIOR window (for the weekly trend).
        unique_visitors: Distinct visitors (aggregate count).
        avg_time_on_page_s: Mean time on page in seconds.
        bounce_rate: Single-page-session share for this page (0..1).
        exit_rate: Exit share for this page (0..1).
        conversions: Conversion events on the page (form subs + downloads; aggregate).
    """

    page_path: str
    site: str
    page_type: str
    pageviews: int
    prev_pageviews: int
    unique_visitors: int
    avg_time_on_page_s: float
    bounce_rate: float
    exit_rate: float
    conversions: int


class SourceMetric(_Aggregate):
    """Per-channel traffic aggregates (13c); ``platform`` set only for social.

    Attributes:
        channel: A ``params.website.traffic_channels`` token (organic/direct/...).
        platform: The social platform (x/facebook/instagram) when channel==social,
            else ``None``.
        sessions: Sessions attributed to this channel/platform (aggregate).
        conversions: Conversions attributed to this channel/platform (aggregate).
    """

    channel: str
    platform: str | None
    sessions: int
    conversions: int


class CampaignSource(_Aggregate):
    """One UTM-tagged campaign source (13c → CRM-Ops attribution-chain validation).

    The raw UTM tags are carried verbatim so the website surface can run the SAME
    ``core.utm_health.check_utm`` rule set CRM Ops uses — surfacing the broken tags at
    their ORIGIN (the website is where UTM parameters originate). A blank value models a
    missing tag (a broken UTM).

    Attributes:
        utm_source / utm_medium / utm_campaign: The campaign's UTM tags (verbatim).
        sessions: Sessions attributed to this tagged campaign (aggregate).
        landing_page: The page the campaign lands on.
    """

    utm_source: str
    utm_medium: str
    utm_campaign: str
    sessions: int
    landing_page: str


class SourcePageCell(_Aggregate):
    """One cell of the source×page matrix (13c): channel → page session count."""

    channel: str
    page_path: str
    sessions: int


class DownloadMetric(_Aggregate):
    """One downloadable asset's tracking aggregates (13d).

    Attributes:
        file_name: The asset file name.
        weekly_count: Downloads this window (aggregate).
        cumulative_count: All-time downloads (aggregate).
        prev_weekly_count: Downloads the prior window (for the trend).
        referring_page: The page the visitor downloaded from.
        source: How the downloader originally arrived (a channel token).
    """

    file_name: str
    weekly_count: int
    cumulative_count: int
    prev_weekly_count: int
    referring_page: str
    source: str


class ConversionPage(_Aggregate):
    """A page with a form-submission conversion rate (13e key-conversion-pages).

    Attributes:
        page_path: The page path.
        site: The property the page lives on.
        sessions: Sessions that reached this page (aggregate).
        form_submissions: Form submissions on this page (aggregate).
    """

    page_path: str
    site: str
    sessions: int
    form_submissions: int


class PathFlow(_Aggregate):
    """One step of the homepage→… user flow (13e): ``from_page`` → ``to_page``."""

    from_page: str
    to_page: str
    sessions: int


class CrossSiteFlow(_Aggregate):
    """A cross-site visitor flow (13e): ``from_site`` → ``to_site`` session count."""

    from_site: str
    to_site: str
    sessions: int


class FunnelStage(_Aggregate):
    """One stage of the landing→application funnel (13e drop-off).

    Attributes:
        stage: The stage label (e.g. ``landing`` / ``program_page`` / ``apply`` ...).
        sessions: Sessions reaching this stage (monotonically non-increasing; aggregate).
    """

    stage: str
    sessions: int


class AnalyticsSnapshot(_Aggregate):
    """One aggregate GA4 read bundling every sub-view's rows (one boundary call).

    The whole snapshot is read in a single :meth:`AnalyticsAdapter.snapshot` call (the
    "one GA4 read" the surface spends), so the pure core derives every rollup from a
    consistent point-in-time view. ``source_mode`` is surfaced honestly.
    """

    source_mode: SourceMode = "simulated"
    sites: list[SiteMetric]
    pages: list[PageMetric]
    sources: list[SourceMetric]
    campaigns: list[CampaignSource]
    source_pages: list[SourcePageCell]
    downloads: list[DownloadMetric]
    conversion_pages: list[ConversionPage]
    path_flows: list[PathFlow]
    cross_site_flows: list[CrossSiteFlow]
    funnel: list[FunnelStage]


class AnalyticsAdapter(ABC):
    """The website-analytics (GA4) external boundary (Module 13).

    Two impls — Simulated (v1 stood-in) and a future GA4 Data-API impl — selected by
    config in :mod:`app.adapters.registry`. The website-analytics layer depends only on
    this interface, never on a concrete client, and only ever sees aggregates (INV-6).
    """

    @abstractmethod
    def snapshot(self, window: AnalyticsWindow) -> AnalyticsSnapshot:
        """Read one aggregate analytics snapshot for ``window`` (aggregate-only)."""
        raise NotImplementedError

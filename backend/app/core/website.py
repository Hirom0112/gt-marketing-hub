"""Pure Website & Digital-Analytics derivations (Module 13; INV-2 / INV-6 / INV-11).

The deterministic core behind the website-analytics surface: given an aggregate GA4
snapshot (site/page/source/campaign/download/conversion-path rows), compute

1. the SITE rollup (13a hero figures): total sessions + pageviews, new-vs-returning
   split, and session-weighted bounce rate + average session duration,
2. the TOP landing pages by traffic + the per-page weekly TREND, and the
   content-refresh CANDIDATES (pages whose bounce rate clears the params threshold —
   the leadership-flag hint),
3. the TRAFFIC breakdown (13c): per-channel sessions/conversions with the social
   platform split, channel shares + conversion rates,
4. the UTM source VALIDATION (13c → Module 7): the SAME ``core.utm_health.check_utm``
   rule set CRM Ops uses, run over the tagged campaigns at the ORIGIN of the tags, so a
   broken UTM is flagged where it is born (never auto-fixed — the honesty mandate),
5. the DOWNLOAD summary (13d): weekly/cumulative totals + week-over-week delta, and
6. the CONVERSION-path figures (13e): key conversion pages by form-submission rate +
   the landing→application funnel drop-off.

This is the deterministic, *pure* core (mirrors :mod:`app.core.admissions` /
:mod:`app.core.content_analytics`): a function of its inputs + the injected params dials
alone — no repository, adapter (incl. the GA4 adapter), decision-queue, httpx, or LLM
import (the core-purity test guards this). The snapshot is read STRUCTURALLY (``*Like``
Protocols) so the core never imports ``app.adapters``. It MAY reuse
:func:`app.core.utm_health.check_utm` (core→core). Every threshold/size is INJECTED from
params (INV-11). Aggregate-only (INV-6): no per-person/child-keyed field ever enters here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from app.core.params import Params
from app.core.utm_health import check_utm


# ---------------------------------------------------------------------------
# Structural shapes the core reads (source-agnostic). The adapter models satisfy these
# structurally, so the API passes snapshot rows straight in and the pure core never
# imports the adapter layer (the admissions ``*Like`` pattern).
# ---------------------------------------------------------------------------
class SiteLike(Protocol):
    """The shape the core reads off one per-site metric row."""

    @property
    def site(self) -> str: ...
    @property
    def sessions(self) -> int: ...
    @property
    def new_users(self) -> int: ...
    @property
    def returning_users(self) -> int: ...
    @property
    def bounce_rate(self) -> float: ...
    @property
    def avg_session_duration_s(self) -> float: ...
    @property
    def pageviews(self) -> int: ...


class PageLike(Protocol):
    """The shape the core reads off one per-page metric row."""

    @property
    def page_path(self) -> str: ...
    @property
    def page_type(self) -> str: ...
    @property
    def pageviews(self) -> int: ...
    @property
    def prev_pageviews(self) -> int: ...
    @property
    def bounce_rate(self) -> float: ...


class SourceLike(Protocol):
    """The shape the core reads off one traffic-source row."""

    @property
    def channel(self) -> str: ...
    @property
    def platform(self) -> str | None: ...
    @property
    def sessions(self) -> int: ...
    @property
    def conversions(self) -> int: ...


class CampaignLike(Protocol):
    """The shape the core reads off one UTM-tagged campaign row."""

    @property
    def utm_source(self) -> str: ...
    @property
    def utm_medium(self) -> str: ...
    @property
    def utm_campaign(self) -> str: ...
    @property
    def sessions(self) -> int: ...
    @property
    def landing_page(self) -> str: ...


class DownloadLike(Protocol):
    """The shape the core reads off one download row."""

    @property
    def weekly_count(self) -> int: ...
    @property
    def cumulative_count(self) -> int: ...
    @property
    def prev_weekly_count(self) -> int: ...


class ConversionPageLike(Protocol):
    """The shape the core reads off one conversion-page row."""

    @property
    def page_path(self) -> str: ...
    @property
    def site(self) -> str: ...
    @property
    def sessions(self) -> int: ...
    @property
    def form_submissions(self) -> int: ...


class FunnelLike(Protocol):
    """The shape the core reads off one funnel stage."""

    @property
    def stage(self) -> str: ...
    @property
    def sessions(self) -> int: ...


def _pct(part: float, whole: float) -> int:
    """``round(100 * part / whole)`` (0 when ``whole`` is non-positive; never div-by-0)."""
    return round(100 * part / whole) if whole > 0 else 0


def _rate(part: float, whole: float) -> float:
    """``part / whole`` rounded to 4 dp (0.0 when ``whole`` is non-positive)."""
    return round(part / whole, 4) if whole > 0 else 0.0


def site_rollup(sites: Iterable[SiteLike]) -> dict[str, object]:
    """The 13a aggregate hero rollup across all sites (session-weighted averages).

    Keys: ``total_sessions`` / ``total_pageviews`` / ``total_new`` / ``total_returning``
    (sums), ``new_pct`` / ``returning_pct`` (share of new+returning), ``avg_bounce_rate``
    and ``avg_session_duration_s`` (session-WEIGHTED means — a small high-bounce site
    cannot drag the blended figure; 0.0 when there are no sessions).
    """
    rows = list(sites)
    total_sessions = sum(s.sessions for s in rows)
    total_pageviews = sum(s.pageviews for s in rows)
    total_new = sum(s.new_users for s in rows)
    total_returning = sum(s.returning_users for s in rows)
    weighted_bounce = sum(s.bounce_rate * s.sessions for s in rows)
    weighted_duration = sum(s.avg_session_duration_s * s.sessions for s in rows)
    nr_total = total_new + total_returning
    return {
        "total_sessions": total_sessions,
        "total_pageviews": total_pageviews,
        "total_new": total_new,
        "total_returning": total_returning,
        "new_pct": _pct(total_new, nr_total),
        "returning_pct": _pct(total_returning, nr_total),
        "avg_bounce_rate": _rate(weighted_bounce, total_sessions),
        "avg_session_duration_s": round(_rate(weighted_duration, total_sessions), 1),
    }


def top_landing_pages[P: PageLike](pages: Iterable[P], *, n: int) -> list[P]:
    """The top ``n`` pages by pageviews (descending; first-seen tie-break).

    ``n`` is read from ``params.website.top_landing_n`` at the edge (INV-11). Returns the
    SAME row objects so the API serialises them directly (the admissions ``top_*`` pattern).
    """
    ranked = sorted(pages, key=lambda p: p.pageviews, reverse=True)
    return ranked[: max(0, n)]


def page_trend_pct(page: PageLike) -> int:
    """One page's week-over-week pageview trend as a signed percent (0 with no prior)."""
    if page.prev_pageviews <= 0:
        return 0
    return round(100 * (page.pageviews - page.prev_pageviews) / page.prev_pageviews)


def refresh_candidates[P: PageLike](pages: Iterable[P], *, bounce_warn_pct: float) -> list[P]:
    """Pages whose bounce rate clears the refresh threshold (the leadership-flag hint).

    ``bounce_warn_pct`` is read from ``params.website.bounce_warn_pct`` (INV-11). A page at
    or above the threshold reads as a content-refresh candidate — surfaced, never
    auto-actioned (leadership decides). Sorted worst-bounce first.
    """
    flagged = [p for p in pages if p.bounce_rate >= bounce_warn_pct]
    return sorted(flagged, key=lambda p: p.bounce_rate, reverse=True)


def traffic_breakdown(sources: Iterable[SourceLike]) -> dict[str, object]:
    """The 13c per-channel traffic breakdown with the social platform split.

    Keys: ``total_sessions`` / ``total_conversions`` (sums); ``channels`` — one row per
    channel (social channels MERGED into one ``social`` row) with ``sessions`` /
    ``conversions`` / ``share_pct`` (of total sessions) / ``conversion_rate`` (conversions
    / sessions); ``social_platforms`` — the per-platform split under ``social``.
    """
    rows = list(sources)
    total_sessions = sum(s.sessions for s in rows)
    total_conversions = sum(s.conversions for s in rows)

    by_channel: dict[str, dict[str, int]] = {}
    platforms: list[dict[str, object]] = []
    for s in rows:
        agg = by_channel.setdefault(s.channel, {"sessions": 0, "conversions": 0})
        agg["sessions"] += s.sessions
        agg["conversions"] += s.conversions
        if s.channel == "social" and s.platform is not None:
            platforms.append(
                {
                    "platform": s.platform,
                    "sessions": s.sessions,
                    "conversions": s.conversions,
                    "conversion_rate": _rate(s.conversions, s.sessions),
                }
            )

    channels = [
        {
            "channel": channel,
            "sessions": agg["sessions"],
            "conversions": agg["conversions"],
            "share_pct": _pct(agg["sessions"], total_sessions),
            "conversion_rate": _rate(agg["conversions"], agg["sessions"]),
        }
        for channel, agg in by_channel.items()
    ]
    channels.sort(key=lambda c: c["sessions"], reverse=True)  # type: ignore[arg-type,return-value]
    platforms.sort(key=lambda p: p["sessions"], reverse=True)  # type: ignore[arg-type,return-value]
    return {
        "total_sessions": total_sessions,
        "total_conversions": total_conversions,
        "channels": channels,
        "social_platforms": platforms,
    }


def validate_campaign_utms(
    campaigns: Iterable[CampaignLike], *, params: Params
) -> dict[str, object]:
    """The 13c UTM source validation (→ Module 7 CRM-Ops attribution chain).

    Runs the SAME ``core.utm_health.check_utm`` rule set CRM Ops uses over each tagged
    campaign's UTM mapping — flagging a broken UTM at the ORIGIN of the tags (the website
    is where UTM parameters originate). DETECT-only (the honesty mandate): nothing is
    normalised. Keys: ``total`` / ``healthy`` / ``broken`` (counts), ``broken_count``,
    ``health_pct`` (healthy share), and ``broken_campaigns`` — one row per broken campaign
    with its tags, sessions, offending keys, and human reasons (the CRM-Ops drill-in feed).
    """
    rows = list(campaigns)
    broken_campaigns: list[dict[str, object]] = []
    healthy = 0
    for c in rows:
        utm = {
            "utm_source": c.utm_source,
            "utm_medium": c.utm_medium,
            "utm_campaign": c.utm_campaign,
        }
        verdict = check_utm(utm, params=params)
        if verdict.status == "broken":
            broken_campaigns.append(
                {
                    "utm_source": c.utm_source,
                    "utm_medium": c.utm_medium,
                    "utm_campaign": c.utm_campaign,
                    "sessions": c.sessions,
                    "landing_page": c.landing_page,
                    "offending_keys": list(verdict.offending_keys),
                    "reasons": list(verdict.reasons),
                }
            )
        else:
            healthy += 1
    total = len(rows)
    return {
        "total": total,
        "healthy": healthy,
        "broken": len(broken_campaigns),
        "broken_count": len(broken_campaigns),
        "health_pct": _pct(healthy, total),
        "broken_campaigns": broken_campaigns,
    }


def download_summary(downloads: Iterable[DownloadLike]) -> dict[str, object]:
    """The 13d download rollup: weekly/cumulative totals + week-over-week delta.

    Keys: ``total_weekly`` / ``total_cumulative`` / ``prev_weekly`` (sums) and
    ``wow_delta_pct`` (signed week-over-week percent change in weekly downloads; 0 with no
    prior). The per-file ranking is sorted by the API off the same rows.
    """
    rows = list(downloads)
    total_weekly = sum(d.weekly_count for d in rows)
    prev_weekly = sum(d.prev_weekly_count for d in rows)
    delta = round(100 * (total_weekly - prev_weekly) / prev_weekly) if prev_weekly > 0 else 0
    return {
        "total_weekly": total_weekly,
        "total_cumulative": sum(d.cumulative_count for d in rows),
        "prev_weekly": prev_weekly,
        "wow_delta_pct": delta,
    }


def conversion_page_rate(page: ConversionPageLike) -> float:
    """One conversion page's form-submission rate (submissions / sessions; 0.0 if none)."""
    return _rate(page.form_submissions, page.sessions)


def key_conversion_pages[P: ConversionPageLike](pages: Iterable[P], *, n: int) -> list[P]:
    """The top ``n`` conversion pages by form-submission RATE (descending).

    ``n`` is read from ``params.website.top_landing_n`` at the edge (INV-11) — the pages
    that most efficiently turn a session into an application.
    """
    ranked = sorted(pages, key=conversion_page_rate, reverse=True)
    return ranked[: max(0, n)]


def funnel_dropoff(funnel: Iterable[FunnelLike]) -> list[dict[str, object]]:
    """The 13e landing→application funnel with per-stage drop-off.

    Returns one row per stage IN ORDER with ``stage`` / ``sessions``, ``of_top_pct`` (share
    of the FIRST stage), and ``drop_from_prev_pct`` (percent lost vs the immediately prior
    stage; 0 for the first). Drives the conversion-path drop-off visual.
    """
    rows = list(funnel)
    if not rows:
        return []
    top = rows[0].sessions
    out: list[dict[str, object]] = []
    prev: int | None = None
    for stage in rows:
        drop = round(100 * (prev - stage.sessions) / prev) if prev is not None and prev > 0 else 0
        out.append(
            {
                "stage": stage.stage,
                "sessions": stage.sessions,
                "of_top_pct": _pct(stage.sessions, top),
                "drop_from_prev_pct": drop,
            }
        )
        prev = stage.sessions
    return out

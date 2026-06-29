"""Simulated (stood-in) AnalyticsAdapter — synthetic, offline, no live GA4 read (INV-9).

The v1 impl of the Module-13 GA4 boundary. There is **no live GA4 credential** in this
portal, so the website-analytics surface reads a deterministic, aggregate, SYNTHETIC
snapshot with ``source_mode="simulated"`` — surfaced honestly, never implied live
(INV-6). There is **no network client** on this class by construction, so "no live read"
is a STRUCTURAL property, provable from the source text alone (no transport to mock).

Determinism: the snapshot is a fixed, representative point-in-time view (no clock, no
PRNG, no GUIDs) — the same call always yields the same aggregates across processes. The
window is accepted for interface parity; the stood-in data is window-independent (a real
GA4 impl would range-query it). Every row is an AGGREGATE — no per-person, per-session,
or child-keyed field exists anywhere (P-4/INV-6). Three of the six tagged campaigns carry
deliberately BROKEN UTMs (a missing campaign, an unallowed ``qr_code`` medium, an
uppercase medium) so the website→CRM-Ops attribution-chain validation has real teeth at
the ORIGIN of the tags.
"""

from __future__ import annotations

from app.adapters.analytics.base import (
    AnalyticsAdapter,
    AnalyticsSnapshot,
    AnalyticsWindow,
    CampaignSource,
    ConversionPage,
    CrossSiteFlow,
    DownloadMetric,
    FunnelStage,
    PageMetric,
    PathFlow,
    SiteMetric,
    SourceMetric,
    SourcePageCell,
)

_GT = "gt.school"
_AW = "anywhere.gt.school"

# (site, sessions, users, new, returning, bounce, avg_dur_s, pageviews)
_SITES: tuple[tuple[str, int, int, int, int, float, float, int], ...] = (
    (_GT, 8420, 6100, 4270, 1830, 0.42, 138.0, 21850),
    (_AW, 3110, 2480, 1990, 490, 0.55, 96.0, 6720),
)

# (path, site, page_type, pageviews, prev_pageviews, unique, time_s, bounce, exit, conv)
_PAGES: tuple[tuple[str, str, str, int, int, int, float, float, float, int], ...] = (
    ("/", _GT, "landing", 5200, 4800, 4100, 64.0, 0.38, 0.30, 120),
    ("/tuition", _GT, "landing", 3100, 2600, 2500, 142.0, 0.34, 0.22, 240),
    ("/how-it-works", _GT, "landing", 2400, 2500, 1900, 175.0, 0.36, 0.25, 95),
    ("/accreditation", _GT, "resource", 1450, 1100, 1200, 150.0, 0.40, 0.33, 60),
    ("/apply", _GT, "form", 1280, 1150, 1050, 210.0, 0.20, 0.18, 330),
    ("/blog/2-hour-learning", _GT, "blog", 1850, 2100, 1600, 220.0, 0.62, 0.55, 18),
    ("/blog/is-my-kid-gifted", _GT, "blog", 1320, 980, 1180, 190.0, 0.58, 0.50, 22),
    ("/summer-camp", _GT, "landing", 1600, 900, 1350, 120.0, 0.45, 0.38, 70),
    ("/esa-guide", _GT, "resource", 980, 720, 820, 240.0, 0.30, 0.28, 140),
    ("/about", _GT, "about", 760, 800, 640, 88.0, 0.55, 0.48, 8),
    ("/", _AW, "landing", 2100, 1700, 1700, 70.0, 0.52, 0.40, 55),
    ("/online-program", _AW, "landing", 1400, 1500, 1150, 130.0, 0.66, 0.58, 30),
    ("/pricing", _AW, "landing", 1180, 980, 980, 120.0, 0.40, 0.30, 90),
    ("/apply", _AW, "form", 540, 480, 470, 200.0, 0.22, 0.20, 130),
    ("/faq", _AW, "resource", 700, 760, 600, 160.0, 0.50, 0.45, 12),
)

# (channel, platform|None, sessions, conversions) — sessions sum to 11530 (= site total).
_SOURCES: tuple[tuple[str, str | None, int, int], ...] = (
    ("organic", None, 4900, 360),
    ("direct", None, 2600, 210),
    ("social", "x", 820, 40),
    ("social", "facebook", 640, 28),
    ("social", "instagram", 720, 36),
    ("email", None, 1100, 150),
    ("referral", None, 750, 55),
)

# (utm_source, utm_medium, utm_campaign, sessions, landing_page) — 3 healthy, 3 BROKEN:
# (4) missing utm_campaign, (5) qr_code medium (unallowed, aliasable→event), (6) uppercase.
_CAMPAIGNS: tuple[tuple[str, str, str, int, str], ...] = (
    ("facebook", "social", "spring_open_house", 420, "/summer-camp"),
    ("newsletter", "email", "june_nurture", 360, "/tuition"),
    ("google", "cpc", "tuition_search", 540, "/tuition"),
    ("instagram", "social", "", 230, "/online-program"),
    ("qr_flyer", "qr_code", "field_event_q2", 180, "/"),
    ("Partner", "Referral", "partner_blast", 90, "/accreditation"),
)

# (channel, page_path, sessions) — the source×page matrix's top cells.
_SOURCE_PAGES: tuple[tuple[str, str, int], ...] = (
    ("organic", "/", 1800),
    ("organic", "/tuition", 1100),
    ("organic", "/blog/2-hour-learning", 900),
    ("organic", "/esa-guide", 480),
    ("direct", "/", 1200),
    ("direct", "/apply", 400),
    ("social", "/summer-camp", 700),
    ("email", "/tuition", 520),
    ("email", "/apply", 280),
    ("referral", "/accreditation", 300),
)

# (file_name, weekly, cumulative, prev_weekly, referring_page, source)
_DOWNLOADS: tuple[tuple[str, int, int, int, str, str], ...] = (
    ("GT-School-Tuition-and-ESA-Guide.pdf", 142, 1880, 120, "/esa-guide", "organic"),
    ("Summer-Camp-2026-Brochure.pdf", 120, 610, 70, "/summer-camp", "social"),
    ("2-Hour-Learning-Whitepaper.pdf", 96, 1240, 110, "/blog/2-hour-learning", "organic"),
    ("Accreditation-FAQ.pdf", 78, 940, 60, "/accreditation", "referral"),
    ("Sample-Daily-Schedule.pdf", 54, 720, 58, "/how-it-works", "direct"),
    ("Parent-Handbook.pdf", 33, 410, 31, "/about", "email"),
)

# (page_path, site, sessions, form_submissions) — key conversion pages.
_CONVERSION_PAGES: tuple[tuple[str, str, int, int], ...] = (
    ("/apply", _GT, 1050, 330),
    ("/apply", _AW, 470, 130),
    ("/tuition", _GT, 2500, 240),
    ("/pricing", _AW, 980, 90),
    ("/summer-camp", _GT, 1350, 70),
)

# (from_page, to_page, sessions) — the homepage→… user flow.
_PATH_FLOWS: tuple[tuple[str, str, int], ...] = (
    ("/", "/tuition", 1400),
    ("/", "/how-it-works", 1100),
    ("/", "/summer-camp", 720),
    ("/tuition", "/apply", 560),
    ("/how-it-works", "/tuition", 480),
    ("/tuition", "/esa-guide", 340),
)

# (from_site, to_site, sessions) — cross-site flow.
_CROSS_SITE: tuple[tuple[str, str, int], ...] = (
    (_GT, _AW, 410),
    (_AW, _GT, 230),
)

# (stage, sessions) — landing→application funnel (monotonically non-increasing).
_FUNNEL: tuple[tuple[str, int], ...] = (
    ("landing", 11530),
    ("program_page", 4200),
    ("tuition_pricing", 2300),
    ("apply_start", 1180),
    ("apply_submit", 460),
)


class SimulatedAnalyticsAdapter(AnalyticsAdapter):
    """Offline synthetic GA4 source (INV-9, stood-in: no live read).

    No network client exists on this class — "no live read" is therefore a structural
    property, not configured behaviour. :meth:`snapshot` returns a fixed, deterministic,
    aggregate snapshot with ``source_mode="simulated"``; nothing is ever fetched from GA4.
    """

    def snapshot(self, window: AnalyticsWindow) -> AnalyticsSnapshot:
        """Return the deterministic stood-in analytics snapshot (window-independent)."""
        return AnalyticsSnapshot(
            source_mode="simulated",
            sites=[
                SiteMetric(
                    site=s,
                    sessions=sess,
                    users=u,
                    new_users=nu,
                    returning_users=ru,
                    bounce_rate=br,
                    avg_session_duration_s=dur,
                    pageviews=pv,
                )
                for (s, sess, u, nu, ru, br, dur, pv) in _SITES
            ],
            pages=[
                PageMetric(
                    page_path=p,
                    site=site,
                    page_type=pt,
                    pageviews=pv,
                    prev_pageviews=prev,
                    unique_visitors=uv,
                    avg_time_on_page_s=t,
                    bounce_rate=b,
                    exit_rate=ex,
                    conversions=conv,
                )
                for (p, site, pt, pv, prev, uv, t, b, ex, conv) in _PAGES
            ],
            sources=[
                SourceMetric(channel=c, platform=plat, sessions=sess, conversions=conv)
                for (c, plat, sess, conv) in _SOURCES
            ],
            campaigns=[
                CampaignSource(
                    utm_source=src,
                    utm_medium=med,
                    utm_campaign=camp,
                    sessions=sess,
                    landing_page=lp,
                )
                for (src, med, camp, sess, lp) in _CAMPAIGNS
            ],
            source_pages=[
                SourcePageCell(channel=c, page_path=p, sessions=sess)
                for (c, p, sess) in _SOURCE_PAGES
            ],
            downloads=[
                DownloadMetric(
                    file_name=fn,
                    weekly_count=wk,
                    cumulative_count=cum,
                    prev_weekly_count=prev,
                    referring_page=ref,
                    source=src,
                )
                for (fn, wk, cum, prev, ref, src) in _DOWNLOADS
            ],
            conversion_pages=[
                ConversionPage(page_path=p, site=site, sessions=sess, form_submissions=fs)
                for (p, site, sess, fs) in _CONVERSION_PAGES
            ],
            path_flows=[
                PathFlow(from_page=f, to_page=t, sessions=sess) for (f, t, sess) in _PATH_FLOWS
            ],
            cross_site_flows=[
                CrossSiteFlow(from_site=f, to_site=t, sessions=sess) for (f, t, sess) in _CROSS_SITE
            ],
            funnel=[FunnelStage(stage=st, sessions=sess) for (st, sess) in _FUNNEL],
        )

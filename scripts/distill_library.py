#!/usr/bin/env python3
"""Distill the scraped GT marketing library into a small, clean, committed seed.

OFFLINE one-shot (Phase-1 marketing). The scraped library (GT's OWN public
marketing — 440 social posts + website pages + engagement insights) is not a
runtime data source: this script reads it ONCE, filters/cleans on the way out,
and writes a deterministic JSON to ``backend/app/data/seeds/brand_library.json``
that the runtime loader (``app.data.library_ingest``) consumes. The scrape ROOT
is NEVER opened at runtime.

What it reads (the ONLY three sources, THREAT_MODEL §5):
  * ``catalog/catalog.csv``      — 440 social posts (platform, caption, engagement, url)
  * ``analysis/INSIGHTS.md``     — the theme × engagement read (direction-setter)
  * ``websites/**/*.md``         — archived website pages + SEO/GEO notes

What it MUST NEVER open (live secrets / media / raw / metadata): any
``*cookies*`` file, ``social/**`` media, ``_raw_html/**``, any ``*.info.json``.
The walk is restricted to the three allowed sources, so those are never touched.

Cleaning on the way out:
  * Dollar reconciliation (INV-11): ``$10,400`` → ``$10,474`` (canonical tuition).
  * V-2 grounding gate (INV-4): a caption carrying a performance multiplier
    ("3x/2x/4X/Nx"), unsupported superlative ("best", "#1", "fastest"), or a
    guaranteed-outcome claim is DROPPED — it never becomes a brand-memory
    exemplar (a banned-claim exemplar would teach the generator blocked copy).
    Reuses the REAL predicate ``app.core.eval_gate.check_v2`` (+ ``check_v3`` for
    minor-safety) — the banned-pattern list is never re-invented here.
  * Incidental PII stripped: phone numbers and street addresses are scrubbed so
    the committed JSON passes ``scripts/pii_scan.py``.

Usage::

    GT_LIBRARY_PATH="~/Desktop/gt school scrap" python scripts/distill_library.py

The ROOT defaults to ``~/Desktop/gt school scrap`` (expanded). If it is
unreadable the script STOPS with a clear error — it never fabricates the seed.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The script lives in <repo>/scripts; the backend package is its sibling.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.eval_gate import RuleVerdict, check_v2, check_v3  # noqa: E402

# --------------------------------------------------------------------------- #
# Output location — a FIXED in-repo constant, never a tunable (INV-11).
# --------------------------------------------------------------------------- #
OUTPUT_PATH = _BACKEND / "app" / "data" / "seeds" / "brand_library.json"

DEFAULT_LIBRARY_PATH = "~/Desktop/gt school scrap"

# --------------------------------------------------------------------------- #
# Theme tagging — mirrors analysis/theme_engagement.py so exemplars group by the
# same INSIGHTS themes. Kept here (not imported) because the scrape's own script
# is NOT a repo module and must not be imported at build time.
# --------------------------------------------------------------------------- #
_THEMES: dict[str, str] = {
    "gifted_identity": r"gifted|talented|\bg&t\b|gt kid|prodigy|high.?iq|genius|bright|advanced learner",
    "acceleration_pace": r"faster|2 ?hour|two hour|accelerat|speed|ahead|grade level|mastery|behind|catch up",
    "anti_busywork": r"busywork|worksheet|bored|boring|one.size|sit still|waste|babysit",
    "ai_platform": r"\bai\b|timeback|adaptive|personaliz|software|app|tech|algorithm|1xl|dash",
    "cost_tefa_esa": r"tefa|esa|tuition|cost|afford|scholarship|voucher|funding|\$|free|price",
    "socialization": r"social|friend|peer|community|tribe|cohort|alone|isolat|club",
    "academic_outcomes": r"sat|act|\bap\b|college|test|score|rank|top 1|results|nwea|map|percentile|outcome",
    "enrollment": r"enroll|apply|admission|tour|open house|location|campus|georgetown|austin|texas|virtual|online|waitlist|start",
    "parent_story": r"my (kid|son|daughter|child)|parent|family|thrive|love|happy|confiden|transform|journey",
}
_THEME_ORDER = list(_THEMES)
_THEME_PATTERNS = {k: re.compile(v, re.I) for k, v in _THEMES.items()}


def _theme_of(caption: str) -> str:
    """First matching theme (stable INSIGHTS order), else ``uncategorized``."""
    for theme in _THEME_ORDER:
        if _THEME_PATTERNS[theme].search(caption):
            return theme
    return "uncategorized"


# --------------------------------------------------------------------------- #
# Cleaning helpers.
# --------------------------------------------------------------------------- #
# Dollar reconciliation (INV-11): the website archive carries an inconsistent
# "$10,400 tuition" alongside the canonical "$10,474" voucher. Normalize all
# "$10,400" (and the bare "10,400") to the single canonical tuition figure.
_DOLLAR_INCONSISTENCY = re.compile(r"\$?10,400")

# Incidental PII: US phone numbers and street addresses in the scraped marketing
# copy (e.g. the Georgetown campus). Scrubbed so the committed JSON is clean.
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+"
    r"(?:Rd|Road|St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court)\b"
    r"(?:,?\s+[A-Z][A-Za-z]+,?\s+[A-Z]{2}\s+\d{5})?",
    re.IGNORECASE,
)


def _reconcile_dollars(text: str) -> str:
    """Replace the inconsistent ``$10,400`` tuition phrasing with ``$10,474`` (INV-11)."""
    return _DOLLAR_INCONSISTENCY.sub("$10,474", text)


def _strip_pii(text: str) -> str:
    """Scrub incidental PII (phone, street address) from scraped copy."""
    text = _PHONE_RE.sub("[redacted]", text)
    text = _STREET_RE.sub("[redacted]", text)
    return text


def _clean(text: str) -> str:
    """Full clean: collapse whitespace, reconcile dollars, strip incidental PII."""
    text = " ".join(text.split())
    text = _reconcile_dollars(text)
    text = _strip_pii(text)
    return text.strip()


# A scraped page body leads with navigation menus + an application/form-field
# block before the real marketing prose. These line patterns are page CHROME, not
# reusable copy — dropped so the library body is the actual content, not the
# header/nav/form scaffolding. Conservative: only clearly-chrome lines are removed.
_CHROME_LINE_RE = re.compile(
    r"^\s*[#*\-]*\s*(navigation|menu|header|footer)\b"  # nav/menu/header/footer headings
    r"|^\s*-?\s*menu:\s"  # inline "Menu: …" rows
    r"|begin application"  # the application CTA
    r"|^\s*[#*\-]*\s*secure your candidacy"  # the application section header
    r"|^\s*[#*\-]*\s*application form\b"  # "Application Form" heading
    r"|application form fields|^\s*-?\s*form fields\b"  # form-field block intro
    r"|income range option"  # income dropdown intro
    r"|sms consent|sms message|i agree to receive|^\s*consent:"  # consent statements
    r"|^\s*-?\s*sign[ -]?in\b"  # "Sign in link for existing applicants"
    r"|^\s*-?\s*(first name|last name|email|phone|zip code|household income"
    r"|prefer not to say)\b"  # individual form-field labels
    r"|^\s*-?\s*(under \$|over \$|\$65,000|\$160,000)",  # income option rows
    re.IGNORECASE,
)

# Bare top-nav menu item rows (a list item that is ONLY a known nav label).
_NAV_LABELS = {
    "how gt works",
    "our advisors",
    "academics",
    "intensives",
    "academic calendar",
    "calendar",
    "tuition & tefa",
    "tuition and tefa",
    "faq",
    "register now",
    "tracks",
    "cities",
    "schedule",
    "pricing",
    "tefa approved school",
}


def _strip_page_chrome(text: str) -> str:
    """Drop nav/header/application boilerplate lines from a scraped page body.

    Returns the prose with chrome lines removed. Falls back to the original text
    if stripping would leave nothing (so a page that is all-chrome still yields a
    body rather than an empty one).
    """
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Skip pure horizontal-rule / separator lines (---, ***, ===).
        if line.strip("-*_= ") == "":
            continue
        if _CHROME_LINE_RE.search(line):
            continue
        # A bare nav-label list item (e.g. "- Our Advisors") is menu chrome.
        bare = line.lstrip("-*# ").strip().lower()
        if bare in _NAV_LABELS:
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    return result or text


# A minimal record so we can call the REAL gate predicate (not a re-invented
# banned-pattern list). The gate reads `.copy_text` and `.claims` structurally
# (the `GatedRecord` Protocol) — no schema import needed.
@dataclass
class _GateProbe:
    copy_text: str
    claims: tuple[str, ...] = ()


def _passes_grounding(text: str) -> bool:
    """True iff the cleaned text passes V-2 grounding AND V-3 minor-safety.

    Reuses the canonical ``app.core.eval_gate`` predicates so the distill DROPS
    exactly what the runtime gate would block — a banned-claim exemplar must
    never condition generation (INV-4).
    """
    probe = _GateProbe(copy_text=text)
    if check_v2(probe) is not RuleVerdict.PASS:
        return False
    return check_v3(probe) is RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# Source readers.
# --------------------------------------------------------------------------- #
# Engagement field per platform: X / YouTube carry `views_plays`; IG / FB /
# TikTok carry `likes`. The loader normalizes WITHIN platform (caps from params).
_VIEWS_PLATFORMS = {"x/twitter", "youtube"}


def _platform_key(platform: str) -> str:
    """Normalize the catalog platform label to a stable lowercase key."""
    return platform.strip().lower()


def _engagement(row: dict[str, str]) -> tuple[str, int]:
    """The (signal_kind, raw_count) for a row, by platform (views vs likes)."""
    plat = _platform_key(row.get("platform", ""))

    def _num(value: str | None) -> int:
        try:
            return int(float(value)) if value else 0
        except ValueError:
            return 0

    if plat in _VIEWS_PLATFORMS:
        return "views", _num(row.get("views_plays"))
    return "likes", _num(row.get("likes"))


def _read_catalog(root: Path) -> list[dict[str, object]]:
    """Read the social-post catalog into cleaned, gate-passing exemplar records.

    Each kept record: platform, theme, cleaned caption, engagement (kind+raw),
    and the post url (the exemplar's `source_ref`). Captions failing V-2/V-3 are
    DROPPED. Sorted deterministically; the loader caps per theme.
    """
    csv_path = root / "catalog" / "catalog.csv"
    records: list[dict[str, object]] = []
    with csv_path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_caption = (row.get("caption") or "").strip()
            if not raw_caption:
                continue
            caption = _clean(raw_caption)
            if not caption:
                continue
            if not _passes_grounding(caption):
                continue
            kind, raw = _engagement(row)
            records.append(
                {
                    "platform": _platform_key(row.get("platform", "")),
                    "theme": _theme_of(caption),
                    "caption": caption,
                    "engagement_kind": kind,
                    "engagement_raw": raw,
                    "url": (row.get("url") or "").strip(),
                }
            )
    # Deterministic order: theme, then engagement desc, then url, then caption.
    records.sort(
        key=lambda r: (
            str(r["theme"]),
            -int(r["engagement_raw"]),  # type: ignore[arg-type]
            str(r["url"]),
            str(r["caption"]),
        )
    )
    return records


# A "## SEO / GEO notes" section's target-keyword line and content-repurposing
# hooks are the GEO direction. We keep the page title + target-keyword summary.
_TITLE_TAG_RE = re.compile(r"^-\s*\*\*Title tag:\*\*\s*(.+)$", re.IGNORECASE)
_KEYWORDS_RE = re.compile(r"^-\s*\*\*Target keywords / topics:\*\*\s*(.+)$", re.IGNORECASE)
_SOURCE_URL_RE = re.compile(r"^source_url:\s*(.+)$", re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+(.+)$")


def _read_websites(root: Path) -> list[dict[str, object]]:
    """Read archived website pages → cleaned page records (title, url, summary).

    Reads ONLY ``websites/**/*.md``. For each page we capture the front-matter
    ``source_url``, the page title, and the SEO/GEO target-keyword summary (the
    GEO direction). Body text is cleaned (dollars reconciled, PII stripped) and
    truncated to a compact searchable summary. Deterministic file order.
    """
    websites = root / "websites"
    pages: list[dict[str, object]] = []
    for md_path in sorted(websites.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        # Strip the YAML front-matter block (between the leading `---` fences).
        body_after_frontmatter = text
        if text.lstrip().startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                body_after_frontmatter = parts[2]
        # Prefer the actual "## Page content" section as the searchable body.
        lower = body_after_frontmatter.lower()
        marker = lower.find("## page content")
        page_body = (
            body_after_frontmatter[marker + len("## page content") :]
            if marker != -1
            else body_after_frontmatter
        )
        source_url = ""
        title = md_path.stem
        keywords = ""
        for line in text.splitlines():
            if not source_url:
                m = _SOURCE_URL_RE.match(line.strip())
                if m:
                    source_url = m.group(1).strip()
            tt = _TITLE_TAG_RE.match(line.strip())
            if tt:
                title = _clean(tt.group(1))
            kw = _KEYWORDS_RE.match(line.strip())
            if kw:
                keywords = _clean(kw.group(1))
            if title == md_path.stem:
                h1 = _H1_RE.match(line.strip())
                if h1:
                    title = _clean(h1.group(1))
        # A compact, cleaned body summary for search/library (not the full page).
        # Strip nav/header/application chrome first so the body is reusable prose.
        body_summary = _clean(_strip_page_chrome(page_body))[:600]
        rel = md_path.relative_to(websites).as_posix()
        pages.append(
            {
                "rel_path": rel,
                "source_url": source_url or f"https://{rel}",
                "title": title or rel,
                "keywords": keywords,
                "body_summary": body_summary,
            }
        )
    return pages


def _read_insights(root: Path) -> dict[str, object]:
    """Read the INSIGHTS direction-setter — the ranked-theme order (top themes).

    We keep the theme ranking line-up so the loader can prefer the themes
    INSIGHTS flagged as strongest. Cleaned + dollar-reconciled.
    """
    path = root / "analysis" / "INSIGHTS.md"
    text = _clean(path.read_text(encoding="utf-8"))
    # The strongest hooks, in the order INSIGHTS argues for them (§5 direction).
    ranked = [
        "gifted_identity",
        "parent_story",
        "ai_platform",
        "academic_outcomes",
        "enrollment",
        "acceleration_pace",
        "cost_tefa_esa",
        "anti_busywork",
        "socialization",
    ]
    return {"ranked_themes": ranked, "note": text[:400]}


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #
def _resolve_root() -> Path:
    raw = os.environ.get("GT_LIBRARY_PATH", DEFAULT_LIBRARY_PATH)
    return Path(raw).expanduser()


def distill(root: Path) -> dict[str, object]:
    """Produce the full distilled seed payload from the scrape ROOT."""
    catalog = _read_catalog(root)
    websites = _read_websites(root)
    insights = _read_insights(root)
    return {
        "schema_version": 1,
        "note": (
            "Distilled offline from GT's OWN public marketing scrape "
            "(scripts/distill_library.py). V-2/V-3-filtered; tuition figure "
            "reconciled to the canonical $10,474; incidental PII stripped. "
            "NOT a runtime path-read."
        ),
        "insights": insights,
        "exemplars": catalog,
        "website_pages": websites,
    }


def main() -> int:
    root = _resolve_root()
    required = [
        root / "catalog" / "catalog.csv",
        root / "analysis" / "INSIGHTS.md",
        root / "websites",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        sys.stderr.write(
            "ERROR: scrape library not readable at "
            f"GT_LIBRARY_PATH={root!r}. Missing: {missing}. "
            "Set GT_LIBRARY_PATH and re-run; the seed is NEVER fabricated.\n"
        )
        return 1

    payload = distill(root)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic, byte-stable: sorted keys, fixed indent, trailing newline.
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_ex = len(payload["exemplars"])  # type: ignore[arg-type]
    n_pages = len(payload["website_pages"])  # type: ignore[arg-type]
    sys.stdout.write(
        f"distilled {n_ex} exemplars + {n_pages} website pages → {OUTPUT_PATH}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

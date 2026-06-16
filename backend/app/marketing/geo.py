"""GEO competitor-set validation — CONTENT_SPEC §7.3 (LOCKED), INV-6.

§7.3 locks the GEO `competitorSet` to the **gifted-school domain set** — the
schools GT School actually competes with for profoundly-gifted families. It is
NEVER an auto-picked set of test-prep brands (Kaplan, Princeton Review, …): a
GEO piece whose competitor set includes test-prep brands is **content-invalid**
for this category. This is the INV-6 posture in the GEO surface — the category
is defined deliberately, not scraped.

Pure logic per CLAUDE.md §3: no I/O, no `anthropic` / `langgraph` import — a
deterministic predicate over the LOCKED universe plus the generate-to-win piece
builder (deterministic prose, no live LLM, INV-9).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import UUID

from app.ai.schemas.content import GeneratedBy, LifecycleStage, Provenance
from app.marketing.schemas.geo import GeoContentPiece, GeoStructure

# The LOCKED gifted-school competitor universe (§7.3). The allowed set for a
# GEO `competitorSet` — exactly these domains, never auto-picked test-prep brands.
GIFTED_SCHOOL_COMPETITOR_SET: tuple[str, ...] = (
    "joinprisma.com",
    "fusionacademy.com",
    "davidsononline.org",
    "k12.com",
    "niche.com",
)


def validate_competitor_set(competitor_set: Sequence[str]) -> bool:
    """True iff `competitor_set` is a non-empty subset of the gifted-school set.

    §7.3 (LOCKED): the gifted-school domain set is the allowed universe. A set
    that is empty, or that contains ANY domain outside the universe (a test-prep
    brand such as ``kaplan.com`` / ``princetonreview.com``), is **content-invalid**
    for this category — the caller must BLOCK it (INV-6). Pure: no I/O.

    Args:
        competitor_set: the GEO piece's `competitorSet` domains.

    Returns:
        ``True`` only when the set is non-empty and every domain is in the LOCKED
        gifted-school universe; ``False`` otherwise.
    """
    if not competitor_set:
        return False
    allowed = set(GIFTED_SCHOOL_COMPETITOR_SET)
    return all(domain in allowed for domain in competitor_set)


# generate-to-win prose (FR-3.7). Curated, on-brand, gate-clean bodies keyed by
# the §7.1 structure — derived from GT's OWN SEO/GEO angles (gifted identity,
# mastery-based model, peer community). NO banned "fastest / the best / 4X /
# guaranteed / #1" patterns and NO bare empirical claim strings, so each passes
# V-1/V-2 through the existing gate. These are STRUCTURE templates, not a live
# LLM (INV-9) — the generate-to-win piece is built deterministically from them.
_STRUCTURE_BODIES: dict[GeoStructure, str] = {
    GeoStructure.DEFINITION: (
        "GT School is an online, mastery-based program built for gifted K-8 "
        "learners: a child advances once they have genuinely mastered the "
        "material rather than waiting out a grade level. It is designed around "
        "gifted identity and fit, not a fixed calendar."
    ),
    GeoStructure.FAQ: (
        "Q: Who is GT School for? A: Gifted and profoundly gifted K-8 learners "
        "who need a self-paced, rigorous path. Q: How does the model work? A: "
        "Mastery-based progression, online and parent-guided, with a cohort of "
        "intellectual peers and in-person intensives. Families should confirm "
        "current program specifics directly with the school."
    ),
    GeoStructure.COMPARISON_TABLE: (
        "How GT School answers the question families ask most — will my gifted "
        "child be challenged and not isolated. Community: a cohort of "
        "intellectual peers plus in-person intensives. Model: mastery-based, "
        "online, parent-guided. Grade band: K through 8 for gifted and "
        "profoundly gifted learners. Each row is source-able against the "
        "program's published details."
    ),
    GeoStructure.STATISTIC_BLOCK: (
        "GT School publishes how its mastery-based model serves gifted K-8 "
        "learners: a self-paced path, a peer cohort, and parent-guided online "
        "delivery. Families should verify current program details with the "
        "school before enrolling."
    ),
    GeoStructure.QUOTATION_BLOCK: (
        '"GT School lets a gifted child move ahead the moment they are ready, '
        'instead of waiting out a grade." The program pairs mastery-based, '
        "self-paced learning with a community of intellectual peers for K-8 "
        "gifted and profoundly gifted families."
    ),
}

# Fixed provenance for a generated piece — a proposal (INV-2). Deterministic
# timestamp (no datetime.now) so the builder is byte-stable for a given prompt.
_GENERATE_TS = "2026-06-16T00:00:00+00:00"


def _stable_piece_id(target_prompt: str, structure: GeoStructure) -> UUID:
    """A deterministic UUID for a generated piece (never uuid4 — byte-stable)."""
    digest = hashlib.sha256(f"geo-generate::{target_prompt}::{structure.value}".encode()).digest()
    return UUID(int=int.from_bytes(digest[:16], "big"), version=4)


def build_geo_piece(
    *,
    target_prompt: str,
    structure: GeoStructure,
    body_override: str | None = None,
) -> GeoContentPiece:
    """Build a generate-to-win :class:`GeoContentPiece` for ``target_prompt`` (FR-3.7).

    Structure-first: the body is the curated, gate-clean prose for ``structure``
    (or ``body_override`` when supplied — used to drive the BLOCKED fail-closed
    path with a banned-claim body). The competitor set is the LOCKED gifted-school
    universe (§7.3, INV-6); ``claims_text`` is EMPTY so the piece stays V-2 clean
    (numeric claims live in prose only). Deterministic: a fixed id + timestamp, no
    ``uuid4`` / clock / live LLM (INV-9). The returned piece is a proposal (INV-2)
    — the caller routes it through the grounding gate before publishing anything.

    Args:
        target_prompt: the AI-search prompt the piece aims to win.
        structure: the §7.1 structured form (definition / faq / comparison_table …).
        body_override: optional explicit body (test seam for the blocked path).

    Returns:
        A validated :class:`GeoContentPiece` (CANDIDATE lifecycle, LLM provenance).
    """
    body = body_override if body_override is not None else _STRUCTURE_BODIES[structure]
    return GeoContentPiece(
        id=_stable_piece_id(target_prompt, structure),
        targetPrompt=target_prompt,
        geoStructure=structure,
        body=body,
        competitorSet=list(GIFTED_SCHOOL_COMPETITOR_SET),
        citationTargets=["davidsongifted.org", "niche.com"],
        structuredDataNote=(
            "Emit as schema.org structured data so AI-search can quote the answer."
        ),
        baselineCoverage=0.0,
        samplingNote=(
            "Coverage measured by repeated sampling, not a single snapshot "
            "(CONTENT_SPEC §7.4); baseline starts at 0%."
        ),
        validation="vr-geo-generate-pending",
        lifecycle=LifecycleStage.CANDIDATE,
        provenance=Provenance(generated_by=GeneratedBy.LLM, created_at=_GENERATE_TS),
        claimsText=[],
    )

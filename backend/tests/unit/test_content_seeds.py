"""Marketing seed-inventory tests (S4 prep; CONTENT_SPEC §11, NFR-1, INV-1, INV-7).

`app.data.synthetic` is the **only seed writer** (NFR-1). On top of the family
spine it also produces the *marketing* seed inventory (§11) — brand memory, brand
rules, recipes, a content batch, and library assets — so the S4 content engine,
the brand judge, and BOTH §9 BLOCK paths (V-2 grounding, V-3 COPPA) are demoable
on synthetic data alone.

These tests pin the §11 minimums (counts + kinds), the INV-7 Tom-Babb attribution
on every recipe, the demo BLOCK coverage (a "Nx speed" V-2 failure and a
minor-targeting V-3 failure present in the batch), and determinism (every
generator is byte-reproducible).
"""

from __future__ import annotations

import re

from app.ai.schemas.brand import (
    BrandMemoryItem,
    BrandMemoryKind,
    BrandMemorySignal,
    BrandRule,
    LibraryAsset,
    LibraryAssetType,
    MarketingRecipe,
    RuleType,
)
from app.ai.schemas.content import (
    ContentCandidate,
    GeneratedBy,
    LifecycleStage,
)
from app.data.synthetic import (
    generate_brand_memory,
    generate_brand_rules,
    generate_content_batch,
    generate_content_pipeline,
    generate_creator_records,
    generate_geo_content_pieces,
    generate_library_assets,
    generate_recipes,
    generate_sentiment_records,
)
from app.marketing.geo import validate_competitor_set
from app.marketing.schemas.artifacts import (
    ArtifactStatus,
    ConceptArtifact,
    ImageArtifact,
    Stage,
    VideoArtifact,
)
from app.marketing.schemas.discovery import (
    AudienceSegment,
    CreatorDataMode,
    CreatorRecord,
    Sentiment,
    SentimentRecord,
    SentimentSourceMode,
)
from app.marketing.schemas.geo import GeoContentPiece

# The §9 V-2 banned-pattern (performance multipliers like "4X speed") and the
# §9 V-3 minor-targeting signal ("Hey kids, ..."), mirrored from
# `app.core.eval_gate` so a candidate's BLOCK demo-ability can be asserted by
# scanning `copy_text` WITHOUT importing the eval stack into synthetic.py.
_V2_NX_SPEED = re.compile(r"\b\d+\s*x\b", re.IGNORECASE)
_V3_MINOR_SIGNAL = re.compile(
    r"\bhey\s+kids\b|\b(?:[0-9]|1[0-7])[\s-]*year[\s-]*old\b|\bages?\s+\d", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# §11.1 Brand memory.
# --------------------------------------------------------------------------- #
def test_brand_memory_counts_and_kinds() -> None:
    """≥8 items with the §11.1 kind distribution and the two named dont-rules present."""
    items = generate_brand_memory()
    assert len(items) >= 8
    assert all(isinstance(i, BrandMemoryItem) for i in items)

    by_kind: dict[BrandMemoryKind, list[BrandMemoryItem]] = {}
    for item in items:
        by_kind.setdefault(item.kind, []).append(item)

    assert len(by_kind.get(BrandMemoryKind.VOICE_ATTRIBUTE, [])) >= 3
    assert len(by_kind.get(BrandMemoryKind.EXEMPLAR, [])) >= 3
    dont_or_signal = by_kind.get(BrandMemoryKind.DONT_RULE, []) + by_kind.get(
        BrandMemoryKind.SIGNAL, []
    )
    assert len(dont_or_signal) >= 2

    # Every item is active, versioned, weighted, synthetic-seed provenance (§11.1).
    for item in items:
        assert item.active is True
        assert item.version >= 1
        assert isinstance(item.weight, float)
        assert item.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED

    # The two named dont-rules MUST be present — they make V-4/gate enforcement
    # demonstrable: "Don't use speed multipliers" and "Don't target children".
    blob = " ".join(i.content.lower() for i in dont_or_signal)
    assert "speed multiplier" in blob
    assert "target children" in blob or "target minors" in blob

    # The speed-multiplier dont-rule carries the discarded learning signal (§11.1).
    speed_items = [i for i in dont_or_signal if "speed multiplier" in i.content.lower()]
    assert any(i.signal is BrandMemorySignal.DISCARDED for i in speed_items)


# --------------------------------------------------------------------------- #
# §11.2 Brand rules.
# --------------------------------------------------------------------------- #
def test_brand_rules_counts_and_types() -> None:
    """≥4 rules including the two `never` rules (unverifiable claims + minors)."""
    rules = generate_brand_rules()
    assert len(rules) >= 4
    assert all(isinstance(r, BrandRule) for r in rules)

    nevers = [r for r in rules if r.rule_type is RuleType.NEVER]
    assert len(nevers) >= 2

    statements = " ".join(r.statement.lower() for r in rules)
    assert "unverifiable" in statements or "performance claim" in statements
    assert "minor" in statements
    assert "test-prep" in statements or "test prep" in statements

    for rule in rules:
        assert rule.active is True
        assert rule.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


# --------------------------------------------------------------------------- #
# §11.3 Recipes — INV-7 Tom Babb attribution on EVERY recipe.
# --------------------------------------------------------------------------- #
def test_recipes_counts_and_tom_babb_attribution() -> None:
    """≥3 recipes; INV-7: every recipe.attribution is non-empty and names Tom Babb."""
    recipes = generate_recipes()
    assert len(recipes) >= 3
    assert all(isinstance(r, MarketingRecipe) for r in recipes)

    names = {r.name.lower() for r in recipes}
    assert any("geo faq" in n for n in names)
    assert any("nurture" in n for n in names)
    assert any("comparison" in n for n in names)

    for recipe in recipes:
        # INV-7 (LOCKED): attribution is non-empty and names Tom Babb.
        assert recipe.attribution.strip()
        assert "tom babb" in recipe.attribution.lower()
        assert recipe.prompt_template.strip()
        assert recipe.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


# --------------------------------------------------------------------------- #
# §11.4 Content batch — keep/discard demo + both BLOCK paths.
# --------------------------------------------------------------------------- #
def test_content_batch_counts_and_block_coverage() -> None:
    """≥6 candidates in one batch; ≥1 V-2 (Nx speed) and ≥1 V-3 (minor) BLOCK demo."""
    batch = generate_content_batch()
    assert len(batch) >= 6
    assert all(isinstance(c, ContentCandidate) for c in batch)

    # One generation batch groups the run (§11.4 "≥1 generation batchId").
    assert len({c.batch_id for c in batch}) == 1

    # Demo BLOCK coverage: at least one candidate trips V-2 (banned "Nx speed")
    # and at least one trips V-3 (minor-targeting signal) — assert by scanning
    # copy_text against the gate's own patterns.
    v2_blocks = [c for c in batch if _V2_NX_SPEED.search(c.copy_text)]
    v3_blocks = [c for c in batch if _V3_MINOR_SIGNAL.search(c.copy_text)]
    assert v2_blocks, "expected ≥1 candidate that FAILS V-2 (contains 'Nx speed')"
    assert v3_blocks, "expected ≥1 candidate that FAILS V-3 (minor-targeting signal)"

    # The "4X speed" example specifically (§11.4) is present.
    assert any("4x speed" in c.copy_text.lower() for c in batch)

    for cand in batch:
        assert cand.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


def test_content_batch_block_candidates_actually_block() -> None:
    """The V-2/V-3 demo candidates BLOCK through the real eval gate (test-only import)."""
    from app.core.eval_gate import RuleVerdict, check_v2, check_v3

    batch = generate_content_batch()

    # Duck-typed proposal shim: the gate consumes `.body` + `.claims[*].text/.source_ref`.
    class _Claim:
        def __init__(self, text: str) -> None:
            self.text = text
            self.source_ref = None

    class _Proposal:
        def __init__(self, body: str, claims: list[str]) -> None:
            self.body = body
            self.claims = [_Claim(t) for t in claims]

    v2_blocked = any(
        check_v2(_Proposal(c.copy_text, list(c.claims))) is RuleVerdict.FAIL for c in batch
    )
    v3_blocked = any(check_v3(_Proposal(c.copy_text, [])) is RuleVerdict.FAIL for c in batch)
    assert v2_blocked, "a candidate must actually BLOCK on V-2 through the gate"
    assert v3_blocked, "a candidate must actually BLOCK on V-3 through the gate"


# --------------------------------------------------------------------------- #
# §11.4 Library assets.
# --------------------------------------------------------------------------- #
def test_library_assets_counts_and_types() -> None:
    """≥4 kept+validated assets across copy/faq/comparison_table (§11.4)."""
    assets = generate_library_assets()
    assert len(assets) >= 4
    assert all(isinstance(a, LibraryAsset) for a in assets)

    types = {a.asset_type for a in assets}
    assert LibraryAssetType.COPY in types
    assert LibraryAssetType.FAQ_BLOCK in types
    assert LibraryAssetType.COMPARISON_TABLE in types

    for asset in assets:
        assert asset.lifecycle is LifecycleStage.KEPT
        assert asset.validation.strip()  # only validated content enters the library
        assert asset.tags
        assert asset.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


# --------------------------------------------------------------------------- #
# §11.5 GEO content pieces — enables S5 (real ICP prompts, gifted-school set,
# 0% baseline, samplingNote). These are VALID seeds (good GEO content), not the
# BLOCK demos — the block demos live in the §11.4 content batch.
# --------------------------------------------------------------------------- #
def test_geo_content_pieces_seed_inventory() -> None:
    """≥3 GeoContentPieces on real ICP prompts (CONTENT_SPEC §11.5, INV-1/INV-6)."""
    pieces = generate_geo_content_pieces()
    assert len(pieces) >= 3
    assert all(isinstance(p, GeoContentPiece) for p in pieces)

    # Ids are unique across the inventory.
    assert len({p.id for p in pieces}) == len(pieces)

    for piece in pieces:
        # Real ICP prompt — non-empty target_prompt (schema enforces min_length=1).
        assert piece.target_prompt.strip()
        # §7.3 / INV-6: the LOCKED gifted-school competitor universe, nothing else.
        assert validate_competitor_set(piece.competitor_set) is True
        # §7.1: every piece starts at the 0% baseline.
        assert piece.baseline_coverage == 0.0
        # §7.4 / §11.5: a repeated-sampling note is present and non-empty.
        assert piece.sampling_note is not None
        assert piece.sampling_note.strip()
        # A structured body and a pre-validated validation ref (schema min_length=1).
        assert piece.body.strip()
        assert piece.validation.strip()
        # Synthetic-seed provenance throughout (INV-1).
        assert piece.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


def test_geo_content_pieces_pass_grounding_gate() -> None:
    """Each seed is VALID GEO content — passes V-1/V-2 through the real gate."""
    from app.core.eval_gate import RuleVerdict, check_v1, check_v2

    pieces = generate_geo_content_pieces()
    for piece in pieces:
        assert check_v1(piece) is RuleVerdict.PASS
        assert check_v2(piece) is RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# Determinism — every generator is byte-reproducible.
# --------------------------------------------------------------------------- #
def test_generators_are_deterministic() -> None:
    """Calling each generator twice yields identical output (CLAUDE.md §4.1)."""
    assert [i.model_dump() for i in generate_brand_memory()] == [
        i.model_dump() for i in generate_brand_memory()
    ]
    assert [r.model_dump() for r in generate_brand_rules()] == [
        r.model_dump() for r in generate_brand_rules()
    ]
    assert [r.model_dump() for r in generate_recipes()] == [
        r.model_dump() for r in generate_recipes()
    ]
    assert [c.model_dump() for c in generate_content_batch()] == [
        c.model_dump() for c in generate_content_batch()
    ]
    assert [a.model_dump() for a in generate_library_assets()] == [
        a.model_dump() for a in generate_library_assets()
    ]
    assert [c.model_dump() for c in generate_creator_records()] == [
        c.model_dump() for c in generate_creator_records()
    ]
    assert [s.model_dump() for s in generate_sentiment_records()] == [
        s.model_dump() for s in generate_sentiment_records()
    ]
    assert [a.model_dump() for a in generate_content_pipeline()] == [
        a.model_dump() for a in generate_content_pipeline()
    ]


# --------------------------------------------------------------------------- #
# §8.1 Creator records — AGGREGATE/SYNTHETIC, adults only, never minors (INV-6).
# --------------------------------------------------------------------------- #
def test_creator_records_seed_inventory() -> None:
    """≥5 synthetic CreatorRecords: adults only, dataMode=synthetic, isMinor=False."""
    creators = generate_creator_records()
    assert len(creators) >= 5
    assert all(isinstance(c, CreatorRecord) for c in creators)

    # Ids are unique across the inventory.
    assert len({c.id for c in creators}) == len(creators)

    adult_segments = {
        AudienceSegment.PARENTS,
        AudienceSegment.EDUCATORS,
        AudienceSegment.GENERAL,
    }
    for creator in creators:
        # INV-6 / §9 V-3: never a minor, never a live scrape, adults only.
        assert creator.is_minor is False
        assert creator.data_mode is CreatorDataMode.SYNTHETIC
        assert creator.audience_segment in adult_segments
        # Scores are plausible 0–1 (schema enforces the range; assert non-degenerate).
        assert 0.0 <= creator.fit_score <= 1.0
        assert 0.0 <= creator.authenticity_score <= 1.0
        # Synthetic, obviously-fake handle — no real/minor handle (INV-1).
        assert creator.display_handle.startswith("@")
        assert creator.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED


# --------------------------------------------------------------------------- #
# §8.2 Sentiment records — PLACEHOLDER, mixed polarity, synthetic excerpts.
# --------------------------------------------------------------------------- #
def test_sentiment_records_seed_inventory() -> None:
    """≥6 placeholder SentimentRecords spanning all three polarities (INV-1)."""
    records = generate_sentiment_records()
    assert len(records) >= 6
    assert all(isinstance(s, SentimentRecord) for s in records)

    # Ids are unique across the inventory.
    assert len({s.id for s in records}) == len(records)

    for record in records:
        # OUT-5: placeholder source mode only — never a live feed.
        assert record.source_mode is SentimentSourceMode.PLACEHOLDER
        assert record.topic.strip()
        # Fixed ISO observed_at (no wall clock).
        assert record.observed_at.strip()
        if record.score is not None:
            assert -1.0 <= record.score <= 1.0
        assert record.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED

    # Mixed polarity: positive, neutral, AND negative are all represented.
    sentiments = {s.sentiment for s in records}
    assert Sentiment.POSITIVE in sentiments
    assert Sentiment.NEUTRAL in sentiments
    assert Sentiment.NEGATIVE in sentiments


# --------------------------------------------------------------------------- #
# §4 Staged pipeline — one concept→image→video chain sharing a pipeline_id.
# --------------------------------------------------------------------------- #
def test_content_pipeline_seed_inventory() -> None:
    """≥1 full concept→image→video pipeline; image/video are PLACEHOLDER (OUT-1)."""
    pipeline = generate_content_pipeline()
    assert len(pipeline) == 3

    concept, image, video = pipeline
    assert isinstance(concept, ConceptArtifact)
    assert isinstance(image, ImageArtifact)
    assert isinstance(video, VideoArtifact)

    # All three share ONE pipeline_id (concept→image→video for one piece).
    assert concept.pipeline_id == image.pipeline_id == video.pipeline_id

    # Stages are correctly typed.
    assert concept.stage is Stage.CONCEPT
    assert image.stage is Stage.IMAGE
    assert video.stage is Stage.VIDEO

    # Concept is REAL in v1: has concept/copy/validation, status not placeholder.
    assert concept.status is not ArtifactStatus.PLACEHOLDER
    assert concept.concept.strip()
    assert concept.copy_text.strip()
    assert concept.validation.strip()

    # Image + video are PLACEHOLDER (OUT-1): placeholder status + non-empty uri +
    # a STRING cost_estimate_ref (never a numeric price, INV-11).
    for artifact in (image, video):
        assert artifact.status is ArtifactStatus.PLACEHOLDER
        assert artifact.placeholder_uri.strip()
        assert isinstance(artifact.cost_estimate_ref, str)
        assert artifact.cost_estimate_ref.strip()
        assert artifact.live_asset_uri is None

    # The ref chain holds: image→concept, video→image.
    assert image.concept_ref == concept.id
    assert video.image_ref == image.id

    # Ids are distinct across the three stages.
    assert len({concept.id, image.id, video.id}) == 3

    # Synthetic-seed provenance throughout (INV-1).
    for artifact in pipeline:
        assert artifact.provenance.generated_by is GeneratedBy.SYNTHETIC_SEED

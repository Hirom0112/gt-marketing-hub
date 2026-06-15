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
    generate_library_assets,
    generate_recipes,
)

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
    from app.core.eval_gate import check_v2, check_v3
    from app.core.eval_gate import RuleVerdict

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

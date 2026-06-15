"""Brand operating system schemas — memory, rules, recipes, library (CONTENT_SPEC §5/§8).

The brand operating system is **content rules as data** (§8.4), enforced by the
validation pipeline (§9). Brand memory (§8.3) is *memory*, not just storage: kept
items persist across sessions AND condition the next generation batch (FR-3.2).
Recipes (§8.5) model Tom Babb's open AI-marketing skills as runnable templates.

INV-7 (CLAUDE.md §1) / §8.5 ATTRIBUTION (LOCKED): every :class:`MarketingRecipe`
carries a non-empty `attribution` field naming Tom Babb — his marketing skills are
ATTRIBUTED, never claimed as the builder's authorship. `attribution` is [req] with
`min_length=1`, so a blank/missing value RAISES `pydantic.ValidationError`.
Stripping or claiming authorship is not representable.

§9.2 Rule V-1: every [req] field is enforced, every enum is CLOSED (out-of-range
RAISES), unknown extras are rejected (`extra="forbid"`) — the records fail closed.

Pure data per CLAUDE.md §3: no `anthropic` / `langgraph` / I/O imports — guarded
by `test_core_purity`. Reuses the shared §2 enums/groups from `content.py`. The
pass/fail gate (`ValidationResult`, §9.6) lives in `app/core/eval_gate.py` (a
separate agent, ASSUMPTIONS A-10); these schemas reference it by id string.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.content import (
    Channel,
    ContentFormat,
    LifecycleStage,
    Provenance,
)

# ---------------------------------------------------------------------------
# §8.3–§8.5 + §5 closed enums (string-valued, matching the StrEnum convention).
# ---------------------------------------------------------------------------


class BrandMemoryKind(StrEnum):
    """`BrandMemoryItem.kind` enum (§8.3, LOCKED) — what kind of memory it is."""

    VOICE_ATTRIBUTE = "voice_attribute"
    EXEMPLAR = "exemplar"
    DO_RULE = "do_rule"
    DONT_RULE = "dont_rule"
    SIGNAL = "signal"


class BrandMemorySignal(StrEnum):
    """`BrandMemoryItem.signal` enum (§8.3.2) — the kept/discarded learning signal."""

    KEPT = "kept"
    DISCARDED = "discarded"
    EDITED = "edited"


class RuleType(StrEnum):
    """`BrandRule.ruleType` enum (§8.4). `must`/`never` hard; `prefer`/`avoid` soft."""

    MUST = "must"
    NEVER = "never"
    PREFER = "prefer"
    AVOID = "avoid"


class EnforcedBy(StrEnum):
    """`BrandRule.enforcedBy` enum (§8.4) — which §9 rule enforces it."""

    GROUNDING = "grounding"
    BRAND = "brand"
    COPPA = "coppa"
    SCHEMA = "schema"


class Severity(StrEnum):
    """`BrandRule.severity` enum (§8.4). `block` ⇒ failing it BLOCKS the piece (§9)."""

    BLOCK = "block"
    WARN = "warn"


class RecipeParamType(StrEnum):
    """`RecipeParam.type` enum (§8.5) — the typed input kind."""

    STRING = "string"
    NUMBER = "number"
    ENUM = "enum"
    CHANNEL = "channel"


class LibraryAssetType(StrEnum):
    """`LibraryAsset.assetType` enum (§5)."""

    COPY = "copy"
    IMAGE = "image"
    VIDEO = "video"
    BLOG_POST = "blog_post"
    FAQ_BLOCK = "faq_block"
    COMPARISON_TABLE = "comparison_table"
    RECIPE_OUTPUT = "recipe_output"


# ---------------------------------------------------------------------------
# §8.3 BrandMemoryItem — persisted, conditioning memory (FR-3.2).
# ---------------------------------------------------------------------------


class BrandMemoryItem(BaseModel):
    """`BrandMemoryItem` (§8.3, FR-3.2) — memory that conditions the next batch.

    Frozen + `extra="forbid"`: a memory item is immutable once built; an edit
    bumps `version` and the old version is retained (NFR-6). `weight`, `active`,
    `version` are [req] — they drive the §8.3.2 conditioning loop (select active
    items, rank by weight, inject) and the audit trail; a record missing any is
    V-1-invalid. `kind` is the closed :class:`BrandMemoryKind`. `channel_scope`
    empty = applies to all channels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    kind: BrandMemoryKind
    content: str = Field(min_length=1)
    signal: BrandMemorySignal | None = None
    source_ref: str | None = None
    weight: float
    channel_scope: list[Channel] = Field(default_factory=list)
    active: bool
    version: int
    provenance: Provenance


# ---------------------------------------------------------------------------
# §8.4 BrandRule — a content rule as data (FR-3.12, the brand operating system).
# ---------------------------------------------------------------------------


class BrandRule(BaseModel):
    """`BrandRule` (§8.4, FR-3.12) — a do/don't, must/never content rule as data.

    Enforced by the validation pipeline (§9). `enforced_by` names which §9 rule
    enforces it; `severity=block` ⇒ failing it BLOCKS the piece (§9.3). All three
    classifying fields are closed enums. Frozen + `extra="forbid"`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    rule_type: RuleType
    statement: str = Field(min_length=1)
    applies_to: list[Channel] = Field(default_factory=list)
    enforced_by: EnforcedBy
    severity: Severity
    active: bool
    provenance: Provenance


# ---------------------------------------------------------------------------
# §8.5 MarketingRecipe + RecipeParam — runnable, Tom Babb-attributed template.
# ---------------------------------------------------------------------------


class RecipeParam(BaseModel):
    """`RecipeParam` (§8.5) — one typed, named recipe input that makes it runnable.

    `type` is the closed :class:`RecipeParamType`. Frozen + `extra="forbid"`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: RecipeParamType
    required: bool
    default: str | None = None
    options: list[str] | None = None


class MarketingRecipe(BaseModel):
    """`MarketingRecipe` (§8.5, FR-3.12) — Tom Babb's open marketing skills as data.

    INV-7 / §8.5 (LOCKED): `attribution` is [req], `min_length=1`, and MUST name
    Tom Babb — his skills are ATTRIBUTED, never claimed as the builder's. A blank
    or missing `attribution` RAISES `ValidationError`: stripping authorship is not
    representable. Frozen + `extra="forbid"`: the recipe (and its attribution)
    cannot be mutated off after build. Running a recipe produces
    `ContentCandidate`s (§3) whose `provenance.recipe_ref` points back here; those
    outputs still pass §9.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: list[RecipeParam]
    prompt_template: str = Field(min_length=1)
    output_channel: Channel | None = None
    output_format: ContentFormat | None = None
    brand_rule_refs: list[str] = Field(default_factory=list)
    version: int
    provenance: Provenance


# ---------------------------------------------------------------------------
# §5 LibraryAsset — searchable, kept/curated library item (FR-3.4).
# ---------------------------------------------------------------------------


class LibraryAsset(BaseModel):
    """`LibraryAsset` (§5, FR-3.4) — the durable, reusable, searchable library unit.

    Only validated content enters the library: `validation` (a passing
    `ValidationResult` id) and `lifecycle` (typically `kept`) are [req], as are
    `tags` and `search_text` (the denormalized search index). `asset_type` is the
    closed :class:`LibraryAssetType`. Frozen + `extra="forbid"`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    asset_type: LibraryAssetType
    channel: Channel | None = None
    format: ContentFormat | None = None
    body: str | None = None
    asset_uri: str | None = None
    source_ref: str | None = None
    tags: list[str]
    search_text: str = Field(min_length=1)
    validation: str = Field(min_length=1)
    lifecycle: LifecycleStage
    provenance: Provenance

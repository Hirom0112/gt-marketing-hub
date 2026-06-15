"""Content-engine schemas — the content-as-data contract (CONTENT_SPEC §2/§3).

CONTENT_SPEC §1.1: the marketing workspace is **content-as-data** — every
concept/copy candidate is a typed, versioned, validated record, never a free-form
blob. The LLM emits a `proposal` (INV-2); a malformed payload RAISES
`pydantic.ValidationError` rather than coercing into a write. §9.2 Rule V-1
(schema-validity) operates on exactly these shapes: every **[req]** field is
enforced, every enum is CLOSED (an out-of-range value RAISES), and an unknown
extra field is rejected — the records fail closed.

§9.2 Rule V-3 (COPPA-safe): :class:`AudienceTag` is a closed set over four
adult/leadership audiences — **there is no minor audience** (INV-6). A
minor-targeted value is not representable.

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: no `anthropic` / `langgraph` /
I/O imports — guarded by `test_core_purity`. Mirrors the StrEnum + Pydantic-v2
(`frozen=True, extra="forbid"`) conventions in `app/data/models.py` and
`app/ai/schemas/enrollment_draft.py`. CONTENT_SPEC uses camelCase wire names
(e.g. `audienceTag`); this model is the source of truth in snake_case — no
aliases (none of the tests need the wire name).

The pass/fail gate itself (`ValidationResult`, §9.6) is NOT defined here: it lives
in `app/core/eval_gate.py` (a separate agent, ASSUMPTIONS A-10). Content records
reference it by id string via `validation`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# §2.1–§2.3 shared closed enums. String-valued so they serialize to the exact
# tokens CONTENT_SPEC locks, matching the StrEnum style in `app/data/models.py`.
# ---------------------------------------------------------------------------


class Channel(StrEnum):
    """`Channel` enum (§2.1, LOCKED). `geo` = AI-search content, not a social feed."""

    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    X = "x"
    LINKEDIN = "linkedin"
    EMAIL = "email"
    BLOG = "blog"
    LANDING_PAGE = "landing_page"
    GEO = "geo"


class ContentFormat(StrEnum):
    """`ContentFormat` enum (§2.2, LOCKED)."""

    SHORT_CAPTION = "short_caption"
    LONG_CAPTION = "long_caption"
    THREAD = "thread"
    BLOG_POST = "blog_post"
    FAQ_BLOCK = "faq_block"
    COMPARISON_TABLE = "comparison_table"
    DEFINITION = "definition"
    EMAIL_SUBJECT = "email_subject"
    EMAIL_BODY = "email_body"
    AD_COPY = "ad_copy"
    VIDEO_SCRIPT = "video_script"
    IMAGE_BRIEF = "image_brief"


class LifecycleStage(StrEnum):
    """`LifecycleStage` enum (§2.3, LOCKED — the content-as-data state machine).

    `blocked` is terminal-until-edited (a §9-failing piece cannot advance);
    `kept` is the signal that conditions brand memory (§8.3).
    """

    DRAFT = "draft"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    KEPT = "kept"
    SCHEDULED = "scheduled"
    SIMULATED_SENT = "simulated_sent"
    DISCARDED = "discarded"
    BLOCKED = "blocked"


class AudienceTag(StrEnum):
    """`audienceTag` enum (§3, LOCKED). **Never a minor-targeted audience** (§9 V-3).

    The closed set is exactly the adult/leadership audiences; a child segment is
    not representable, enforcing COPPA-safety at the type level (INV-6).
    """

    PROSPECTIVE_PARENT = "prospective_parent"
    CURRENT_PARENT = "current_parent"
    LEADERSHIP = "leadership"
    GENERAL = "general"


class GeneratedBy(StrEnum):
    """`provenance.generatedBy` enum (§2.4) — how a record came to exist."""

    LLM = "llm"
    HUMAN = "human"
    RECIPE = "recipe"
    IMPORT = "import"
    SYNTHETIC_SEED = "synthetic_seed"


class Decision(StrEnum):
    """`HumanDecision.decision` enum (§2.5) — the review/approve verdict."""

    PENDING = "pending"
    KEEP = "keep"
    DISCARD = "discard"
    EDIT = "edit"
    APPROVE = "approve"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# §2.4 / §2.5 shared groups. Frozen + extra="forbid": closed records — an
# unknown field is V-1-invalid (§1.2); a proposal is not mutated after parse.
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """`Provenance` group (§2.4, LOCKED — required on every generated record).

    Mandatory so every AI output / eval / human decision is queryable (NFR-6).
    `model_ref` / `prompt_id` / `recipe_ref` / `brand_memory_refs` are [opt]
    links; `generated_by` + `created_at` are [req].
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_by: GeneratedBy
    created_at: str = Field(min_length=1)
    model_ref: str | None = None
    prompt_id: str | None = None
    recipe_ref: str | None = None
    brand_memory_refs: list[str] = Field(default_factory=list)
    created_by_user: str | None = None


class HumanDecision(BaseModel):
    """`HumanDecision` group (§2.5, LOCKED — review/approve audit, FR-3.5/NFR-6).

    `decision` is [req] (default `pending`); the rest is [opt] audit context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision = Decision.PENDING
    decided_by: str | None = None
    decided_at: str | None = None
    edit_delta: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# §3 ContentCandidate — the atomic generated keep/discard unit (FR-3.1).
# ---------------------------------------------------------------------------


class ContentCandidate(BaseModel):
    """`ContentCandidate` (§3, FR-3.1) — one generated concept/copy candidate.

    Frozen + `extra="forbid"`: the proposal is immutable once parsed and rejects
    any unexpected field, so a malformed LLM payload fails closed (V-1, §9.2)
    rather than being coerced into a keep. `audience_tag` is the closed
    :class:`AudienceTag` (never a minor audience, §9 V-3). A candidate cannot
    reach `kept` without a passing `ValidationResult` (§9 / FR-4.3) — that gate
    runs in `app/core/eval_gate.py`; here we only model the data so it CAN be
    gated. `claims` feeds §9 V-2 grounding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    channel: Channel
    format: ContentFormat
    concept: str = Field(min_length=1)
    # §3 names this field `copy`; that spec name shadows `BaseModel.copy`, so the
    # Python attribute is `copy_text` and the spec/wire name is kept as an alias.
    # `populate_by_name=True` lets callers pass either `copy=` or `copy_text=`.
    copy_text: str = Field(min_length=1, alias="copy")
    claims: list[str] = Field(default_factory=list)
    cta: str | None = None
    audience_tag: AudienceTag
    family_ref: str | None = None
    lifecycle: LifecycleStage
    validation: str | None = None
    decision: HumanDecision
    provenance: Provenance

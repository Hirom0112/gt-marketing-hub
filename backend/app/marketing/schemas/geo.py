"""`GeoContentPiece` — the GEO content-as-data record (CONTENT_SPEC §7.1, LOCKED).

§7 is the GEO (generative-engine-optimization) engine's content type: the
structured piece a gifted-school query should *win* in AI-search answers. Like
every content record (§1.1) it is a typed, versioned, validated proposal
(INV-2), never a free-form blob — a malformed payload RAISES
`pydantic.ValidationError` rather than coercing into a write, and §9.2 Rule V-1
operates on exactly this shape (closed `geoStructure` enum, `extra="forbid"`,
every [req] field enforced — fail closed).

It is gated by the SAME `app.core.eval_gate.evaluate_message` that gates
enrollment drafts and content candidates — there is NO second gate (A-10). The
gate normalizes record text via `record.body` and reads `record.claims` for V-2
grounding; this model exposes `body` directly and a read-only `claims` property
(over the `claims_text` field) so it structurally satisfies the gate's
`GatedRecord` Protocol (whose `claims` is a `@property -> Sequence[object]`).

`baselineCoverage` starts at the **0% baseline** (§7.1) — GEO begins with zero
AI-search coverage and is grown deliberately; a single-snapshot coverage claim
is invalid and must be measured by repeated sampling (`samplingNote`, §7.4).
`competitorSet` is the LOCKED gifted-school universe only (§7.3) — validated by
`app.marketing.geo.validate_competitor_set`, never auto-picked test-prep brands
(INV-6).

Pure data per CLAUDE.md §3: imports only `app.ai.schemas.content`
(reusing the LOCKED `Provenance` / `LifecycleStage`), pydantic, and stdlib — no
`anthropic` / `langgraph` / I/O (guarded by `test_core_purity`).

CONTENT_SPEC uses camelCase wire names (`geoStructure`, `baselineCoverage`); the
Python attributes are snake_case (the source of truth, matching
`app/ai/schemas/content.py`), with Pydantic aliases + `populate_by_name=True`
so either form populates — §7.1 references the wire names directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.content import LifecycleStage, Provenance


class GeoStructure(StrEnum):
    """`geoStructure` enum (§7.1, LOCKED) — the structured form that wins citations.

    Closed set: an out-of-range value RAISES (V-1, §9.2). These are the
    AI-search-citable structured forms a gifted-school query can win.
    """

    DEFINITION = "definition"
    FAQ = "faq"
    COMPARISON_TABLE = "comparison_table"
    STATISTIC_BLOCK = "statistic_block"
    QUOTATION_BLOCK = "quotation_block"


class GeoContentPiece(BaseModel):
    """`GeoContentPiece` (§7.1, LOCKED) — one GEO content-as-data record.

    Frozen + `extra="forbid"`: the proposal is immutable once parsed and rejects
    any unexpected field, so a malformed LLM payload fails closed (V-1, §9.2)
    rather than being coerced into a write (INV-2). It flows through the EXISTING
    grounding gate unchanged (A-10): `body` is the gated text and the read-only
    `claims` property feeds §9 V-2 grounding.

    `baseline_coverage` defaults to the **0% baseline** (§7.1). `competitor_set`
    must be the gifted-school universe (§7.3) — enforce it at the call site with
    `app.marketing.geo.validate_competitor_set` before persisting (INV-6).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: UUID
    target_prompt: str = Field(min_length=1, alias="targetPrompt")
    geo_structure: GeoStructure = Field(alias="geoStructure")
    body: str = Field(min_length=1)
    # §7.3: gifted-school domains only — validated by `validate_competitor_set`.
    competitor_set: list[str] = Field(alias="competitorSet")
    citation_targets: list[str] = Field(default_factory=list, alias="citationTargets")
    structured_data_note: str | None = Field(default=None, alias="structuredDataNote")
    # §7.1: starts at the 0% baseline; grown deliberately via repeated sampling.
    baseline_coverage: float = Field(default=0.0, alias="baselineCoverage")
    sampling_note: str | None = Field(default=None, alias="samplingNote")
    validation: str = Field(min_length=1)
    lifecycle: LifecycleStage
    provenance: Provenance
    # The empirical claim strings the piece carries — surfaced to the gate's V-2
    # grounding via the read-only `claims` property below. Default empty: a piece
    # with no empirical assertions carries no claims.
    claims_text: list[str] = Field(default_factory=list, alias="claimsText")

    @property
    def claims(self) -> Sequence[str]:
        """Read-only V-2 grounding evidence (the `GatedRecord` Protocol shape).

        Returns the empirical claim strings (`claims_text`) so the EXISTING
        `app.core.eval_gate` reads them exactly as it reads a
        `ContentCandidate`'s bare-string claims (A-10). A property (not a
        settable field) to match the gate's covariant `claims` Protocol.
        """
        return self.claims_text

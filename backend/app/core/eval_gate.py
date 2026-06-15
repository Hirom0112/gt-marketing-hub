"""The message safety/grounding gate — FR-4.3, INV-3 / INV-4 (CONTENT_SPEC §9).

This is the architectural heart of the product. Every AI-drafted outbound
enrollment message crosses this gate before a human ever sees an "approve"
button: INV-3 (no AI action reaches a human un-evaled) and INV-4 (the gate
**BLOCKS**, never softens — fail-closed). The verdict is a single
:class:`ValidationResult` mirroring `CONTENT_SPEC §9.6`, and

    passed = V-1 ∧ V-2 ∧ V-3 ∧ V-4

with **any** single FAIL ⇒ BLOCKED. There is no "soften and continue" path
(§9.3): a failing piece is returned with its `failed_rules` for the audit log,
and the caller must not advance it.

The four rules (§9.2):

* **V-1 Schema-validity.** A valid :class:`EnrollmentDraftProposal` *is* the
  V-1 pass — a malformed payload raises `pydantic.ValidationError` upstream and
  never reaches this gate. We re-assert the shape defensively here.
* **V-2 Grounding.** Banned-pattern set (performance multipliers, unsupported
  superlatives, guaranteed outcomes) matches zero spans AND every empirical
  claim carries a `source_ref` ⇒ pass; else FAIL. This satisfies FR-4.3's
  "4X/2X" example.
* **V-3 COPPA-safe.** No minor-targeting audience, no minor handle /
  child-identifying PII, no behavioral-targeting-of-minors signal ⇒ pass
  (INV-6).
* **V-4 On-brand.** Zero active `never`-rule violations AND a brand-conformance
  score ≥ the params threshold. The score comes from an **injected** judge
  (a proposal — INV-2); if the judge is unavailable the gate degrades to
  **DENY**, never a silent pass (§9.4, NFR-5 fail-closed posture).

Purity (CLAUDE.md §3, ARCHITECTURE.md §3; guarded by `test_core_purity`): this
module imports nothing from `app.ai` / `app.adapters` and performs no network
or LLM call. V-1/V-2/V-3 are pure deterministic predicates; the V-4 LLM judge
is **injected** as a callable, never imported here — the live wiring to
`settings`/Anthropic is a deliberately-marked seam for a later slice.

Every threshold reads from `params/params.yaml`
(`eval_thresholds.message_safety_grounding`) — no magic number in code
(INV-11).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.core.params import Params
    from app.core.settings import Settings


# The record contracts are consumed STRUCTURALLY (duck-typed), never imported
# from `app.ai` — importing an edge schema here would break core purity (INV-2,
# §3, guarded by `test_core_purity`). Both the S2 `EnrollmentDraftProposal`
# (`.body`) and the S4 `ContentCandidate` (`.copy_text`) satisfy this protocol,
# so a single canonical gate evaluates both (A-10).
@runtime_checkable
class GatedRecord(Protocol):
    """Structural contract for anything the gate evaluates (A-10).

    The text field is `body` (enrollment draft) OR `copy_text` (content
    candidate); `_record_text` normalizes across the two. `claims` is the V-2
    grounding evidence: a `Sequence` of either :class:`Claim`-like objects
    (`.text` / `.source_ref`, the enrollment-draft form) or bare claim strings
    (the content-candidate form). Defined as a `Protocol` so BOTH record types
    type-check through `evaluate_message` without the forbidden `app.ai` import.
    """

    claims: Sequence[object]


# A `BrandRule`-like object (§8.4) is consumed structurally too: V-4 only reads
# `rule_type`, `statement`, `active`. No `app.ai` import (purity).
@runtime_checkable
class BrandRuleLike(Protocol):
    """Structural view of a §8.4 `BrandRule` — only the fields V-4 reads."""

    rule_type: object
    statement: str
    active: bool


def _record_text(record: GatedRecord) -> str:
    """The text body of a gated record, normalized across S2 / S4 shapes (A-10).

    Returns `record.body` (enrollment draft) or `record.copy_text` (content
    candidate); empty string if neither is a usable string, which V-1 then
    treats as a schema failure.
    """
    text = getattr(record, "body", None) or getattr(record, "copy_text", None)
    return text if isinstance(text, str) else ""


# The params block every threshold in this gate reads from (INV-11). Surfaced on
# the verdict as `threshold_ref` so the audit log can point at the exact entry.
THRESHOLD_REF = "eval_thresholds.message_safety_grounding"

# A brand-conformance score in [0,1], or ``None`` to signal "judge unavailable"
# (⇒ V-4 deny, §9.4). The judge is a proposal (INV-2), injected — never imported.
# It receives the gated record and the active never-rule statements.
BrandJudge = Callable[["GatedRecord", list[str]], "float | None"]

# COPPA-safe audiences (§9.2 V-3). Anything else is a minor-targeting signal.
COPPA_SAFE_AUDIENCES: frozenset[str] = frozenset(
    {"prospective_parent", "current_parent", "parents", "educators", "leadership", "general"}
)

# Default `never`-rule set (V-4) — the DATA form of the §8.4 banned "4X/2X"
# family plus an off-brand exemplar. Kept minimal on purpose: the full §8
# brand-memory schema (BrandMemoryItem / BrandRule / recipes) is S4, not here.
DEFAULT_NEVER_RULES: tuple[str, ...] = (
    "4x speed",
    "2x speed",
    "guaranteed",
    "act now or lose your spot",
)


class RuleVerdict(StrEnum):
    """Per-rule outcome (§9.6): exactly `pass` or `fail`."""

    PASS = "pass"
    FAIL = "fail"


class ValidationResult(BaseModel):
    """The gate verdict — `CONTENT_SPEC §9.6` `ValidationResult` (snake_case).

    `passed` is the AND of the four rule verdicts; `failed_rules` lists the
    named rules that failed, for the audit log (NFR-6). `brand_score` is the
    V-4 conformance score (``None`` when the judge was unavailable ⇒ deny).
    `threshold_ref` points at the params entry the thresholds came from (INV-11).
    Frozen: a verdict is not mutated after the gate produces it.

    The §9.6 enrichment fields — `subject_ref` / `subject_type` (which record
    was gated, e.g. a `ContentCandidate.id` and `"content_candidate"`),
    `judge_model_ref` (the V-4 judge identity), `provenance_ref` (a link back to
    the record's provenance) — are all OPTIONAL and default to ``None`` so
    existing S2 callers that never set them keep working unchanged (A-10).
    """

    model_config = ConfigDict(frozen=True)

    v1_schema: RuleVerdict
    v2_grounding: RuleVerdict
    v3_coppa: RuleVerdict
    v4_onbrand: RuleVerdict
    passed: bool
    failed_rules: list[str] = Field(default_factory=list)
    brand_score: float | None = None
    threshold_ref: str = THRESHOLD_REF
    evaluated_at: str | None = None
    # §9.6 enrichment (A-10) — optional, default None; never required by callers.
    subject_ref: str | None = None
    subject_type: str | None = None
    judge_model_ref: str | None = None
    provenance_ref: str | None = None


# --------------------------------------------------------------------------- #
# V-1 — Schema-validity (pure).
# --------------------------------------------------------------------------- #
def _claim_text(claim: object) -> str | None:
    """The text of a claim across S2 / S4 forms (A-10).

    Enrollment drafts carry :class:`Claim` objects (`.text`); content
    candidates carry bare claim strings. Returns the text, or ``None`` if the
    claim has no usable text.
    """
    if isinstance(claim, str):
        return claim
    text = getattr(claim, "text", None)
    return text if isinstance(text, str) else None


def _claim_source_ref(claim: object) -> str | None:
    """The `source_ref` of a claim, or ``None`` for bare-string claims (A-10)."""
    if isinstance(claim, str):
        return None
    source = getattr(claim, "source_ref", None)
    return source if isinstance(source, str) else None


def check_v1(record: GatedRecord) -> RuleVerdict:
    """V-1 (§9.2): a validly-constructed record IS a V-1 pass.

    A malformed payload raises `pydantic.ValidationError` upstream and never
    reaches the gate; we defensively re-assert the contract — text body present/
    non-empty, every claim text non-empty — so a future caller that hands us a
    hand-built object can't smuggle a malformed record past V-1.

    Checked structurally (not an `isinstance` against the `app.ai` schema):
    importing that type here would violate core purity (the gate consumes the
    record contract structurally, never imports the edge — INV-2, §3). Works for
    both the enrollment draft (`.body`) and the content candidate (`.copy_text`).
    """
    text = _record_text(record)
    claims = getattr(record, "claims", None)
    if not text.strip():
        return RuleVerdict.FAIL
    if claims is None:
        return RuleVerdict.FAIL
    for claim in claims:
        claim_text = _claim_text(claim)
        if claim_text is None or not claim_text.strip():
            return RuleVerdict.FAIL
    return RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# V-2 — Grounding / no unverifiable claims (pure).
# --------------------------------------------------------------------------- #
# Banned grounding patterns (§9.2 V-2). FAIL on any match.
_BANNED_GROUNDING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Performance multipliers: "4X speed", "2X speed", "Nx faster", "3 times faster".
    re.compile(r"\b\d+\s*x\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*times\s+(?:faster|quicker|more)\b", re.IGNORECASE),
    # Superlatives without support.
    re.compile(r"\bthe\s+best\b", re.IGNORECASE),
    re.compile(r"#\s*1\b"),
    re.compile(r"\bnumber\s+one\b", re.IGNORECASE),
    re.compile(r"\bfastest\b", re.IGNORECASE),
    # Guaranteed outcomes.
    re.compile(r"\bguarantee(?:d|s)?\b", re.IGNORECASE),
    re.compile(r"\bwill\s+get\s+into\b", re.IGNORECASE),
)

# A claim is treated as EMPIRICAL (needs a source) when it carries a measurable /
# outcome signal: a number, a percentage, or a comparative/outcome verb. A claim
# with no such signal is treated as self-evident / non-empirical (§9.2(a)).
_EMPIRICAL_SIGNAL = re.compile(
    r"\d|%|\bpercent\b|\bmore\b|\bless\b|\bfaster\b|\bbetter\b|\bre-?enroll\b|\bincrease\b|\bproven\b",
    re.IGNORECASE,
)


def _is_empirical(text: str) -> bool:
    """A claim is empirical (and so needs a `source_ref`) if it asserts a fact."""
    return bool(_EMPIRICAL_SIGNAL.search(text))


def check_v2(record: GatedRecord) -> RuleVerdict:
    """V-2 (§9.2): banned patterns match zero spans AND every empirical claim is sourced.

    FAILS if the record text contains any banned grounding pattern, or if any
    claim is empirical yet carries no `source_ref` (an unsourced empirical
    claim; §9.2(b)). A self-evident / non-empirical claim needs no source
    (§9.2(a)). Reads the text / claims structurally so a `ContentCandidate`
    (`.copy_text`, bare-string claims) is gated identically to an enrollment
    draft (A-10).
    """
    text = _record_text(record)
    for pattern in _BANNED_GROUNDING_PATTERNS:
        if pattern.search(text):
            return RuleVerdict.FAIL
    for claim in getattr(record, "claims", []):
        claim_text = _claim_text(claim)
        if claim_text is None:
            continue
        if _is_empirical(claim_text) and not _claim_source_ref(claim):
            return RuleVerdict.FAIL
    return RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# V-3 — COPPA-safe / no targeting/PII of minors (pure).
# --------------------------------------------------------------------------- #
# Minor-PII / minor-targeting signals in the body (§9.2 V-3). FAIL on any match.
_MINOR_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # An age that is plainly a minor: "12-year-old", "age 9".
    re.compile(r"\b(?:[0-9]|1[0-7])[\s-]*year[\s-]*old\b", re.IGNORECASE),
    re.compile(r"\bage\s+(?:[0-9]|1[0-7])\b", re.IGNORECASE),
    # A child-identifying social handle (e.g. "@timmy_grade6").
    re.compile(r"@[A-Za-z0-9_]*grade\s*\d", re.IGNORECASE),
    re.compile(r"@[A-Za-z0-9_]*kid", re.IGNORECASE),
    # Behavioral-targeting-of-minors signal addressed directly at children.
    re.compile(r"\bhey\s+kids\b", re.IGNORECASE),
    re.compile(r"\bafter-?school\s+club\b.*\bsign\s+up\b", re.IGNORECASE),
)


def check_v3(record: GatedRecord, *, audience: str | None = None) -> RuleVerdict:
    """V-3 (§9.2): no minor-targeting audience, no minor handle/PII, no minor-targeting signal.

    PASS when the (optional) `audience` ∈ the COPPA-safe set AND the text holds
    no minor-PII / minor-targeting pattern (INV-6). An audience outside the safe
    set is itself a minor-targeting signal ⇒ FAIL. A `None` audience means the
    record carries no audience tag (the enrollment-draft schema has none), so
    the text patterns alone decide. A `ContentCandidate` passes its
    `audience_tag.value` here — the closed §3 set is entirely COPPA-safe.
    """
    if audience is not None and audience not in COPPA_SAFE_AUDIENCES:
        return RuleVerdict.FAIL
    text = _record_text(record)
    for pattern in _MINOR_SIGNAL_PATTERNS:
        if pattern.search(text):
            return RuleVerdict.FAIL
    return RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# V-4 — On-brand (never-rule check + injected judge; fail-closed).
# --------------------------------------------------------------------------- #
def never_statements_from_brand_rules(brand_rules: Sequence[BrandRuleLike]) -> list[str]:
    """Extract the ACTIVE `never`-rule statements from §8.4 `BrandRule`s (A-10).

    Only rules with `rule_type == "never"` AND `active is True` contribute a
    blocking phrase (an inactive never-rule does NOT block). Read structurally —
    `rule_type` may be a `RuleType` StrEnum or its string value — so no `app.ai`
    import (purity).
    """
    statements: list[str] = []
    for rule in brand_rules:
        if not getattr(rule, "active", False):
            continue
        rule_type = getattr(rule, "rule_type", None)
        if str(rule_type) != "never" and getattr(rule_type, "value", None) != "never":
            continue
        statement = getattr(rule, "statement", None)
        if isinstance(statement, str) and statement.strip():
            statements.append(statement)
    return statements


def check_v4(
    record: GatedRecord,
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    never_rules: list[str] | None = None,
    brand_rules: Sequence[BrandRuleLike] | None = None,
) -> tuple[RuleVerdict, float | None]:
    """V-4 (§9.2 / §9.4): never-rule check, then an injected judge's score vs threshold.

    Order matters and is fail-closed:

    1. Deterministic `never`-rule check — any active never-phrase in the text
       ⇒ FAIL immediately (no judge consulted). The never-phrase set is the
       union of `never_rules` (the explicit override, or
       :data:`DEFAULT_NEVER_RULES` when none) AND the ACTIVE `never`-type
       statements extracted from any `brand_rules` (§8.4). An ACTIVE never-rule
       is absolute; an INACTIVE one contributes nothing (A-10).
    2. Obtain the conformance score. If `brand_judge` is injected, call it;
       otherwise, if `settings.llm_available` is False, the judge is
       **unavailable** ⇒ score is ``None`` ⇒ V-4 DENY (§9.4). A real LLM judge
       wired to `settings` is a later concern — this is the seam; we do NOT
       import or call `anthropic` here (purity, INV-2).
    3. `score is None` ⇒ deny. `score >= threshold` ⇒ pass; else FAIL.

    The brand-conformance floor REUSES
    `eval_thresholds.message_safety_grounding.min_grounding` — there is no
    distinct brand-threshold param, so per the scope guard we reuse it rather
    than invent a code literal (INV-11).

    Returns the verdict AND the score (``None`` when unavailable) so the gate
    can surface `brand_score` on the verdict.
    """
    base_rules = DEFAULT_NEVER_RULES if never_rules is None else tuple(never_rules)
    brand_never = tuple(never_statements_from_brand_rules(brand_rules)) if brand_rules else ()
    rules = base_rules + brand_never
    lowered = _record_text(record).lower()
    for phrase in rules:
        if phrase.lower() in lowered:
            return RuleVerdict.FAIL, None

    if brand_judge is not None:
        score = brand_judge(record, list(rules))
    elif settings.llm_available:
        # SEAM: a real LLM judge wired to `settings` lives here in a later slice.
        # It is INJECTED, never imported — purity (INV-2). With no injected judge
        # and no live wiring yet, treat as unavailable ⇒ deny (fail-closed).
        score = None
    else:
        # No injected judge AND no key/kill-switch tripped ⇒ judge unavailable.
        score = None

    if score is None:
        return RuleVerdict.FAIL, None

    threshold = params.eval_thresholds.message_safety_grounding.min_grounding
    if score >= threshold:
        return RuleVerdict.PASS, score
    return RuleVerdict.FAIL, score


# --------------------------------------------------------------------------- #
# The gate.
# --------------------------------------------------------------------------- #
def _subject_type(record: GatedRecord) -> str | None:
    """The §9.6 `subject_type` for a gated record (A-10).

    Inferred structurally from the text field: a `.body` record is an
    enrollment draft; a `.copy_text` record is a content candidate. ``None`` if
    neither (an unrecognized record); never imports the edge schema.
    """
    if isinstance(getattr(record, "body", None), str):
        return "enrollment_draft"
    if isinstance(getattr(record, "copy_text", None), str):
        return "content_candidate"
    return None


def evaluate_message(
    record: GatedRecord,
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    never_rules: list[str] | None = None,
    brand_rules: Sequence[BrandRuleLike] | None = None,
    audience: str | None = None,
    evaluated_at: str | None = None,
    judge_model_ref: str | None = None,
) -> ValidationResult:
    """Run V-1..V-4 and return the single `ValidationResult` verdict (§9.3).

    `passed = V-1 ∧ V-2 ∧ V-3 ∧ V-4`; ANY single FAIL ⇒ BLOCKED, with no
    soften-and-continue path (INV-4). The verdict carries `failed_rules` for
    the audit log (NFR-6) and `brand_score` from V-4. The same gate evaluates
    both an `EnrollmentDraftProposal` (`.body`) and a `ContentCandidate`
    (`.copy_text`) — there is no second gate (A-10).

    Args:
        record: the AI-drafted record (a validated proposal — INV-2): an
            enrollment draft (`.body`) or a content candidate (`.copy_text`).
        settings: the env seam; `settings.llm_available` drives V-4's
            "judge unavailable ⇒ deny" (§9.4).
        params: the loaded params; the V-4 brand floor and V-2 caps read from
            `eval_thresholds.message_safety_grounding` (INV-11).
        brand_judge: an INJECTED brand-conformance judge (a proposal — INV-2);
            ``None`` ⇒ judge unavailable ⇒ V-4 deny when no key.
        never_rules: optional override of the default never-rule set (V-4).
        brand_rules: optional §8.4 `BrandRule`s; ACTIVE `never`-type rules add
            absolute blocking phrases to V-4 (A-10).
        audience: optional audience tag for V-3 (a content candidate passes its
            `audience_tag.value`; the enrollment-draft schema carries none).
        evaluated_at: optional injectable ISO timestamp (omitted from the pure
            paths the tests pin — no `datetime.now` here, for determinism).
        judge_model_ref: optional V-4 judge identity, surfaced on the verdict
            for the audit log (§9.6); ``None`` for the deterministic stub.

    Returns:
        A frozen :class:`ValidationResult`.
    """
    v1 = check_v1(record)
    v2 = check_v2(record)
    v3 = check_v3(record, audience=audience)
    v4, brand_score = check_v4(
        record,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
        never_rules=never_rules,
        brand_rules=brand_rules,
    )

    verdicts: dict[str, RuleVerdict] = {
        "v1_schema": v1,
        "v2_grounding": v2,
        "v3_coppa": v3,
        "v4_onbrand": v4,
    }
    failed_rules = [name for name, verdict in verdicts.items() if verdict is RuleVerdict.FAIL]
    passed = not failed_rules

    subject_ref = getattr(record, "id", None)
    provenance = getattr(record, "provenance", None)
    provenance_ref = getattr(provenance, "model_ref", None) if provenance is not None else None

    return ValidationResult(
        v1_schema=v1,
        v2_grounding=v2,
        v3_coppa=v3,
        v4_onbrand=v4,
        passed=passed,
        failed_rules=failed_rules,
        brand_score=brand_score,
        threshold_ref=THRESHOLD_REF,
        evaluated_at=evaluated_at,
        subject_ref=subject_ref if isinstance(subject_ref, str) else None,
        subject_type=_subject_type(record),
        judge_model_ref=judge_model_ref,
        provenance_ref=provenance_ref if isinstance(provenance_ref, str) else None,
    )

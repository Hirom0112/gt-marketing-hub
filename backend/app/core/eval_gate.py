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
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from typing import Any

    from app.core.params import Params
    from app.core.settings import Settings

    # The proposal contract is consumed STRUCTURALLY (duck-typed), never imported
    # from `app.ai` — importing the edge schema here would break core purity
    # (INV-2, §3, guarded by `test_core_purity`). `EnrollmentDraftProposal` is a
    # protocol-style alias so signatures read clearly without the forbidden import.
    EnrollmentDraftProposal = Any


# The params block every threshold in this gate reads from (INV-11). Surfaced on
# the verdict as `threshold_ref` so the audit log can point at the exact entry.
THRESHOLD_REF = "eval_thresholds.message_safety_grounding"

# A brand-conformance score in [0,1], or ``None`` to signal "judge unavailable"
# (⇒ V-4 deny, §9.4). The judge is a proposal (INV-2), injected — never imported.
BrandJudge = Callable[["EnrollmentDraftProposal", list[str]], "float | None"]

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


# --------------------------------------------------------------------------- #
# V-1 — Schema-validity (pure).
# --------------------------------------------------------------------------- #
def check_v1(proposal: EnrollmentDraftProposal) -> RuleVerdict:
    """V-1 (§9.2): a validly-constructed proposal IS a V-1 pass.

    A malformed payload raises `pydantic.ValidationError` upstream and never
    reaches the gate; we defensively re-assert the contract — required fields
    present/non-null, body non-empty, every claim text non-empty — so a future
    caller that hands us a hand-built object can't smuggle a malformed record
    past V-1.

    Checked by duck-typing (not an `isinstance` against the `app.ai` schema):
    importing that type here would violate core purity (the gate consumes the
    proposal contract structurally, never imports the edge — INV-2, §3).
    """
    body = getattr(proposal, "body", None)
    claims = getattr(proposal, "claims", None)
    if not isinstance(body, str) or not body.strip():
        return RuleVerdict.FAIL
    if claims is None:
        return RuleVerdict.FAIL
    for claim in claims:
        text = getattr(claim, "text", None)
        if not isinstance(text, str) or not text.strip():
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


def check_v2(proposal: EnrollmentDraftProposal) -> RuleVerdict:
    """V-2 (§9.2): banned patterns match zero spans AND every empirical claim is sourced.

    FAILS if the `body` contains any banned grounding pattern, or if any
    :class:`Claim` is empirical yet carries no `source_ref` (an unsourced
    empirical claim; §9.2(b)). A self-evident / non-empirical claim needs no
    source (§9.2(a)).
    """
    for pattern in _BANNED_GROUNDING_PATTERNS:
        if pattern.search(proposal.body):
            return RuleVerdict.FAIL
    for claim in proposal.claims:
        if _is_empirical(claim.text) and not claim.source_ref:
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


def check_v3(proposal: EnrollmentDraftProposal, *, audience: str | None = None) -> RuleVerdict:
    """V-3 (§9.2): no minor-targeting audience, no minor handle/PII, no minor-targeting signal.

    PASS when the (optional) `audience` ∈ the COPPA-safe set AND the body holds
    no minor-PII / minor-targeting pattern (INV-6). An audience outside the safe
    set is itself a minor-targeting signal ⇒ FAIL. A `None` audience means the
    proposal carries no audience tag (the enrollment-draft schema has none), so
    the body patterns alone decide.
    """
    if audience is not None and audience not in COPPA_SAFE_AUDIENCES:
        return RuleVerdict.FAIL
    for pattern in _MINOR_SIGNAL_PATTERNS:
        if pattern.search(proposal.body):
            return RuleVerdict.FAIL
    return RuleVerdict.PASS


# --------------------------------------------------------------------------- #
# V-4 — On-brand (never-rule check + injected judge; fail-closed).
# --------------------------------------------------------------------------- #
def check_v4(
    proposal: EnrollmentDraftProposal,
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    never_rules: list[str] | None = None,
) -> tuple[RuleVerdict, float | None]:
    """V-4 (§9.2 / §9.4): never-rule check, then an injected judge's score vs threshold.

    Order matters and is fail-closed:

    1. Deterministic `never`-rule check — any active never-phrase in the body
       ⇒ FAIL immediately (no judge consulted).
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
    rules = DEFAULT_NEVER_RULES if never_rules is None else tuple(never_rules)
    lowered = proposal.body.lower()
    for phrase in rules:
        if phrase.lower() in lowered:
            return RuleVerdict.FAIL, None

    if brand_judge is not None:
        score = brand_judge(proposal, list(rules))
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
def evaluate_message(
    proposal: EnrollmentDraftProposal,
    *,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    never_rules: list[str] | None = None,
    audience: str | None = None,
    evaluated_at: str | None = None,
) -> ValidationResult:
    """Run V-1..V-4 and return the single `ValidationResult` verdict (§9.3).

    `passed = V-1 ∧ V-2 ∧ V-3 ∧ V-4`; ANY single FAIL ⇒ BLOCKED, with no
    soften-and-continue path (INV-4). The verdict carries `failed_rules` for
    the audit log (NFR-6) and `brand_score` from V-4.

    Args:
        proposal: the AI-drafted enrollment message (already a validated
            proposal — INV-2).
        settings: the env seam; `settings.llm_available` drives V-4's
            "judge unavailable ⇒ deny" (§9.4).
        params: the loaded params; the V-4 brand floor and V-2 caps read from
            `eval_thresholds.message_safety_grounding` (INV-11).
        brand_judge: an INJECTED brand-conformance judge (a proposal — INV-2);
            ``None`` ⇒ judge unavailable ⇒ V-4 deny when no key.
        never_rules: optional override of the default never-rule set (V-4).
        audience: optional audience tag for V-3 (the enrollment-draft schema
            carries none; the gate can be told one by the caller).
        evaluated_at: optional injectable ISO timestamp (omitted from the pure
            paths the tests pin — no `datetime.now` here, for determinism).

    Returns:
        A frozen :class:`ValidationResult`.
    """
    v1 = check_v1(proposal)
    v2 = check_v2(proposal)
    v3 = check_v3(proposal, audience=audience)
    v4, brand_score = check_v4(
        proposal,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
        never_rules=never_rules,
    )

    verdicts: dict[str, RuleVerdict] = {
        "v1_schema": v1,
        "v2_grounding": v2,
        "v3_coppa": v3,
        "v4_onbrand": v4,
    }
    failed_rules = [name for name, verdict in verdicts.items() if verdict is RuleVerdict.FAIL]
    passed = not failed_rules

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
    )

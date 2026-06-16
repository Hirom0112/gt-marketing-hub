"""The V-4 brand-conformance judge — a proposal, never a state write (INV-2/INV-4).

The gate (:mod:`app.core.eval_gate`) consumes a :data:`~app.core.eval_gate.BrandJudge`
— a ``Callable[[GatedRecord, list[str]], float | None]`` — for V-4 (on-brand). It
returns a brand-conformance score in ``[0, 1]``, or ``None`` to signal "judge
unavailable" (⇒ V-4 DENY, §9.4). The gate compares the score against
``eval_thresholds.message_safety_grounding.min_grounding`` (INV-11 — no distinct
brand-threshold param; the floor is reused).

This module wires a REAL judge with two backends, chosen by the env edge:

* **LLM-backed** (``settings.llm_available``): the injected :class:`~app.ai.client.LLMClient`
  scores brand-conformance. The model returns a 0..1 float; a malformed/degraded
  response degrades to the heuristic (never a silent pass, never a live overspend
  — INV-8). The transport is injected, so no live call ever runs under test.
* **Heuristic** (no key / kill switch): a DETERMINISTIC score (a proposal, INV-2)
  computed offline — reward on-brand GT voice signals, penalize off-brand/hype
  tokens. Genuinely on-brand copy clears the threshold WITHOUT a live call;
  off-brand or banned copy scores BELOW it. It NEVER silently passes everything
  (the gate's V-1/V-2/V-3 still block banned patterns regardless of this judge).

The judge is a *proposal* (INV-2): it informs the human-gated verdict, it does not
write state. It is INJECTED into the gate (`get_brand_judge`), never imported by
`app/core/` (purity, INV-2).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.ai.client import LLMClient
from app.ai.cost import RunBudget
from app.core.eval_gate import GatedRecord, _record_text

if TYPE_CHECKING:
    from app.core.params import Params
    from app.core.settings import Settings

# --------------------------------------------------------------------------- #
# Heuristic vocabulary — the offline on-brand / off-brand signal sets.
#
# These are the DATA form of the §11.1 GT voice attributes ("confident,
# mastery-focused, parent-respectful"; "concrete over hype"; "plain language,
# speak to the parent") — NOT scoring tunables. The numeric weights/score floor
# are config (see `_HEURISTIC_*` below + the params threshold the GATE applies).
# --------------------------------------------------------------------------- #

# On-brand GT vocabulary: program-led, mastery-focused, parent-respectful voice.
# A word here signals the copy is speaking GT's actual brand (a reward signal).
_ONBRAND_TERMS: frozenset[str] = frozenset(
    {
        # Identity / fit (the strongest proven hook — INSIGHTS §2).
        "gt school",
        "gt",
        "gifted",
        "talented",
        "advanced",
        "profoundly gifted",
        "aptitude",
        "high-aptitude",
        "potential",
        # Mastery / pace model.
        "mastery",
        "mastery-based",
        "k-8",
        "k8",
        "accelerate",
        "acceleration",
        "self-paced",
        "two hour",
        "2 hour",
        "timeback",
        # Audience / voice (parent-respectful).
        "parent",
        "parents",
        "family",
        "families",
        "child",
        "children",
        # Affordability / TEFA — GT's most under-posted-yet-critical theme
        # (INSIGHTS §2); real affordability copy must read as on-brand, not bland.
        "tefa",
        "esa",
        "education savings account",
        "education freedom account",
        "voucher",
        "tuition",
        "scholarship",
        "afford",
        "affordable",
        "funding",
        "financial",
        # Model / proof / community (advisors, intensives, accreditation, the
        # #1 socialization objection answered as proof).
        "advisor",
        "advisors",
        "intensive",
        "intensives",
        "accredited",
        "accreditation",
        "virtual",
        "online",
        "cohort",
        "community",
        "socialization",
        "peer",
        "peers",
        # Program / enrollment.
        "enroll",
        "enrollment",
        "program",
        "learn",
        "learned",
        "learning",
        "school",
        "student",
        "students",
    }
)

# Off-brand / hype vocabulary the GT voice rejects ("concrete over hype"; the §8.4
# never-rules). A match here is a strong penalty — these are the tokens that make
# copy feel like marketing hype rather than GT's plain, confident voice. (The
# deterministic gate V-2 already BLOCKS the unverifiable "4X/2X/guaranteed" family
# on its own; these terms broaden the heuristic's off-brand sensitivity so a
# hype-laden message scores below the floor here too, not only at V-2.)
_OFFBRAND_TERMS: frozenset[str] = frozenset(
    {
        "act now",
        "limited time",
        "don't miss",
        "dont miss",
        "hurry",
        "amazing",
        "incredible",
        "revolutionary",
        "best in class",
        "the best",
        "guaranteed",
        "guarantee",
        "unbeatable",
        "world-class",
        "hey kids",
        "🔥",
        "!!!",
        "buy now",
        "sign up today",
    }
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")

# Heuristic scoring constants (config seam — NOT magic in logic, INV-11 posture:
# the only value the SAFETY GATE compares against is the params floor; these shape
# the proposal score the judge emits, and live in this one canonical home).
#
# On-brand copy must clear the gate floor (`min_grounding`, 0.95 in params), so a
# clean on-brand message scores at the ceiling; each off-brand hit drops it well
# below the floor. The base is intentionally just under the ceiling so that copy
# with NO on-brand signal at all (bland/generic, neither on- nor off-brand) lands
# BELOW the floor — the judge does not silently pass un-branded copy.
_HEURISTIC_CEILING = 1.0
_HEURISTIC_BASE = 0.80  # no-signal baseline — below the 0.95 floor on purpose.
_HEURISTIC_ONBRAND_BONUS = 0.05  # per distinct on-brand term, capped at ceiling.
_HEURISTIC_OFFBRAND_PENALTY = 0.50  # per off-brand hit — one hit sinks the score.


def heuristic_brand_score(record: GatedRecord, never_rules: list[str]) -> float:
    """A DETERMINISTIC offline brand-conformance score in ``[0, 1]`` (a proposal, INV-2).

    No LLM, no I/O — a pure function of the record text and the active never-rule
    phrases. Rewards distinct on-brand GT voice terms; penalizes off-brand/hype
    tokens and any active never-rule phrase appearing in the copy. Bland copy with
    no on-brand signal stays BELOW the gate floor (the judge never silently passes
    un-branded copy); genuinely on-brand GT copy clears it.

    Args:
        record: the gated record (enrollment draft ``.body`` or content
            candidate ``.copy_text``); normalized via the gate's ``_record_text``.
        never_rules: the active never-rule phrases the gate already enforces; a
            literal occurrence here is an additional off-brand signal.

    Returns:
        A score in ``[0.0, 1.0]``.
    """
    text = _record_text(record).lower()
    if not text.strip():
        return 0.0

    # An off-brand token or an active never-phrase present ⇒ heavy penalty each.
    offbrand_hits = sum(1 for term in _OFFBRAND_TERMS if term in text)
    offbrand_hits += sum(1 for phrase in never_rules if phrase.lower() in text)

    # Distinct on-brand terms present (multi-word terms matched as substrings,
    # single words matched as whole tokens so "scholarship" doesn't match "school").
    tokens = set(_WORD_RE.findall(text))
    onbrand_hits = 0
    for term in _ONBRAND_TERMS:
        if " " in term or "-" in term:
            if term in text:
                onbrand_hits += 1
        elif term in tokens:
            onbrand_hits += 1

    score = (
        _HEURISTIC_BASE
        + onbrand_hits * _HEURISTIC_ONBRAND_BONUS
        - offbrand_hits * _HEURISTIC_OFFBRAND_PENALTY
    )
    return max(0.0, min(_HEURISTIC_CEILING, score))


# The prompt the LLM judge scores against — concrete, returns a bare float so the
# parse is trivial and degradation is unambiguous.
_LLM_JUDGE_PROMPT = (
    "You are GT School's brand-conformance judge. Score how on-brand the following "
    "marketing copy is, where GT's voice is confident, mastery-focused, "
    "parent-respectful, concrete over hype, and plain-language. Penalize hype, "
    "unverifiable performance claims, and anything addressed at children. "
    "These phrases must NEVER appear: {never_rules}. "
    "Reply with ONLY a single decimal number between 0 and 1 (1 = perfectly "
    "on-brand, 0 = off-brand). Copy:\n\n{copy}"
)

_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+")


def _parse_score(text: str) -> float | None:
    """Parse the first ``[0,1]`` float from the model reply, or ``None`` if absent."""
    match = _FLOAT_RE.search(text)
    if match is None:
        return None
    try:
        value = float(match.group())
    except ValueError:
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


class BrandJudge:
    """A callable V-4 brand judge — LLM-backed when available, heuristic otherwise.

    Satisfies the gate's :data:`~app.core.eval_gate.BrandJudge` protocol
    (``__call__(record, never_rules) -> float | None``). With a usable LLM edge it
    scores via the injected client; with no key / kill switch (or a degraded /
    unparseable model reply) it falls back to :func:`heuristic_brand_score` — never
    a silent pass, never a live overspend (INV-8, §9.4 fail-closed posture).

    Args:
        settings: the env seam; ``settings.llm_available`` selects the backend.
        params: the loaded params — the per-run budget (INV-8) is built from it.
        client: an injected :class:`~app.ai.client.LLMClient` (tests inject a fake
            transport so no live call runs); ``None`` ⇒ heuristic-only.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        params: Params,
        client: LLMClient | None = None,
    ) -> None:
        self._settings = settings
        self._params = params
        self._client = client

    def __call__(self, record: GatedRecord, never_rules: list[str]) -> float | None:
        """Score brand-conformance in ``[0,1]`` (a proposal, INV-2); never ``None`` here.

        Always returns a real score: the LLM path falls back to the heuristic on
        an unavailable edge or an unparseable reply, so V-4 is decided on a genuine
        conformance score rather than degraded to a blanket deny. (A blanket deny
        is still the gate's behavior when NO judge is injected at all — that seam
        is preserved; this judge, once wired, always proposes a score.)
        """
        if self._client is not None and self._settings.llm_available:
            llm_score = self._score_via_llm(record, never_rules)
            if llm_score is not None:
                return llm_score
            # Degraded / unparseable ⇒ fall back to the heuristic (no silent pass).
        return heuristic_brand_score(record, never_rules)

    def _score_via_llm(self, record: GatedRecord, never_rules: list[str]) -> float | None:
        """Ask the injected client for a 0..1 score; ``None`` if degraded/unparseable."""
        if self._client is None:
            # No edge wired ⇒ no LLM score; the caller falls back to the heuristic.
            return None
        prompt = _LLM_JUDGE_PROMPT.format(
            never_rules="; ".join(never_rules) or "(none)",
            copy=_record_text(record),
        )
        budget = RunBudget.from_config(settings=self._settings, params=self._params)
        result = self._client.complete(
            prompt, max_tokens=self._settings.anthropic_max_tokens, budget=budget
        )
        if result.degraded:
            return None
        return _parse_score(result.text)

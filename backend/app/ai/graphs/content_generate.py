"""Content-generation orchestration — conditioning → batch → gate (FR-3.1; ARCH §5.3).

The marketing analog of the §5.2 enrollment-draft pipeline, wired as one linear
deterministic orchestration (ASSUMPTIONS A-6 — no branching state, so no
LangGraph dependency). The §5.3 doctrine:

  1. the deterministic core assembles a brand-CONDITIONED context from persisted
     brand memory (:func:`app.ai.conditioning.assemble_conditioning`) — the
     conditioning block's text is injected into the generation prompt and its
     ``brand_memory_refs`` stamp every candidate's provenance (NFR-6 audit);
  2. the AI edge returns a BATCH of candidate proposals (a JSON array). The
     client fails closed to a degraded path — NO live call — when the edge is
     unavailable / the kill switch is on / the budget is tripped (INV-8, NFR-5);
     the degraded batch is built from PERSISTENT brand-memory exemplars instead;
  3. each item is parsed into :class:`ContentCandidate`. A malformed item is
     DROPPED at the boundary — never coerced (INV-2);
  4. the eval gate (:func:`app.core.eval_gate.evaluate_message`) runs on EACH
     candidate (INV-3). A failing candidate is WITHHELD (not surfaced) — but the
     caller still LOGS its proposal + failing eval (INV-4 audit side);
  5. only PASSING candidates surface, each paired with its passing verdict.

Purity at the edge (CLAUDE.md §3): this module consumes the
:class:`~app.ai.client.LLMClient` protocol and the brand-memory store boundary;
it imports **no** ``anthropic`` / ``langgraph``. Tests inject a fake transport +
judge, so no live call ever runs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, cast

from pydantic import ValidationError

from app.ai.client import LLMClient
from app.ai.conditioning import ConditioningBlock, assemble_conditioning
from app.ai.schemas.content import Channel, ContentCandidate
from app.core.eval_gate import BrandJudge, BrandRuleLike, ValidationResult, evaluate_message

if TYPE_CHECKING:
    from app.adapters.brand_memory.base import BrandMemoryStore
    from app.ai.cost import RunBudget
    from app.ai.schemas.brand import BrandRule
    from app.core.params import Params
    from app.core.settings import Settings


class SurfacedCandidate(NamedTuple):
    """A candidate that PASSED the gate, paired with its passing verdict (§5.3 step 5).

    `validation.passed` is always True for a surfaced candidate — a failing one
    is withheld, never paired here. A :class:`NamedTuple` so callers can both
    tuple-unpack ``(candidate, validation)`` and read ``.candidate`` /
    ``.validation``. The verdict stays attached so the API can log the eval and
    the keep path can re-assert the pass (FR-3.5).
    """

    candidate: ContentCandidate
    validation: ValidationResult


class WithheldCandidate(NamedTuple):
    """A candidate that FAILED the gate — withheld from the operator (INV-3/INV-4).

    Not surfaced, but retained so the caller can LOG the proposal + its failing
    eval (the "zero unverifiable claims escape" audit, INV-4). `validation` is
    always a failing verdict. A :class:`NamedTuple` (see :class:`SurfacedCandidate`).
    """

    candidate: ContentCandidate
    validation: ValidationResult


@dataclass(frozen=True)
class ContentBatchOutcome:
    """The result of the §5.3 generation pipeline — surfaced vs withheld + flags.

    `surfaced` holds only candidates whose eval PASSED (each with its passing
    verdict). `withheld` holds gated-but-FAILING candidates (logged, not shown).
    `withheld_count` is ``len(withheld)`` — the blocked count the API reports.
    `degraded` is True when the edge was unavailable / killed / over-cap and the
    batch came from persistent brand memory instead of a live call (NFR-3).
    """

    surfaced: list[SurfacedCandidate] = field(default_factory=list)
    withheld: list[WithheldCandidate] = field(default_factory=list)
    degraded: bool = False

    @property
    def withheld_count(self) -> int:
        """The number of gated-but-failing (blocked) candidates (the audit count)."""
        return len(self.withheld)


def _build_prompt(prompt: str, channel: Channel | str, block: ConditioningBlock) -> str:
    """Render the operator prompt + brand-conditioning block into the batch prompt.

    The conditioning block's text (voice → do → dont → exemplars → signals) is
    injected verbatim so the edge is conditioned on persisted brand memory
    (FR-3.2). The edge is instructed to return a JSON ARRAY of objects conforming
    to the :class:`ContentCandidate` schema — a batch, not a single proposal.
    """
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return (
        f"Generate a BATCH of on-brand GT School marketing candidates for the "
        f"'{channel_value}' channel, following ONLY the brand memory below. Return a "
        "JSON array of objects, each conforming to the ContentCandidate schema "
        "(keys: id, batch_id, prompt, channel, format, concept, copy, claims, "
        "audience_tag, lifecycle, decision, provenance). Ground every empirical "
        "claim; never use performance multipliers; address parents/educators, "
        "never minors.\n\n"
        f"Operator request: {prompt}\n\n"
        f"Brand memory (conditioning):\n{block.text}\n"
    )


def build_campaign_prompt(
    *,
    theme: str,
    channel: Channel | str,
    audience: str,
    target_geo_prompt: str | None,
    count: int,
) -> str:
    """Render a CAMPAIGN operator prompt embedding the four campaign axes (Slice B).

    A campaign is defined by four axes that this prompt embeds so the edge conditions on
    all of them: the ``theme`` (the angle to LEAD with), the ``channel`` (shapes
    format/length — passed separately to :func:`generate_content_batch` for conditioning,
    and named here for the model), the ``audience`` (tone + CTA), and — when set — the
    ``target_geo_prompt`` (an instruction to structure the copy to WIN that AI-search
    prompt, i.e. SEO/GEO). ``count`` is the (already-clamped) requested batch size.

    The returned string is the OPERATOR request fed to :func:`generate_content_batch`,
    which then wraps it with the brand-memory conditioning block — so this helper adds no
    live call and no magic numbers (the cap is applied by the caller, INV-8/INV-11).
    """
    channel_value = channel.value if isinstance(channel, Channel) else channel
    lines = [
        f"Generate a campaign batch of {count} on-brand GT School social captions.",
        f"Lead with this THEME / angle: {theme}.",
        f"Channel: {channel_value} — shape the format and length for this channel.",
        f"Audience: {audience} — match the tone and call-to-action to this audience.",
    ]
    if target_geo_prompt:
        lines.append(
            "Structure the copy to WIN this AI-search (SEO/GEO) prompt — answer it "
            f"directly and concisely so GT School is the cited source: {target_geo_prompt}"
        )
    return "\n".join(lines)


def _parse_batch(text: str, block: ConditioningBlock) -> list[ContentCandidate]:
    """Parse the edge's JSON array into a list of :class:`ContentCandidate` (§5.3 step 3).

    A non-array payload or a malformed candidate is DROPPED — never coerced into
    a write (INV-2). Each parsed candidate's ``provenance.brand_memory_refs`` is
    set to the conditioning block's refs so the audit trail records exactly what
    shaped it (NFR-6).
    """
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    candidates: list[ContentCandidate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            candidate = ContentCandidate.model_validate(item)
        except ValidationError:
            # Malformed candidate — dropped at the boundary, never coerced (INV-2).
            continue
        candidates.append(_stamp_refs(candidate, block))
    return candidates


def _stamp_refs(candidate: ContentCandidate, block: ConditioningBlock) -> ContentCandidate:
    """Return a copy of ``candidate`` whose provenance carries the conditioning refs."""
    provenance = candidate.provenance.model_copy(
        update={"brand_memory_refs": list(block.brand_memory_refs)}
    )
    return candidate.model_copy(update={"provenance": provenance})


def _degraded_candidates(
    store: BrandMemoryStore, channel: Channel | str, prompt: str, block: ConditioningBlock
) -> list[ContentCandidate]:
    """Build candidates from PERSISTENT brand-memory exemplars (NFR-3 degraded path).

    With the live edge unavailable (no key / kill switch / cap tripped) we make NO
    live call (INV-8); instead we surface the persisted, channel-scoped exemplars
    as candidates so a degraded run still yields something on-brand and gated.
    Each carries the conditioning refs in provenance (NFR-6).
    """
    from app.ai.schemas.brand import BrandMemoryKind
    from app.ai.schemas.content import (
        AudienceTag,
        ContentFormat,
        Decision,
        HumanDecision,
        LifecycleStage,
        Provenance,
    )

    channel_value = channel.value if isinstance(channel, Channel) else channel
    channel_enum = Channel(channel_value)
    exemplars = [
        item for item in store.list_active(channel_value) if item.kind is BrandMemoryKind.EXEMPLAR
    ]
    candidates: list[ContentCandidate] = []
    for exemplar in exemplars:
        provenance = Provenance(
            generated_by=exemplar.provenance.generated_by,
            created_at=exemplar.provenance.created_at,
            brand_memory_refs=list(block.brand_memory_refs),
        )
        candidates.append(
            ContentCandidate(
                id=f"cc-degraded-{exemplar.id}",
                batch_id="batch-degraded",
                prompt=prompt,
                channel=channel_enum,
                format=ContentFormat.SHORT_CAPTION,
                concept="Degraded mode: persisted brand-memory exemplar.",
                copy=exemplar.content,
                claims=[],
                audience_tag=AudienceTag.PROSPECTIVE_PARENT,
                lifecycle=LifecycleStage.CANDIDATE,
                decision=HumanDecision(decision=Decision.PENDING),
                provenance=provenance,
            )
        )
    return candidates


def generate_content_batch(
    prompt: str,
    channel: Channel | str,
    *,
    store: BrandMemoryStore,
    client: LLMClient,
    budget: RunBudget,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    brand_rules: list[BrandRule] | None = None,
) -> ContentBatchOutcome:
    """Run the §5.3 content-generation pipeline and return a :class:`ContentBatchOutcome`.

    Assembles brand-conditioned context from persisted memory, calls the LLM edge
    (degrading to persistent exemplars with NO live call when unavailable —
    INV-8), parses the batch dropping malformed items (INV-2), runs the eval gate
    on EACH candidate (INV-3), and returns only PASSING candidates as ``surfaced``
    while retaining FAILING ones in ``withheld`` for the caller's audit log
    (INV-4).

    Args:
        prompt: the operator's generation request.
        channel: the target :class:`Channel` (or its string value).
        store: the persisted brand-memory store (conditioning source).
        client: the LLM edge seam (a fake transport is injected under test).
        budget: the per-run token/USD governor (INV-8).
        settings: the env seam; ``anthropic_max_tokens`` bounds the call.
        params: the loaded params; the eval thresholds read from here (INV-11).
        brand_judge: an INJECTED V-4 brand judge (a proposal — INV-2); ``None`` ⇒
            the gate's judge is unavailable ⇒ V-4 fail-closed (deny).
        brand_rules: optional §8.4 brand rules; ACTIVE ``never`` rules add
            absolute V-4 blocking phrases (A-10).

    Returns:
        A frozen :class:`ContentBatchOutcome`.
    """
    block = assemble_conditioning(store, channel)
    edge_prompt = _build_prompt(prompt, channel, block)

    result = client.complete(edge_prompt, max_tokens=settings.anthropic_max_tokens, budget=budget)

    if result.degraded:
        candidates = _degraded_candidates(store, channel, prompt, block)
    else:
        candidates = _parse_batch(result.text, block)

    # `BrandRule` satisfies the gate's structural `BrandRuleLike` (it reads only
    # `rule_type`/`statement`/`active`); the cast bridges the invariant Protocol
    # attribute (`rule_type: object`) to the concrete `RuleType` (A-10, purity).
    rules: Sequence[BrandRuleLike] | None = (
        cast("Sequence[BrandRuleLike]", brand_rules) if brand_rules is not None else None
    )

    surfaced: list[SurfacedCandidate] = []
    withheld: list[WithheldCandidate] = []
    for candidate in candidates:
        validation = evaluate_message(
            candidate,
            settings=settings,
            params=params,
            brand_judge=brand_judge,
            brand_rules=rules,
            audience=candidate.audience_tag.value,
        )
        if validation.passed:
            surfaced.append(SurfacedCandidate(candidate=candidate, validation=validation))
        else:
            withheld.append(WithheldCandidate(candidate=candidate, validation=validation))

    return ContentBatchOutcome(surfaced=surfaced, withheld=withheld, degraded=result.degraded)

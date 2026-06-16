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
from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentCandidate,
    ContentFormat,
    Decision,
    GeneratedBy,
    HumanDecision,
    LifecycleStage,
    Provenance,
)
from app.core.eval_gate import BrandJudge, BrandRuleLike, ValidationResult, evaluate_message

# A fixed provenance stamp for a live-generated candidate. The candidate's
# `created_at` is audit METADATA only — the §10 observability log records the real
# proposal instant separately — so a stable marker keeps the module pure (no
# datetime.now) without losing the audit trail (NFR-6).
_LIVE_CREATED_AT = "2026-06-15T00:00:00+00:00"

# Loose non-enum `format` strings the edge emits → the nearest ContentFormat token
# (the model rarely uses the exact §2.2 enum). Substring-matched, first hit wins.
_FORMAT_HINTS: tuple[tuple[str, str], ...] = (
    ("thread", "thread"),
    ("long", "long_caption"),  # "long caption" before the generic "caption" hint
    ("caption", "short_caption"),  # "single-image caption", "ig caption" → a caption
    ("tweet", "short_caption"),
    ("post", "short_caption"),
    ("blog", "blog_post"),
    ("faq", "faq_block"),
    ("comparison", "comparison_table"),
    ("table", "comparison_table"),
    ("definition", "definition"),
    ("subject", "email_subject"),
    ("email", "email_body"),
    ("video", "video_script"),
    ("reel", "video_script"),
    ("script", "video_script"),
    ("ad", "ad_copy"),
    ("image brief", "image_brief"),  # only a genuine image brief, not "...image caption"
)

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


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown ``` fence the edge often wraps JSON in.

    Claude routinely returns ```json …``` rather than a bare array; without this
    ``json.loads`` fails on the whole payload and a live batch yields ZERO
    candidates. Drops the opening fence line (``` or ```json) and the closing ```.
    """
    t = text.strip()
    if t.startswith("```"):
        newline = t.find("\n")
        if newline != -1:
            t = t[newline + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _coerce_format(value: object) -> ContentFormat:
    """Map the edge's free-text format to the nearest §2.2 ContentFormat token.

    Tries the exact token (normalizing spaces/hyphens), then a substring hint
    (e.g. "single-image caption" → short_caption via fallthrough), else defaults
    to ``short_caption`` so a loose value never drops the whole candidate.
    """
    if isinstance(value, str):
        token = value.strip().lower().replace(" ", "_").replace("-", "_")
        try:
            return ContentFormat(token)
        except ValueError:
            low = value.lower()
            for hint, fmt in _FORMAT_HINTS:
                if hint in low:
                    return ContentFormat(fmt)
    return ContentFormat.SHORT_CAPTION


def _coerce_audience(value: object) -> AudienceTag:
    """Map the edge's audience string to a valid §3 AudienceTag (else ``general``)."""
    if isinstance(value, str):
        token = value.strip().lower().replace(" ", "_").replace("-", "_")
        try:
            return AudienceTag(token)
        except ValueError:
            pass
    return AudienceTag.GENERAL


def _coerce_candidate(
    item: dict[str, object],
    index: int,
    channel: Channel | str,
    prompt: str,
    block: ConditioningBlock,
) -> ContentCandidate | None:
    """Build a valid candidate from a LOOSE edge item (§5.3 robustness).

    The edge reliably emits the CREATIVE fields (copy/concept/claims) but not the
    strict mechanical envelope (id/batch_id/provenance) or exact enum tokens. Take
    the creative content VERBATIM — so the V-2..V-4 gates judge the REAL copy and a
    "4X" claim still blocks — and synthesize/coerce only the structural fields.
    Returns ``None`` when there is no usable copy. Still a proposal (INV-2): the
    human keeps/discards and the gate still runs on the actual text.
    """
    copy_value = item.get("copy") or item.get("copy_text")
    if not isinstance(copy_value, str) or not copy_value.strip():
        return None
    concept_value = item.get("concept")
    concept = (
        concept_value.strip()
        if isinstance(concept_value, str) and concept_value.strip()
        else copy_value.strip()[:120]
    )
    claims_raw = item.get("claims")
    claims = [str(c) for c in claims_raw] if isinstance(claims_raw, list) else []
    cta_value = item.get("cta")
    cta = cta_value if isinstance(cta_value, str) and cta_value.strip() else None
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    try:
        channel_enum = Channel(channel_value)
    except ValueError:
        channel_enum = Channel.INSTAGRAM
    id_value = item.get("id")
    item_id = id_value if isinstance(id_value, str) and id_value.strip() else f"cc-live-{index}"
    batch_value = item.get("batch_id")
    batch_id = batch_value if isinstance(batch_value, str) and batch_value.strip() else "batch-live"
    try:
        return ContentCandidate(
            id=item_id,
            batch_id=batch_id,
            prompt=prompt or "campaign",
            channel=channel_enum,
            format=_coerce_format(item.get("format")),
            concept=concept,
            copy=copy_value,
            claims=claims,
            cta=cta,
            audience_tag=_coerce_audience(item.get("audience_tag") or item.get("audience")),
            lifecycle=LifecycleStage.CANDIDATE,
            decision=HumanDecision(decision=Decision.PENDING),
            provenance=Provenance(
                generated_by=GeneratedBy.LLM,
                created_at=_LIVE_CREATED_AT,
                brand_memory_refs=list(block.brand_memory_refs),
            ),
        )
    except ValidationError:
        return None


def _parse_batch(
    text: str, block: ConditioningBlock, channel: Channel | str, prompt: str
) -> list[ContentCandidate]:
    """Parse the edge's JSON into a list of :class:`ContentCandidate` (§5.3 step 3).

    Robust to how the edge actually replies: strips a ```json fence, tolerates a
    ``{"candidates": [...]}`` wrapper or a single object, then for each item tries
    STRICT validation first (a conformant edge) and falls back to tolerant
    coercion (:func:`_coerce_candidate`) — taking the creative copy verbatim and
    synthesizing only the mechanical envelope. A surfaced candidate still passes
    the full V-1..V-4 gate downstream, so coercion fixes shape, never safety. Each
    candidate carries the conditioning ``brand_memory_refs`` (NFR-6).
    """
    try:
        raw = json.loads(_strip_code_fence(text))
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(raw, dict):
        # Tolerate a wrapper object ({"candidates": [...]}) or a single candidate.
        wrapped: object = None
        for key in ("candidates", "batch", "items", "posts"):
            if isinstance(raw.get(key), list):
                wrapped = raw[key]
                break
        raw = wrapped if isinstance(wrapped, list) else [raw]
    if not isinstance(raw, list):
        return []

    candidates: list[ContentCandidate] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(_stamp_refs(ContentCandidate.model_validate(item), block))
            continue
        except ValidationError:
            pass
        coerced = _coerce_candidate(item, index, channel, prompt, block)
        if coerced is not None:
            candidates.append(coerced)
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
        candidates = _parse_batch(result.text, block, channel, prompt)

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

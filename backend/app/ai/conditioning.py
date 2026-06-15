"""Brand-memory conditioning — *memory*, not storage (FR-3.2; CONTENT_SPEC §8.3.2).

This module turns persisted brand memory into the conditioning block that shapes
the NEXT generation batch, and it closes the learning loop: keeping a candidate
affirms/creates an exemplar (so it conditions future batches), discarding a
candidate strengthens a dont/discarded signal (so the next batch avoids it).
That keep-changes-the-next-batch behavior is what makes FR-3.2 "memory, not
storage" real and gives the V-4 brand judge something to measure.

It is a pure *assembler* over the brand-memory boundary
(:class:`app.adapters.brand_memory.base.BrandMemoryStore`) — it selects, ranks,
and groups items into a prompt-ready block, and it applies keep/discard signals
via the store's `affirm`/`weaken`/`upsert`. It does NOT call an LLM: building the
actual prompt and invoking the model is the generation graph's job
(CLAUDE.md §3 boundary). It imports nothing from ``anthropic`` / ``langgraph``.

INV-11: the keep/discard weight delta is never a magic number here — it comes
from ``params.brand_memory.weight_step`` (`app/core/params.py`). The store's
in-code default (`SqliteBrandMemoryStore`'s `_DEFAULT_WEIGHT_STEP`) is a fallback
seam; the canonical value flows from params through this loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.brand_memory.base import BrandMemoryStore
from app.ai.schemas.brand import BrandMemoryItem, BrandMemoryKind, BrandMemorySignal
from app.ai.schemas.content import Channel, ContentCandidate
from app.core.params import Params

# Section order for the assembled prompt block (CONTENT_SPEC §8.3.2): voice
# attributes first, then do-rules, dont-rules, exemplars, raw signals. Each kind
# maps to one labelled section; items within a section keep weight-desc order.
_SECTION_ORDER: tuple[tuple[BrandMemoryKind, str], ...] = (
    (BrandMemoryKind.VOICE_ATTRIBUTE, "VOICE"),
    (BrandMemoryKind.DO_RULE, "DO"),
    (BrandMemoryKind.DONT_RULE, "DON'T"),
    (BrandMemoryKind.EXEMPLAR, "EXEMPLARS"),
    (BrandMemoryKind.SIGNAL, "SIGNALS"),
)


@dataclass(frozen=True)
class ConditioningBlock:
    """The prompt-ready brand-memory conditioning for one channel (§8.3.2).

    `text` is the grouped, ranked, human/LLM-readable block injected into the
    generation prompt. `brand_memory_refs` is the list of item ids that
    conditioned it, in overall rank order (weight desc) — a generated
    candidate's `provenance.brand_memory_refs` is set to exactly this list so the
    audit trail records what shaped it (NFR-6).
    """

    text: str
    brand_memory_refs: list[str] = field(default_factory=list)


def _ranked(items: list[BrandMemoryItem]) -> list[BrandMemoryItem]:
    """Rank items by weight descending; ties broken by id for stable ordering."""
    return sorted(items, key=lambda i: (-i.weight, i.id))


def assemble_conditioning(store: BrandMemoryStore, channel: Channel | str) -> ConditioningBlock:
    """Assemble the conditioning block for ``channel`` from active brand memory.

    Selects ``store.list_active(channel)`` (active-only, channel-scoped),
    ranks every item by weight descending, groups them into the §8.3.2 sections
    (voice → do → dont → exemplars → signals), and exposes both a prompt-ready
    ``text`` and ``brand_memory_refs`` (the ids used, in overall rank order).
    """
    channel_value = channel.value if isinstance(channel, Channel) else channel
    active = store.list_active(channel_value)
    ranked = _ranked(active)

    lines: list[str] = []
    for kind, label in _SECTION_ORDER:
        section = [item for item in ranked if item.kind is kind]
        if not section:
            continue
        lines.append(f"## {label}")
        for item in section:
            lines.append(f"- {item.content}")

    return ConditioningBlock(
        text="\n".join(lines),
        brand_memory_refs=[item.id for item in ranked],
    )


def _exemplar_id_for(candidate: ContentCandidate) -> str:
    """Deterministic exemplar id derived from the kept candidate's id."""
    return f"bm-exemplar-kept-{candidate.id}"


def _discard_signal_id_for(candidate: ContentCandidate) -> str:
    """Deterministic discarded-signal id derived from the candidate's id."""
    return f"bm-signal-discarded-{candidate.id}"


def apply_keep(
    store: BrandMemoryStore, candidate: ContentCandidate, params: Params
) -> BrandMemoryItem:
    """Keep ``candidate``: affirm/create an exemplar so it conditions future batches.

    If the candidate's copy maps to an existing active exemplar (same content),
    that item is ``affirm``-ed (weight += ``params.brand_memory.weight_step``,
    version bumped, ``signal=kept``). Otherwise a NEW ``exemplar`` item is
    ``upsert``-ed carrying the candidate's text at the params weight_step weight,
    ``signal=kept``, scoped to the candidate's channel. Returns the stored item.

    The weight delta is read from params (INV-11) — no magic number here.
    """
    step = params.brand_memory.weight_step

    existing = next(
        (
            item
            for item in store.list_active()
            if item.kind is BrandMemoryKind.EXEMPLAR and item.content == candidate.copy_text
        ),
        None,
    )
    if existing is not None:
        return store.affirm(existing.id, BrandMemorySignal.KEPT)

    new_item = BrandMemoryItem(
        id=_exemplar_id_for(candidate),
        kind=BrandMemoryKind.EXEMPLAR,
        content=candidate.copy_text,
        signal=BrandMemorySignal.KEPT,
        source_ref=candidate.id,
        weight=step,
        channel_scope=[candidate.channel],
        active=True,
        version=1,
        provenance=candidate.provenance,
    )
    return store.upsert(new_item)


def apply_discard(
    store: BrandMemoryStore, candidate: ContentCandidate, params: Params
) -> BrandMemoryItem:
    """Discard ``candidate``: strengthen a dont/discarded signal for the next batch.

    If the candidate's copy matches an existing active dont-rule/signal item,
    that item is ``weaken``-ed (weight -= ``params.brand_memory.weight_step``,
    version bumped, ``signal=discarded``) — its lower conditioning weight reflects
    the rejection. Otherwise a NEW ``signal`` item is ``upsert``-ed recording the
    discarded copy at the params weight_step weight, ``signal=discarded``, scoped
    to the candidate's channel. Returns the stored item.

    The weight delta is read from params (INV-11) — no magic number here.
    """
    step = params.brand_memory.weight_step

    existing = next(
        (
            item
            for item in store.list_active()
            if item.kind in (BrandMemoryKind.DONT_RULE, BrandMemoryKind.SIGNAL)
            and item.content == candidate.copy_text
        ),
        None,
    )
    if existing is not None:
        return store.weaken(existing.id, BrandMemorySignal.DISCARDED)

    new_item = BrandMemoryItem(
        id=_discard_signal_id_for(candidate),
        kind=BrandMemoryKind.SIGNAL,
        content=candidate.copy_text,
        signal=BrandMemorySignal.DISCARDED,
        source_ref=candidate.id,
        weight=step,
        channel_scope=[candidate.channel],
        active=True,
        version=1,
        provenance=candidate.provenance,
    )
    return store.upsert(new_item)

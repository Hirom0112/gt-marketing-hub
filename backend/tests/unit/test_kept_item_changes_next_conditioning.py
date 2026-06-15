"""Brand-memory conditioning is *memory*, not storage (FR-3.2; CONTENT_SPEC §8.3.2).

The pinned behavior: keeping a candidate CHANGES what the next generation batch
is conditioned on (a learning loop), and discarding strengthens a "don't"
signal. This is what makes FR-3.2 "memory, not storage" real and the V-4 brand
judge meaningful.

These tests drive `app/ai/conditioning.py`:

- `assemble_conditioning(store, channel)` selects the active, channel-scoped
  brand-memory items, ranks them by weight desc, and exposes a prompt-ready
  `text` plus `brand_memory_refs` (the ids used, in rank order).
- `apply_keep(store, candidate, params)` affirms/creates an *exemplar* from a
  kept candidate so it conditions the NEXT batch; the weight delta comes from
  `params.brand_memory.weight_step` (INV-11 — no magic number).
- `apply_discard(store, candidate, params)` strengthens a dont/discarded signal
  so the next conditioning reflects the rejection.

The committed `params/params.example.yaml` is loaded explicitly (the loader
default `params/params.yaml` is not created in this repo).
"""

from __future__ import annotations

from pathlib import Path

from app.ai.conditioning import (
    ConditioningBlock,
    apply_discard,
    apply_keep,
    assemble_conditioning,
)
from app.ai.schemas.brand import BrandMemoryKind, BrandMemorySignal
from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentFormat,
    Decision,
    GeneratedBy,
    HumanDecision,
    LifecycleStage,
    Provenance,
)
from app.ai.schemas.content import ContentCandidate
from app.core.params import load_params
from app.data.synthetic import generate_brand_memory

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _seed_store(tmp_path: Path):
    """Seed a persistent sqlite store with the synthetic brand-memory inventory."""
    from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore

    store = SqliteBrandMemoryStore(tmp_path / "brand.sqlite3")
    for item in generate_brand_memory():
        store.upsert(item)
    return store


def _candidate(*, copy_text: str, channel: Channel) -> ContentCandidate:
    """A minimal valid kept ContentCandidate for the given channel."""
    return ContentCandidate(
        id="cand-keep-1",
        batch_id="batch-1",
        prompt="Write a confident, mastery-focused caption for prospective parents.",
        channel=channel,
        format=ContentFormat.SHORT_CAPTION,
        concept="Mastery-based gifted K-8, parent-respectful.",
        copy=copy_text,
        audience_tag=AudienceTag.PROSPECTIVE_PARENT,
        lifecycle=LifecycleStage.KEPT,
        decision=HumanDecision(decision=Decision.KEEP),
        provenance=Provenance(
            generated_by=GeneratedBy.LLM,
            created_at="2026-06-14T00:00:00Z",
        ),
    )


def test_assemble_ranks_by_weight_desc_and_only_active(tmp_path: Path) -> None:
    """Conditioning selects only active items and ranks them by weight desc."""
    from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore

    store = SqliteBrandMemoryStore(tmp_path / "brand.sqlite3")
    items = generate_brand_memory()
    for item in items:
        store.upsert(item)
    # Deactivate one item: it must NOT appear in the conditioning block.
    inactive = items[0]
    store.upsert(inactive.model_copy(update={"active": False}))

    block = assemble_conditioning(store, Channel.INSTAGRAM)

    assert isinstance(block, ConditioningBlock)
    assert inactive.id not in block.brand_memory_refs

    selected = store.list_active(Channel.INSTAGRAM.value)
    by_id = {i.id: i for i in selected}
    # Every ref is an active item; none is the inactivated one.
    assert all(ref in by_id for ref in block.brand_memory_refs)
    weights = [by_id[ref].weight for ref in block.brand_memory_refs]
    assert weights == sorted(weights, reverse=True), weights


def test_kept_item_changes_next_conditioning(tmp_path: Path) -> None:
    """Keeping a candidate changes what the next batch is conditioned on (FR-3.2)."""
    store = _seed_store(tmp_path)
    params = load_params(EXAMPLE_PARAMS)

    block_a = assemble_conditioning(store, Channel.INSTAGRAM)

    kept_copy = "GT School: a mastery-based gifted K-8 program built around how your child learns."
    candidate = _candidate(copy_text=kept_copy, channel=Channel.INSTAGRAM)
    item = apply_keep(store, candidate, params)

    # The kept candidate became an exemplar carrying its text.
    assert item.kind is BrandMemoryKind.EXEMPLAR
    assert item.signal is BrandMemorySignal.KEPT
    assert kept_copy in item.content

    block_b = assemble_conditioning(store, Channel.INSTAGRAM)

    # B differs from A and now reflects the kept item.
    assert block_b.text != block_a.text
    assert item.id in block_b.brand_memory_refs
    assert kept_copy in block_b.text

    # A candidate generated from block B records exactly what conditioned it.
    next_candidate = _candidate(copy_text="next draft", channel=Channel.INSTAGRAM).model_copy(
        update={
            "provenance": Provenance(
                generated_by=GeneratedBy.LLM,
                created_at="2026-06-14T01:00:00Z",
                brand_memory_refs=block_b.brand_memory_refs,
            )
        }
    )
    assert item.id in next_candidate.provenance.brand_memory_refs


def test_keep_affirms_existing_exemplar_using_weight_step(tmp_path: Path) -> None:
    """Keeping a candidate that maps to an existing exemplar affirms it (+weight_step)."""
    store = _seed_store(tmp_path)
    params = load_params(EXAMPLE_PARAMS)

    existing = next(
        i for i in store.list_active() if i.kind is BrandMemoryKind.EXEMPLAR
    )
    candidate = _candidate(copy_text=existing.content, channel=Channel.INSTAGRAM)

    item = apply_keep(store, candidate, params)

    # Affirmed the SAME item: weight rose by exactly the params weight_step.
    assert item.id == existing.id
    assert item.weight == existing.weight + params.brand_memory.weight_step
    assert item.version == existing.version + 1
    assert item.signal is BrandMemorySignal.KEPT


def test_discard_strengthens_a_dont_signal(tmp_path: Path) -> None:
    """Discarding strengthens a dont/discarded signal so the next batch reflects it."""
    store = _seed_store(tmp_path)
    params = load_params(EXAMPLE_PARAMS)

    before = store.list_active()
    discarded_copy = "GT School makes kids learn 4X faster than any other school."
    candidate = _candidate(copy_text=discarded_copy, channel=Channel.INSTAGRAM)

    item = apply_discard(store, candidate, params)

    assert item.signal is BrandMemorySignal.DISCARDED
    assert item.kind in (BrandMemoryKind.DONT_RULE, BrandMemoryKind.SIGNAL)

    after = store.list_active()
    after_by_id = {i.id: i for i in after}
    # Either a new discarded-signal item appeared, or an existing dont/signal
    # item now records the discarded signal (its conditioning weight changed).
    before_ids = {i.id for i in before}
    if item.id in before_ids:
        before_item = next(i for i in before if i.id == item.id)
        assert after_by_id[item.id].weight != before_item.weight
        assert after_by_id[item.id].signal is BrandMemorySignal.DISCARDED
    else:
        assert item.id in after_by_id
        assert after_by_id[item.id].signal is BrandMemorySignal.DISCARDED

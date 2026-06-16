"""Integration: the seeded stores now carry IMPORT-provenance real items.

Phase-1 marketing wires the distilled scraper-library seed into the composition
root. These tests prove the wiring reaches the runtime stores: the brand-memory
store, the content library, and the GEO prompt set all surface IMPORT-provenance
records distilled from GT's OWN public marketing — while the two named gate-demo
dont_rules survive in brand memory (the §9 demo still works).
"""

from __future__ import annotations

from app.ai.schemas.brand import BrandMemoryKind
from app.ai.schemas.content import GeneratedBy
from app.api.deps import _build_brand_memory_store
from app.api.geo import _default_prompt_set
from app.data.library_ingest import seed_available
from app.marketing.library import InMemoryContentLibrary


def test_brand_memory_store_seeded_with_import_exemplars() -> None:
    """The seeded brand-memory store contains IMPORT-provenance EXEMPLARS."""
    store = _build_brand_memory_store()
    active = store.list_active()
    import_exemplars = [
        i
        for i in active
        if i.kind is BrandMemoryKind.EXEMPLAR
        and i.provenance.generated_by is GeneratedBy.IMPORT
    ]
    if seed_available():
        assert import_exemplars, "expected imported real exemplars in the store"


def test_brand_memory_keeps_gate_demo_dont_rules() -> None:
    """The two named gate-demo dont_rules survive (speed multipliers / minors)."""
    store = _build_brand_memory_store()
    contents = " ".join(i.content.lower() for i in store.list_active())
    assert "speed multipliers" in contents
    assert "target children" in contents or "target minors" in contents


def test_content_library_seeded_with_import_assets() -> None:
    """The seeded content library surfaces IMPORT-provenance assets in search."""
    library = InMemoryContentLibrary.seeded()
    results = library.search()
    assert results
    if seed_available():
        assert any(a.provenance.generated_by is GeneratedBy.IMPORT for a in results)


def test_geo_prompt_set_includes_imported_prompts() -> None:
    """The default GEO prompt set includes the imported uncontested prompts."""
    prompts = _default_prompt_set()
    assert prompts
    # The TEFA-affordability prompt is one of the imported GEO targets.
    assert any("tefa" in p.lower() for p in prompts)

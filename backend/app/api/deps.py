"""API dependency wiring — the repository composition root (NFR-8 seam).

The read routers depend on the :class:`FamilyRepository` *interface* via
:func:`get_repository`; they never name a concrete store. v1 binds the in-memory
impl (ASSUMPTIONS A-3), hydrated once at import from the synthetic generator.
Going to production = rebinding :data:`_repository` to a Supabase-backed impl
(or overriding the dependency) with zero router/core changes.
"""

from __future__ import annotations

from app.data.repository import FamilyRepository, InMemoryFamilyRepository

# Singleton store, seeded once at process start from the fixed synthetic seed
# (A-3). Production swaps this for a Supabase-backed FamilyRepository.
_repository: FamilyRepository = InMemoryFamilyRepository.seeded()


def get_repository() -> FamilyRepository:
    """FastAPI dependency yielding the active repository (the store seam)."""
    return _repository

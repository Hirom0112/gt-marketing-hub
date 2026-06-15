"""API dependency wiring — the repository composition root (NFR-8 seam).

The read routers depend on the :class:`FamilyRepository` *interface* via
:func:`get_repository`; they never name a concrete store. v1 binds the in-memory
impl (ASSUMPTIONS A-3), hydrated once at import from the synthetic generator.
Going to production = rebinding :data:`_repository` to a Supabase-backed impl
(or overriding the dependency) with zero router/core changes.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import Params, load_params
from app.data.repository import FamilyRepository, InMemoryFamilyRepository

# Singleton store, seeded once at process start from the fixed synthetic seed
# (A-3). Production swaps this for a Supabase-backed FamilyRepository.
_repository: FamilyRepository = InMemoryFamilyRepository.seeded()

# The committed example params, used as a fallback when no local params.yaml
# exists (it is gitignored and absent in this env). Resolved relative to the
# repo root: backend/app/api/deps.py → parents[3] is the repo root.
_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _load_params_with_fallback() -> Params:
    """Load params from the canonical path, falling back to the committed example.

    `load_params()` resolves `PARAMS_PATH` → `params/params.yaml`, which does not
    exist in this build env (gitignored). When that file is absent we fall back
    to the committed `params/params.example.yaml` so both the app and the tests
    run without a local params.yaml — same values either way (INV-11).
    """
    try:
        return load_params()
    except FileNotFoundError:
        return load_params(_EXAMPLE_PARAMS)


# Singleton params, resolved once at import (like _repository).
_params: Params = _load_params_with_fallback()


def get_repository() -> FamilyRepository:
    """FastAPI dependency yielding the active repository (the store seam)."""
    return _repository


def get_params() -> Params:
    """FastAPI dependency yielding the active typed params (§8; INV-11)."""
    return _params

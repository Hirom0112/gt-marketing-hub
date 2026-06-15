"""API dependency wiring — the repository composition root (NFR-8 seam).

The read routers depend on the :class:`FamilyRepository` *interface* via
:func:`get_repository`; they never name a concrete store. v1 binds the in-memory
impl (ASSUMPTIONS A-3), hydrated once at import from the synthetic generator.
Going to production = rebinding :data:`_repository` to a Supabase-backed impl
(or overriding the dependency) with zero router/core changes.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters import registry
from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.ai.client import AnthropicLLMClient, LLMClient
from app.core.eval_gate import BrandJudge
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.observability.log_store import InMemoryObservabilityLog, ObservabilityLog

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

# Singleton env snapshot, read once at import (TECH_STACK §5; INV-11). Tests that
# need a different env override `get_settings_dep`; named so it never clashes with
# `app.core.settings.get_settings` (a fresh-read helper, not the cached seam).
_settings: Settings = Settings.from_env()

# Singleton observability log — the A-3 in-memory NFR-6 audit store. Production
# swaps a Supabase-backed `ObservabilityLog` behind the same interface (the seam
# pattern as `_repository`). Held in a one-slot list so `reset_observability_log`
# can rebind it for test isolation without a `global` statement.
_observability: list[ObservabilityLog] = [InMemoryObservabilityLog()]


def get_repository() -> FamilyRepository:
    """FastAPI dependency yielding the active repository (the store seam)."""
    return _repository


def get_params() -> Params:
    """FastAPI dependency yielding the active typed params (§8; INV-11)."""
    return _params


def get_settings_dep() -> Settings:
    """FastAPI dependency yielding the cached env snapshot (the §5 env seam).

    Distinct name from `app.core.settings.get_settings` (which re-reads the env
    each call): this is the composition-layer singleton, read once at import.
    """
    return _settings


def get_observability_log() -> ObservabilityLog:
    """FastAPI dependency yielding the active NFR-6 audit log (the A-3 store seam)."""
    return _observability[0]


def reset_observability_log() -> None:
    """Rebind the in-memory observability singleton (test isolation only).

    The audit log is append-only by design; tests need a fresh store per case so
    proposal ids do not collide. Production never calls this.
    """
    _observability[0] = InMemoryObservabilityLog()


def get_llm_client() -> LLMClient:
    """FastAPI dependency yielding the gated Anthropic edge client (the AI seam).

    Built over the cached settings. With no key (or kill switch) the client
    degrades to the deterministic template WITHOUT a live call (INV-8, NFR-5);
    tests override this with a fake-transport client so no live call ever runs.
    """
    return AnthropicLLMClient(settings=_settings)


def get_brand_judge() -> BrandJudge | None:
    """FastAPI dependency yielding the V-4 brand judge, or None (fail-closed).

    INV-4 / §9.4: without a live judge the gate must DENY V-4, never silently
    pass. There is no live LLM judge wired in v1, so this returns ``None`` —
    which makes V-4 deny — both when the LLM is unavailable AND (for now) when it
    is available. The dependency EXISTS so tests can override it with a
    deterministic judge; wiring the real judge is the only change here later.
    """
    if not _settings.llm_available:
        return None
    # TODO(S7): wire the live LLM brand judge to `_settings` here. Until then we
    # return None even with a key so V-4 fails closed (INV-4) rather than passing
    # an un-judged message — the seam is the point, not the impl.
    return None


def get_crm_adapter_dep() -> CRMAdapter:
    """FastAPI dependency yielding the CRM adapter for the active SEND_MODE (INV-9).

    Delegates to the §7 registry (v1 ⇒ a simulated recorder; live ⇒ fail-loud).
    Tests override this with a known `SimulatedCRMAdapter` to inspect `sent_log`.
    """
    return registry.get_crm_adapter()

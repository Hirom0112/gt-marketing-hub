"""API dependency wiring — the repository composition root (NFR-8 seam).

The read routers depend on the :class:`FamilyRepository` *interface* via
:func:`get_repository`; they never name a concrete store. v1 binds the in-memory
impl (ASSUMPTIONS A-3), hydrated once at import from the synthetic generator.
Going to production = rebinding :data:`_repository` to a Supabase-backed impl
(or overriding the dependency) with zero router/core changes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID

from app.adapters import registry
from app.adapters.brand_memory.base import BrandMemoryStore
from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
from app.adapters.funding.base import FundingSignalAdapter
from app.adapters.geo_sampling.base import GeoSamplingAdapter
from app.adapters.hubspot.crm_adapter import CRMAdapter
from app.adapters.media.base import MediaGenAdapter
from app.adapters.sentiment.base import SentimentAdapter
from app.adapters.social.base import SocialAdapter
from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.schemas.brand import BrandRule
from app.core.eval_gate import BrandJudge
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.data.notes_repository import InMemoryNotesRepository, NotesRepository
from app.data.repository import FamilyRepository, InMemoryFamilyRepository
from app.evals.suite import EvalSuiteResult
from app.marketing.library import ContentLibrary, InMemoryContentLibrary
from app.observability.log_store import InMemoryObservabilityLog, ObservabilityLog

# Singleton store, seeded once at process start from the fixed synthetic seed
# (A-3). Production swaps this for a Supabase-backed FamilyRepository.
_repository: FamilyRepository = InMemoryFamilyRepository.seeded()

# Singleton notes store — the FR-2.3 timeline (A-3 in-memory, append-only).
# Production swaps a Supabase-backed NotesRepository behind the same interface.
_notes_repository: NotesRepository = InMemoryNotesRepository()

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

# How many of the seeded demo families read as "followed_up" — a deterministic
# handful get a seeded approve-decision so the situation bar shows a believable
# light-green slice alongside fresh/overdue/closed (A-14: recency is DERIVED from
# the audit log, never a stored column). ~15% of the slimmed demo cohort.
_FOLLOWED_UP_SEED_COUNT = 4


def _seed_followed_up_contacts(log: ObservabilityLog) -> None:
    """Seed a few approved outbounds so a handful of demo families read followed_up.

    Recency is derived from the audit log (A-14): a family with an APPROVE
    decision has a non-None ``last_contact_at`` and so colors FOLLOWED_UP. We
    pick the first ``_FOLLOWED_UP_SEED_COUNT`` non-funded, otherwise-overdue
    families (stable repo order ⇒ deterministic) and log one approved outbound
    each, dated two days after the family was created — a realistic "we already
    reached out" contact. This is the only writer to the demo log; tests that need
    a clean log call ``reset_observability_log`` (which rebinds to an empty store).
    """
    from app.data.models import FundingState
    from app.observability.log_store import DecisionAction

    seeded = 0
    for joined in _repository.list_joined():
        if seeded >= _FOLLOWED_UP_SEED_COUNT:
            break
        family = joined.family
        if family.funding_state is FundingState.FUNDED:
            continue  # funded ⇒ CLOSED; leave it for the closed slice.
        created = family.created_at
        if created is None:
            continue
        # A deterministic id per family so re-seeding is idempotent in shape.
        proposal_id = UUID(int=(family.family_id.int ^ 0xF0110_3ED) & ((1 << 128) - 1), version=4)
        contacted_at = created + timedelta(days=2)
        log.log_proposal(
            proposal_id=proposal_id,
            flow="enrollment_draft",
            schema_version="1",
            payload={"action": "email", "body": "Following up on your GT School application."},
            family_id=family.family_id,
            created_at=created,
        )
        log.log_decision(
            proposal_id=proposal_id,
            human="seed-operator",
            action=DecisionAction.APPROVE,
            created_at=contacted_at,
        )
        seeded += 1


def _build_observability_log() -> ObservabilityLog:
    """Build the demo audit log, pre-seeded with a few followed_up contacts (A-14)."""
    log = InMemoryObservabilityLog()
    _seed_followed_up_contacts(log)
    return log


# Singleton observability log — the A-3 in-memory NFR-6 audit store. Production
# swaps a Supabase-backed `ObservabilityLog` behind the same interface (the seam
# pattern as `_repository`). Held in a one-slot list so `reset_observability_log`
# can rebind it for test isolation without a `global` statement. Seeded with a
# few approved outbounds so the demo situation bar shows a followed_up slice.
_observability: list[ObservabilityLog] = [_build_observability_log()]

# Singleton consolidated eval-suite verdict (FR-4.5) — the last `run_suite(...)`
# result, or `None` until a suite has run. Held in a one-slot list (the same
# pattern as `_observability`) so `set_eval_state`/`reset_eval_state` can rebind
# it without a `global` statement. This is the LIVE suite-level kill seam: a red
# row disables the gated action in the running app, fail-closed (INV-3).
_eval_state: list[EvalSuiteResult | None] = [None]


def _build_brand_memory_store() -> BrandMemoryStore:
    """Construct the seeded brand-memory store with the params weight_step (INV-11).

    Closes the INV-11 wiring gap the conditioning agent flagged: the SQLite store
    still DEFAULTS `weight_step` in code, so the composition root passes
    `params.brand_memory.weight_step` explicitly here — the single canonical home
    flows through affirm/weaken. The store is seeded once from the §11.1 synthetic
    brand-memory inventory (the only seed writer, NFR-1) so S4 generation is
    conditioned and demoable on synthetic data alone. v1 uses a temp-file SQLite
    backing (A-3/A-11; persistence is per-process here, swapped for Postgres in
    prod). Imported lazily to keep this module's import graph thin.
    """
    import tempfile
    from pathlib import Path

    from app.data.synthetic import generate_brand_memory

    db_path = Path(tempfile.gettempdir()) / "gt_cockpit_brand_memory.sqlite3"
    # Fresh file each process start so the singleton is deterministic from the seed.
    db_path.unlink(missing_ok=True)
    store = SqliteBrandMemoryStore(db_path, weight_step=_params.brand_memory.weight_step)
    for item in generate_brand_memory():
        store.upsert(item)
    return store


# Singleton brand-memory store (FR-3.2) — seeded + params-wired weight_step. One-slot
# list so `reset_brand_memory_store` can rebind for test isolation.
_brand_memory_store: list[BrandMemoryStore] = [_build_brand_memory_store()]

# Singleton content library (FR-3.4) — seeded from the §11.4 kept+validated assets.
# One-slot list so `reset_content_library` can rebind for test isolation.
_content_library: list[ContentLibrary] = [InMemoryContentLibrary.seeded()]


def _build_brand_rules() -> list[BrandRule]:
    """The §11.2 active brand-rule seed inventory (the only seed writer, NFR-1)."""
    from app.data.synthetic import generate_brand_rules

    return list(generate_brand_rules())


# Singleton active brand rules (§8.4) — the §11.2 seed inventory; ACTIVE `never`
# rules add absolute V-4 blocking phrases in the gate (A-10).
_brand_rules: list[BrandRule] = _build_brand_rules()


def get_repository() -> FamilyRepository:
    """FastAPI dependency yielding the active repository (the store seam)."""
    return _repository


def get_notes_repository() -> NotesRepository:
    """FastAPI dependency yielding the active notes store (the FR-2.3 timeline seam)."""
    return _notes_repository


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


def get_eval_state() -> EvalSuiteResult | None:
    """FastAPI dependency yielding the last consolidated suite verdict (FR-4.5).

    The suite-level kill seam (INV-3): ``None`` until ``POST /evals/run`` has run
    a suite; thereafter the latest :class:`EvalSuiteResult`. The fail-closed gate
    (``app.core.eval_gate.action_enabled``) reads this to disable a gated action
    when its row went red — in the LIVE path, not just the UI.
    """
    return _eval_state[0]


def set_eval_state(result: EvalSuiteResult | None) -> None:
    """Rebind the consolidated suite verdict (the ``POST /evals/run`` write seam)."""
    _eval_state[0] = result


def reset_eval_state() -> None:
    """Clear the consolidated suite verdict back to ``None`` (test isolation only)."""
    _eval_state[0] = None


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


def get_funding_signal_adapter_dep() -> FundingSignalAdapter:
    """FastAPI dependency yielding the §7.2 GT-controlled signal adapter (INV-10).

    Delegates to the §7 registry (v1 ⇒ a simulated synthetic source; live ⇒
    fail-loud). The signal is GT-controlled — GT-confirmed enrollment, a
    first-installment receipt, the family's self-report — NOT an Odyssey/TEFA
    feed (INV-10; none exists). Tests override this to inject a known signal.
    """
    return registry.get_funding_signal_adapter()


def get_geo_sampling_adapter_dep() -> GeoSamplingAdapter:
    """FastAPI dependency yielding the §7.6 GEO sampling adapter (INV-9; FR-3.7/4.4).

    Delegates to the §7 registry (v1 ⇒ a simulated, offline source; live ⇒
    fail-loud). GEO coverage is sampled by **repeated, variance-reported** runs of
    an AI engine's citations (CONTENT_SPEC §7.4); live polling of real engines is
    OUT in v1, so under ``SEND_MODE='simulate'`` this returns the simulated impl —
    no live engine call ever happens here (INV-9). Tests override this to inject a
    known sampling stream (e.g. the insufficient-samples fail-closed path).
    """
    return registry.get_geo_sampling_adapter()


def get_sentiment_adapter_dep() -> SentimentAdapter:
    """FastAPI dependency yielding the §7.5 sentiment-feed adapter (INV-6/INV-9).

    Delegates to the §7 registry (v1 ⇒ a placeholder, aggregate-only source over
    synthetic data, ``source_mode='placeholder'``; live ⇒ fail-loud). The summary
    is AGGREGATE only — no per-person or child-keyed field (INV-6) — and no live
    feed is ever polled (INV-9). Tests override this to inject a known summary.
    """
    return registry.get_sentiment_adapter()


def get_social_adapter_dep() -> SocialAdapter:
    """FastAPI dependency yielding the §7.4 social-posting adapter (INV-9).

    Delegates to the §7 registry (v1 ⇒ a simulated, backend-held queue with
    simulated receipts; live ⇒ fail-loud). Every v1 dispatch is SIMULATED — a
    live send is never performed here (INV-9, OUT-2). Tests override this.
    """
    return registry.get_social_adapter()


def get_media_gen_adapter_dep() -> MediaGenAdapter:
    """FastAPI dependency yielding the §7.3 media-gen adapter (INV-9, OUT-1).

    Delegates to the §7 registry (v1 ⇒ a placeholder, $0-spend stub; live ⇒
    fail-loud). No live media is generated in v1 (OUT-1). Tests override this.
    """
    return registry.get_media_gen_adapter()


def get_brand_memory_store_dep() -> BrandMemoryStore:
    """FastAPI dependency yielding the seeded brand-memory store (FR-3.2).

    The store's affirm/weaken honor `params.brand_memory.weight_step` because the
    singleton was constructed with it (INV-11 — the single param home). Tests
    override this to inject an isolated store.
    """
    return _brand_memory_store[0]


def get_content_library_dep() -> ContentLibrary:
    """FastAPI dependency yielding the seeded content library (FR-3.4)."""
    return _content_library[0]


def get_active_brand_rules() -> list[BrandRule]:
    """FastAPI dependency yielding the §8.4 active brand rules (V-4 never-rules)."""
    return _brand_rules


def reset_brand_memory_store() -> None:
    """Rebind the brand-memory singleton to a fresh seeded store (test isolation)."""
    _brand_memory_store[0] = _build_brand_memory_store()


def reset_content_library() -> None:
    """Rebind the content-library singleton to a fresh seeded library (test isolation)."""
    _content_library[0] = InMemoryContentLibrary.seeded()

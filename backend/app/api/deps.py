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
from app.marketing.library import ContentLibrary, SqliteContentLibrary
from app.observability.log_store import InMemoryObservabilityLog, ObservabilityLog


def _build_repository(params: Params) -> FamilyRepository:
    """Seed the in-memory store, honoring the ``COCKPIT_SCENARIO`` toggle (A-21).

    Default (no/blank/``june`` env) ⇒ the unchanged 24-family June demo cohort —
    so the June-anchored count/recency fixtures and the 404 tests are untouched.
    ``COCKPIT_SCENARIO=back_to_school`` ⇒ the SEPARATE deterministic volume cohort
    (``generate_back_to_school``). ``COCKPIT_SCENARIO=realistic`` ⇒ the
    cadence-calibrated cohort (``generate_realistic``): its dismiss-target ids are
    recorded in ``_realistic_dismissed_family_ids`` so the observability log can
    seed the matching dismiss events (the dismissed slice). Every cohort is sized
    entirely from params (INV-11), so the running app can serve any scenario with
    no fixture churn. One composition-root env read; no change to the repository
    seam, Settings registry, or any router.
    """
    import os

    from app.data.synthetic import generate_back_to_school, generate_realistic

    scenario = (os.environ.get("COCKPIT_SCENARIO", "") or "").strip().lower()
    if scenario == "back_to_school":
        bts = params.back_to_school
        dataset = generate_back_to_school(
            count=bts.count,
            seed=bts.seed,
            spike_year=bts.spike_year,
            spike_month=bts.spike_month,
            spike_day=bts.spike_day,
            spike_share=bts.spike_share,
            spread_days=bts.spread_days,
        )
        return InMemoryFamilyRepository(dataset)
    if scenario == "realistic":
        cohort = generate_realistic(params=params.realistic)
        # Record the dismiss targets so the observability log can seed the dismiss
        # events for the same families (the dismissed slice of History).
        _realistic_dismissed_family_ids.clear()
        _realistic_dismissed_family_ids.extend(cohort.dismissed_family_ids)
        return InMemoryFamilyRepository(cohort.dataset)
    return InMemoryFamilyRepository.seeded()


# Dismiss-target ids from the realistic cohort (empty under other scenarios). The
# observability log seeds one dismiss event per id so those families derive
# ``dismissed`` (A-19) — recency/state are DERIVED from the log, never stored.
_realistic_dismissed_family_ids: list[UUID] = []


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


# Singleton params, resolved once at import (the cohort toggle reads from here).
_params: Params = _load_params_with_fallback()

# Singleton store, seeded once at process start (A-3). Default = the June demo
# cohort; ``COCKPIT_SCENARIO=back_to_school`` swaps the volume cohort (A-21).
# Production swaps this for a Supabase-backed FamilyRepository.
_repository: FamilyRepository = _build_repository(_params)

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


def _seed_realistic_dismissals(log: ObservabilityLog) -> None:
    """Log one dismiss event per realistic-cohort dismiss target (A-19).

    No-op under the default/back_to_school scenarios (the id list is empty). Each
    event is dated at the demo now so it post-dates the family's recent stall and
    holds (not superseded). This is what makes the History/dismissed slice
    non-empty under ``COCKPIT_SCENARIO=realistic``.
    """
    from datetime import UTC, datetime

    if not _realistic_dismissed_family_ids:
        return
    dismissed_at = datetime(2026, 6, 15, tzinfo=UTC)  # the demo now (synthetic _EPOCH)
    for family_id in _realistic_dismissed_family_ids:
        log.log_dismiss(
            family_id=family_id,
            human="seed-operator",
            reason="Family went quiet and was set aside during recovery triage (synthetic seed).",
            created_at=dismissed_at,
        )


def _build_observability_log() -> ObservabilityLog:
    """Build the demo audit log, pre-seeded with followed_up contacts + dismissals (A-14/A-19)."""
    log = InMemoryObservabilityLog()
    _seed_followed_up_contacts(log)
    _seed_realistic_dismissals(log)
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

    from app.ai.schemas.brand import BrandMemoryKind
    from app.data.library_ingest import load_brand_memory_exemplars
    from app.data.synthetic import generate_brand_memory

    db_path = Path(tempfile.gettempdir()) / "gt_cockpit_brand_memory.sqlite3"
    # Fresh file each process start so the singleton is deterministic from the seed.
    db_path.unlink(missing_ok=True)
    store = SqliteBrandMemoryStore(db_path, weight_step=_params.brand_memory.weight_step)

    # Phase-1 marketing: seed exemplars from GT's OWN proven captions (the
    # distilled, V-2/V-3-filtered, IMPORT-provenance library), so brand memory
    # conditions generation on real winning hooks instead of synthetic stand-ins.
    imported = load_brand_memory_exemplars(_params)
    if imported:
        for item in imported:
            store.upsert(item)
        # The catalog has no RULE items, but the §9 gate demo needs the two named
        # dont_rules ("no speed multipliers" / "don't target children"). Keep just
        # those non-exemplar rules from the synthetic seed (NOT its exemplars,
        # which the imported real ones replace).
        for item in generate_brand_memory():
            if item.kind is not BrandMemoryKind.EXEMPLAR:
                store.upsert(item)
    else:
        # Graceful fallback: no committed seed (default dev / fresh checkout) ⇒
        # the synthetic generator keeps the store seeded and existing tests green.
        for item in generate_brand_memory():
            store.upsert(item)
    return store


# Singleton brand-memory store (FR-3.2) — seeded + params-wired weight_step. One-slot
# list so `reset_brand_memory_store` can rebind for test isolation.
_brand_memory_store: list[BrandMemoryStore] = [_build_brand_memory_store()]


def _build_content_library() -> ContentLibrary:
    """Construct the seeded PERSISTENT content library (FR-3.4, D-8, A-11).

    Mirrors :func:`_build_brand_memory_store`: the library is server-side
    persistent (a kept asset survives a restart, D-8), not in-memory — so the v1
    impl is the stdlib-``sqlite3``-backed :class:`SqliteContentLibrary` over a
    temp-file path (no Postgres in this env, A-3/A-11; Postgres in prod). The file
    is removed on each process start so the singleton is deterministic from the
    seed (imported real assets, falling back to the §11.4 synthetic inventory).
    """
    import tempfile
    from pathlib import Path

    db_path = Path(tempfile.gettempdir()) / "gt_cockpit_content_library.sqlite3"
    db_path.unlink(missing_ok=True)
    return SqliteContentLibrary.seeded(db_path)


# Singleton content library (FR-3.4) — seeded from the §11.4 kept+validated assets,
# PERSISTED to a temp-file sqlite path (D-8). One-slot list so
# `reset_content_library` can rebind for test isolation.
_content_library: list[ContentLibrary] = [_build_content_library()]


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
    """FastAPI dependency yielding the V-4 brand judge (a proposal — INV-2).

    The real :class:`app.ai.brand_judge.BrandJudge` is wired here: LLM-backed when
    ``_settings.llm_available`` (scored via the gated edge client, no live call
    under test), HEURISTIC otherwise — a DETERMINISTIC offline conformance score so
    dev/no-key content can pass V-4 on genuinely on-brand copy WITHOUT a live call,
    while off-brand / banned copy scores below the params floor (never a silent
    pass; INV-8/§9.4 fail-closed posture).

    The judge is INJECTED into the gate, never imported by ``app/core/`` (purity,
    INV-2). The gate's V-1/V-2/V-3 still block banned patterns regardless of this
    judge (INV-4), and the no-judge-at-all path (``brand_judge=None``) still denies
    V-4 — that seam is preserved; this dependency simply always supplies a judge
    now. Tests override this with their own deterministic judge.
    """
    from app.ai.brand_judge import BrandJudge as _RealBrandJudge

    client = get_llm_client() if _settings.llm_available else None
    return _RealBrandJudge(settings=_settings, params=_params, client=client)


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
    """Rebind the content-library singleton to a fresh seeded library (test isolation).

    Rebuilds the PERSISTENT sqlite library (a fresh temp-file from the seed), the
    same seam pattern as :func:`reset_brand_memory_store`.
    """
    _content_library[0] = _build_content_library()

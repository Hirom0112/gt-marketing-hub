"""API dependency wiring — the repository composition root (NFR-8 seam).

The read routers depend on the :class:`FamilyRepository` *interface* via
:func:`get_repository`; they never name a concrete store. v1 binds the in-memory
impl (ASSUMPTIONS A-3), hydrated once at import from the synthetic generator.
Going to production = rebinding :data:`_repository` to a Supabase-backed impl
(or overriding the dependency) with zero router/core changes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from app.adapters import registry
from app.adapters.brand_memory.base import BrandMemoryStore
from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
from app.adapters.funding.base import FundingSignalAdapter
from app.adapters.geo_sampling.base import GeoSamplingAdapter
from app.adapters.hubspot.crm_adapter import CRMAdapter, SimulatedCRMAdapter
from app.adapters.media.base import MediaGenAdapter
from app.adapters.payments.base import PaymentsAdapter
from app.adapters.sentiment.base import SentimentAdapter
from app.adapters.social.base import SocialAdapter
from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.schemas.brand import BrandRule
from app.core.eval_gate import BrandJudge
from app.core.jwt_verify import JwtError, verify_hs256
from app.core.params import Params, load_params
from app.core.program import Program, resolve_program
from app.core.sales_agents import lookup as lookup_sales_agent
from app.core.seam import MirrorState
from app.core.settings import Settings
from app.data.notes_repository import InMemoryNotesRepository, NotesRepository
from app.data.payments_store import (
    InMemoryPaymentsStore,
    PaymentsStore,
    build_supabase_payments_store,
)
from app.data.repository import (
    UNASSIGNED,
    FamilyRepository,
    InMemoryFamilyRepository,
    OwnerScope,
)
from app.data.supabase_repository import build_supabase_repository
from app.data.watermark_store import (
    InMemoryWatermarkStore,
    WatermarkStore,
    build_supabase_watermark_store,
)
from app.evals.suite import EvalSuiteResult
from app.marketing.library import ContentLibrary, SqliteContentLibrary
from app.observability.log_store import InMemoryObservabilityLog, ObservabilityLog
from app.observability.security_log import (
    InMemorySecurityEventLog,
    SecurityEventLog,
    seed_simulated_feed,
)


def _build_repository(params: Params) -> FamilyRepository:
    """Bind the data source the cockpit reads, honoring the COCKPIT_REPO override.

    ``COCKPIT_REPO`` (TECH_STACK §5.1) is the explicit data-source selector — it
    chooses WHICH :class:`FamilyRepository` is bound, never changing either repo's
    behavior (doctrine-neutral). It is read through :class:`Settings` and applied
    BEFORE the A-24 M5 default so sourcing the full ``.env`` (HubSpot token /
    Anthropic key / gallery path) can no longer silently bind the empty cloud
    Supabase:

    - ``synthetic`` ⇒ FORCE the in-memory cohort (``_build_in_memory_repository``),
      NEVER Supabase even when ``SUPABASE_URL`` is set.
    - ``supabase`` ⇒ REQUIRE the live repo. ``build_supabase_repository`` returning
      ``None`` (no/blank ``SUPABASE_URL``) is a misconfig ⇒ raise ``RuntimeError``
      (fail loud — the CRM-adapter posture), NOT a silent in-memory fallback.
    - ``auto`` (the default) ⇒ the UNCHANGED A-24 M5 single source of truth: a
      configured ``SUPABASE_URL`` (+ service_role key) binds the LIVE
      :class:`SupabaseFamilyRepository` (query-per-request, server-only
      service_role, stage DERIVED on read) and skips in-memory seeding; with no
      Supabase credential the in-memory impl stays the v1 fallback (A-3).

    The in-memory cohort honors ``COCKPIT_SCENARIO`` exactly as before (the June
    demo default / ``back_to_school`` / ``realistic`` cohorts) — see
    :func:`_build_in_memory_repository`. One composition-root read; no change to the
    repository seam, the router layer, or the synthetic cohorts themselves.
    """
    settings = Settings.from_env()
    repo_mode = settings.cockpit_repo
    # A1: resolve the active program fail-closed (an unknown GT_PROGRAM_ID raises at
    # boot — never a silent default; A1 fail-closed posture). Threaded into the live
    # Supabase repo so every program-scoped read/write is bounded to this program,
    # the app-layer isolation over the service_role read path (PLAN_v2 §A1 / A-38).
    program = resolve_program(settings.gt_program_id)

    if repo_mode == "synthetic":
        return _build_in_memory_repository(params)

    if repo_mode == "supabase":
        # Fail loud on misconfig — the operator asked for the live repo explicitly,
        # so a missing/blank SUPABASE_URL is an error, NOT a silent synthetic boot.
        supabase = build_supabase_repository(params, program=program)
        if supabase is None:
            raise RuntimeError(
                "COCKPIT_REPO=supabase requires SUPABASE_URL (+ "
                "SUPABASE_SERVICE_ROLE_KEY); none was configured. Set them, or use "
                "COCKPIT_REPO=synthetic (force in-memory) / COCKPIT_REPO=auto (default)."
            )
        return supabase

    # auto (default): A-24 M5 single source of truth — Supabase when configured,
    # else the in-memory v1 fallback.
    supabase = build_supabase_repository(params, program=program)
    if supabase is not None:
        return supabase
    return _build_in_memory_repository(params)


def _build_in_memory_repository(params: Params) -> FamilyRepository:
    """Seed the in-memory synthetic cohort, honoring ``COCKPIT_SCENARIO`` (A-3/A-21).

    Default (no/blank/``june`` env) ⇒ the unchanged 24-family June demo cohort —
    so the June-anchored count/recency fixtures and the 404 tests are untouched.
    ``COCKPIT_SCENARIO=back_to_school`` ⇒ the SEPARATE deterministic volume cohort
    (``generate_back_to_school``). ``COCKPIT_SCENARIO=realistic`` ⇒ the
    cadence-calibrated cohort (``generate_realistic``): its dismiss-target ids are
    recorded in ``_realistic_dismissed_family_ids`` so the observability log can
    seed the matching dismiss events (the dismissed slice).
    ``COCKPIT_SCENARIO=demo`` ⇒ the curated on-camera demo cohort
    (``generate_demo_cohort``, MULTI_AGENT §10.1): a small, hand-shaped fixture of
    8–10 households with controlled, legible state. Every cohort is sized entirely
    from params (INV-11).
    """
    import os

    from app.data.synthetic import (
        generate_back_to_school,
        generate_demo_cohort,
        generate_realistic,
    )

    scenario = (os.environ.get("COCKPIT_SCENARIO", "") or "").strip().lower()
    if scenario == "demo":
        # MD — the curated on-camera demo cohort (MULTI_AGENT §10.1): a small,
        # hand-shaped, deterministic fixture seeded into the in-memory repo (the
        # gate path). The LIVE-Supabase seed (clear-slate + each family a synthetic
        # anon-session user) is the director's live-step, NOT built here.
        dataset = generate_demo_cohort(params=params)
        return InMemoryFamilyRepository(dataset, params=params)
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
        return InMemoryFamilyRepository(dataset, params=params)
    if scenario == "realistic":
        cohort = generate_realistic(params=params.realistic)
        # Record the dismiss targets so the observability log can seed the dismiss
        # events for the same families (the dismissed slice of History).
        _realistic_dismissed_family_ids.clear()
        _realistic_dismissed_family_ids.extend(cohort.dismissed_family_ids)
        return InMemoryFamilyRepository(cohort.dataset, params=params)
    return InMemoryFamilyRepository.seeded(params=params)


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


def _build_watermark_store() -> WatermarkStore:
    """Bind the A2 CRM-poll watermark store, MIRRORING ``_build_repository``'s mode.

    The same ``COCKPIT_REPO`` / ``SUPABASE_URL`` selection as the family store, so
    the two never disagree on which backend is live:

    - ``synthetic`` ⇒ FORCE the in-memory store (never Supabase).
    - ``supabase`` ⇒ REQUIRE the live store; a missing ``SUPABASE_URL`` is a
      misconfig ⇒ raise (fail loud, the family-store posture).
    - ``auto`` (default) ⇒ Supabase when ``SUPABASE_URL`` is configured, else the
      in-memory v1 fallback (A-3).
    """
    repo_mode = Settings.from_env().cockpit_repo
    if repo_mode == "synthetic":
        return InMemoryWatermarkStore()
    if repo_mode == "supabase":
        supabase = build_supabase_watermark_store()
        if supabase is None:
            raise RuntimeError(
                "COCKPIT_REPO=supabase requires SUPABASE_URL (+ "
                "SUPABASE_SERVICE_ROLE_KEY) for the CRM-sync watermark store; none "
                "was configured. Set them, or use COCKPIT_REPO=synthetic / auto."
            )
        return supabase
    return build_supabase_watermark_store() or InMemoryWatermarkStore()


# Singleton CRM-poll watermark store (A2) — the durable per-program incremental-poll
# state behind the same NFR-8 seam as ``_repository``. Default v1 = in-memory (A-3);
# production swaps the Supabase-backed impl over the 0025 table.
_watermark_store: WatermarkStore = _build_watermark_store()


def _build_payments_store() -> PaymentsStore:
    """Bind the A3 Stripe dedupe + payment ledger store, MIRRORING ``_build_repository``.

    The same ``COCKPIT_REPO`` / ``SUPABASE_URL`` selection as the family and watermark
    stores, so the three never disagree on which backend is live (the NFR-8 store seam):

    - ``synthetic`` ⇒ FORCE the in-memory store (never Supabase).
    - ``supabase`` ⇒ REQUIRE the live store; a missing ``SUPABASE_URL`` is a misconfig
      ⇒ raise (fail loud, the family-store posture).
    - ``auto`` (default) ⇒ Supabase when ``SUPABASE_URL`` is configured, else the
      in-memory v1 fallback (A-3). Default CI is :class:`InMemoryPaymentsStore`.
    """
    repo_mode = Settings.from_env().cockpit_repo
    if repo_mode == "synthetic":
        return InMemoryPaymentsStore()
    if repo_mode == "supabase":
        supabase = build_supabase_payments_store()
        if supabase is None:
            raise RuntimeError(
                "COCKPIT_REPO=supabase requires SUPABASE_URL (+ "
                "SUPABASE_SERVICE_ROLE_KEY) for the Stripe payments store; none was "
                "configured. Set them, or use COCKPIT_REPO=synthetic / auto."
            )
        return supabase
    return build_supabase_payments_store() or InMemoryPaymentsStore()


# Singleton Stripe payments store (A3) — the durable per-program dedupe (stripe_events)
# + payment money ledger behind the same NFR-8 seam as ``_repository``. Default v1 =
# in-memory (A-3); production swaps the Supabase-backed impl over the 0026 tables.
_payments_store: PaymentsStore = _build_payments_store()

# Singleton env snapshot, read once at import (TECH_STACK §5; INV-11). Tests that
# need a different env override `get_settings_dep`; named so it never clashes with
# `app.core.settings.get_settings` (a fresh-read helper, not the cached seam).
_settings: Settings = Settings.from_env()

# Singleton active program (A1), resolved ONCE at import from GT_PROGRAM_ID via the
# fail-closed `resolve_program` (an unknown token raises at boot — never a silent
# default; A1). This is DEPLOYMENT config, NEVER a client header (A-37): it is read
# from the env seam, not from any request. It bounds the live Supabase repo to the
# active program (the app-layer isolation, threaded in `_build_repository`); routers
# consume it via `get_active_program` (B1 widens this to the auth-claim path).
_active_program: Program = resolve_program(_settings.gt_program_id)

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


def _build_simulated_crm_adapter() -> SimulatedCRMAdapter:
    """Build the demo simulated CRM adapter, seeding its mirror per family (R1; §7.1).

    The §4.7 seam compares the DB record to the CRM mirror; a fresh
    :class:`SimulatedCRMAdapter` mirror is empty (push-rebuilt), so the demo seeds
    it from each seeded family's ``crm_seam_status`` column — reproducing, in the
    REAL adapter mirror, the divergence the deriver would read. This is what keeps
    ``GET /seam`` and ``POST /seam/{id}/reconcile`` demoable end-to-end on
    synthetic data once the seam endpoint reads the adapter (not a fabricated
    mirror). The seeded mirror shape per seeded status:

    - ``synced``   — the mirror mirrors local exactly (stage + funding_state +
      owner at the same instant) ⇒ ``synced``.
    - ``unsynced`` — an empty mirror (nothing pushed) ⇒ ``unsynced`` ⇒ a
      ``push_local`` reconcile.
    - ``conflict`` — a tracked stage that diverges from local at an equal instant
      (no clear winner) ⇒ a genuine §4.7 ``conflict`` ⇒ ``flag_conflict``.

    INV-9: pure in-memory seeding (``seed_mirror``), no network, no live call.
    """
    from app.data.models import SeamStatus, Stage

    adapter = SimulatedCRMAdapter()
    for record in _repository.list_families():
        family_owner = None if record.user_id is None else str(record.user_id)
        if record.crm_seam_status is SeamStatus.CONFLICT:
            # A tracked stage that differs from local, at an equal instant ⇒ no
            # clear winner ⇒ a genuine §4.7 conflict (flag_conflict).
            diverging_stage = next(stage for stage in Stage if stage != record.current_stage)
            adapter.seed_mirror(
                record.family_id,
                MirrorState(
                    stage=diverging_stage,
                    mirror_updated_at=record.updated_at,
                    funding_state=record.funding_state,
                    owner=family_owner,
                ),
            )
        elif record.crm_seam_status is SeamStatus.UNSYNCED:
            # Nothing pushed ⇒ leave the mirror empty (the default), which the
            # deriver reads as unsynced (local changes unpushed) ⇒ push_local.
            continue
        else:  # SYNCED — the mirror reflects local across every tracked field.
            adapter.seed_mirror(
                record.family_id,
                MirrorState(
                    stage=record.current_stage,
                    mirror_updated_at=record.updated_at,
                    funding_state=record.funding_state,
                    owner=family_owner,
                ),
            )
    return adapter


# Singleton observability log — the A-3 in-memory NFR-6 audit store. Production
# swaps a Supabase-backed `ObservabilityLog` behind the same interface (the seam
# pattern as `_repository`). Held in a one-slot list so `reset_observability_log`
# can rebind it for test isolation without a `global` statement. Seeded with a
# few approved outbounds so the demo situation bar shows a followed_up slice.
_observability: list[ObservabilityLog] = [_build_observability_log()]

# Singleton demo CRM adapter for the SIMULATE path — a SimulatedCRMAdapter whose
# mirror is pre-seeded per family (R1) so the §4.7 seam endpoints read a populated
# mirror and stay demoable on synthetic data. Held in a one-slot list so
# `reset_crm_adapter` can rebind it for test isolation (the `_observability`
# pattern). The LIVE path is unaffected: `get_crm_adapter_dep` still delegates to
# the registry whenever the effective CRM mode is live (CRM_MODE=live + token + no
# kill switch). Built lazily from `_simulated_crm_adapter()` so it tracks any
# repository rebind/reseed a test performed before first use.
_simulated_crm: list[SimulatedCRMAdapter | None] = [None]


def _simulated_crm_adapter() -> SimulatedCRMAdapter:
    """The seeded demo simulated CRM adapter, built once on first use (R1; §7.1)."""
    adapter = _simulated_crm[0]
    if adapter is None:
        adapter = _build_simulated_crm_adapter()
        _simulated_crm[0] = adapter
    return adapter


def reset_crm_adapter() -> None:
    """Rebind the demo simulated CRM adapter to a fresh seeded one (test isolation)."""
    _simulated_crm[0] = None


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


def _build_content_library(*, fresh: bool = False) -> ContentLibrary:
    """Construct the seeded PERSISTENT content library (FR-3.4, D-8, A-11).

    The library is server-side persistent — a kept asset survives a restart (D-8)
    — so the v1 impl is the stdlib-``sqlite3``-backed :class:`SqliteContentLibrary`
    over a temp-file path (no Postgres in this env, A-3/A-11; Postgres in prod).

    Unlike :func:`_build_brand_memory_store` (which is re-seeded deterministically
    each boot and holds no operator-authored state, so it unlinks its file), the
    library MUST NOT delete its backing file on a normal start: the operator's PAST
    kept assets live there and have to survive a cockpit restart (D-8). The seed is
    re-applied on top via :meth:`SqliteContentLibrary.seeded`, whose ``add`` is an
    idempotent upsert on ``id`` — so re-seeding a populated file is a no-op in
    shape (the deterministic seed rows are unchanged) while previously-kept rows
    are preserved. This is the fix for the "nor past content" defect: the prior
    unconditional ``unlink`` wiped every kept asset on every boot.

    ``fresh=True`` is the TEST-ONLY path (:func:`reset_content_library`): it unlinks
    the backing file first so each test starts from a clean seed with no cross-test
    leakage. The path is derived from :func:`tempfile.gettempdir`, which the test
    harness (``tests/conftest.py``) redirects to a per-SESSION temp dir so test
    processes never share the one fixed file. Sharing the single fixed path was the
    root of a gate flake: a ``fresh=True`` unlink could clobber a live store and a
    fresh connection then saw a tableless file → "no such table" (now also
    self-healed in :meth:`SqliteContentLibrary._connect`). Production
    (``fresh=False``) always uses the STABLE persistent path so the operator's past
    kept assets survive a cockpit restart (D-8); the production path is never
    randomized.
    """
    import tempfile
    from pathlib import Path

    db_path = Path(tempfile.gettempdir()) / "gt_cockpit_content_library.sqlite3"
    if fresh:
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


def get_watermark_store() -> WatermarkStore:
    """FastAPI dependency yielding the active CRM-poll watermark store (A2 seam)."""
    return _watermark_store


def get_payments_adapter_dep() -> PaymentsAdapter:
    """FastAPI dependency yielding the Stripe payments adapter (A3; INV-8/INV-9).

    Delegates to the §7 registry (v1 ⇒ a simulated recorder that still verifies
    webhooks offline; live ⇒ the Stripe adapter behind the cap + kill switch, or its
    fail-loud misconfig). Mirrors :func:`get_crm_adapter_dep`. Tests override this with
    a `SimulatedPaymentsAdapter` constructed with a known webhook secret.
    """
    return registry.get_payments_adapter()


def get_payments_store() -> PaymentsStore:
    """FastAPI dependency yielding the active Stripe payments store (A3 seam)."""
    return _payments_store


# ===========================================================================
# M1 owner scope — the app-layer stand-in for auth.uid() (the IDOR atonement).
# MULTI_AGENT_COCKPIT §4: every owner-scoped read clamps its scope HERE, keyed off
# the VERIFIED principal's role (B1). It scopes reads at the repository/app layer
# and MUST NEVER grant RLS bypass or touch service_role (D-RLS-4) — an app-layer
# scope, not a DB role. The spoofable client-supplied role header that used to feed
# this was DELETED (the security audit's top finding, S1).
# ===========================================================================

# The fail-closed scope for an operator principal with no resolvable agent_id: a nil
# UUID that no real ``assigned_rep_id`` ever equals, so the rep sees ZERO rows
# (never the unassigned pool, never another rep's book). A malformed operator request
# must read nothing — deny-by-default (INV-5).
_NIL_AGENT_ID = UUID(int=0)


def resolve_owner_scope(principal: Principal, requested_owner: str | None) -> OwnerScope:
    """Clamp the client-requested owner against the verified principal's authority (§4, §6).

    The SINGLE security chokepoint (DRY): every owner-scoped read route resolves
    its effective scope here, so the IDOR clamp is enforced identically everywhere.
    The verified ROLE — never the client-supplied ``owner`` — decides:

    - ``role=operator`` ⇒ ALWAYS the principal's own ``agent_id`` (its ``OwnerScope``),
      regardless of what ``owner`` the client passed (``all`` / another agent's id
      are IGNORED — the IDOR defense). An operator with no resolved id clamps to the
      nil-uuid sentinel (:data:`_NIL_AGENT_ID`) ⇒ ZERO rows (fail-closed: never the
      unassigned pool, never a foreign book). This is the old ``agent`` behavior.
    - ``role=leader`` / ``role=admin`` ⇒ honor ``requested_owner``: ``None``/``"all"``
      ⇒ everything; ``"none"`` ⇒ the unassigned pool (:data:`UNASSIGNED`); a uuid
      string ⇒ that agent's book. An unparseable owner is treated as ``all`` (no
      narrowing). This is the old ``admin`` cross-agent behavior, now shared by the
      leadership lens.

    Returns the typed :data:`OwnerScope` the repository read consumes.
    """
    if principal.role == "operator":
        # The clamp: an operator is ALWAYS scoped to its own book. A principal with no
        # resolved agent_id owns nothing → the nil-uuid sentinel matches no real
        # assigned_rep_id (fail-closed; it can never read the pool OR a foreign book).
        return principal.agent_id if principal.agent_id is not None else _NIL_AGENT_ID

    # leader / admin: honor the requested owner (the cross-agent slice).
    requested = (requested_owner or "").strip()
    if requested == "" or requested.lower() == "all":
        return None
    if requested.lower() == UNASSIGNED:
        return UNASSIGNED
    try:
        return UUID(requested)
    except ValueError:
        return None


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


def get_active_program() -> Program:
    """FastAPI dependency yielding the active :class:`Program` (A1 — the program seam).

    Resolved fail-closed from ``GT_PROGRAM_ID`` at import (an unknown token raised
    at boot), it is DEPLOYMENT config and is NEVER taken from a client header (A-37)
    — exactly like :func:`get_settings_dep`, this returns the composition-layer
    singleton. It is the active ``program_id`` the app-layer isolation stamps/filters
    on; B1's auth rewrite widens program resolution to the JWT ``app_metadata`` claim
    once the app connects via the non-``BYPASSRLS`` ``app_runtime`` role (A-38).
    """
    return _active_program


# ===========================================================================
# B1 verified-identity principal — the SIGNED successor to the deleted demo header.
# This REPLACES the spoofable client-supplied role header (the security audit's top
# finding, S1). The role is taken ONLY from the verified JWT's `app_metadata`
# (server-controlled in Supabase), NEVER `user_metadata` (client-writable;
# RESEARCH_v2 §II.5). Default-deny on any missing/forged/expired token. The
# spoofable header and its app-layer principal were DELETED — this is now the SOLE
# identity seam every owner-scoped route consumes.
# ===========================================================================

# The three verified roles (mirrors the 0027_rbac.sql `app_role` enum +
# params.rbac.roles): `admin` (full), `leader` (cross-agent leadership view),
# `operator` (a single rep scoped to its own book). The wire spelling of the
# verified `app_metadata.role` claim — named constants, not tunables (INV-11).
Role = Literal["admin", "leader", "operator"]


class Principal(BaseModel):
    """The VERIFIED principal — derived from a signed Supabase JWT (B1; S1 fix).

    ``role`` is the trusted authority (from ``app_metadata.role`` ONLY). ``user_id``
    is the JWT ``sub`` (the auth user). ``agent_id`` is the operator's rep id (from
    ``app_metadata.agent_id``; ``None`` for admin/leader). ``tier`` is the operator's
    closer/setter tier (resolved via the sales-agent registry; ``None`` otherwise).
    It carries NO db-role/service_role field — scoping is app-layer, never an
    RLS-bypass DB role (D-RLS-4).
    """

    role: Role
    user_id: UUID | None = None
    agent_id: UUID | None = None
    tier: str | None = None


def _parse_uuid(value: object) -> UUID | None:
    """Parse a JWT claim into a UUID, or ``None`` when absent/malformed (fail-soft id)."""
    if not isinstance(value, str):
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        return None


def get_principal(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    """Resolve the VERIFIED principal from a signed ``Authorization: Bearer`` JWT (B1).

    Default-DENY at every step — the S1 fix (no more default-admin from a spoofable
    header):

    - No configured ``supabase_jwt_secret`` ⇒ **401** (fail closed; NEVER
      default-allow when the verifying secret is absent).
    - Missing/blank Authorization header, a non-``Bearer`` scheme, or a
      forged/tampered/expired/malformed token ⇒ **401**.
    - A VALID, unexpired token whose role is absent from ``app_metadata`` (e.g.
      present only in the client-writable ``user_metadata``) or is not one of the
      three roles ⇒ **403** (a real identity, but no trusted authority).

    On success it maps the JWT ``sub`` → ``user_id``, ``app_metadata.agent_id`` →
    ``agent_id`` (operators), and resolves an operator's ``tier`` via the static
    sales-agent registry. The verifier is the stdlib HS256 check; ``now`` is injected
    here (the core stays clock-free). NEVER reads/sets ``service_role`` (D-RLS-4).
    """
    if settings.supabase_jwt_secret is None:
        # Fail closed: no verifying secret ⇒ no token can be trusted (never allow).
        raise HTTPException(status_code=401, detail="JWT verification is not configured")
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="malformed Authorization header")

    now = int(datetime.now(UTC).timestamp())
    try:
        claims = verify_hs256(token.strip(), secret=settings.supabase_jwt_secret, now=now)
    except JwtError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc

    # Trust the role ONLY from app_metadata (server-controlled). A role in
    # user_metadata (client-writable) is IGNORED ⇒ default-deny (403).
    app_metadata = claims.get("app_metadata")
    raw_role = app_metadata.get("role") if isinstance(app_metadata, dict) else None
    if raw_role not in ("admin", "leader", "operator"):
        raise HTTPException(status_code=403, detail="no trusted role in app_metadata")
    role: Role = raw_role  # narrowed to the Role literal by the membership check above

    user_id = _parse_uuid(claims.get("sub"))
    agent_id = _parse_uuid(app_metadata.get("agent_id")) if isinstance(app_metadata, dict) else None
    tier: str | None = None
    if role == "operator" and agent_id is not None:
        agent = lookup_sales_agent(agent_id)
        tier = agent.tier if agent is not None else None
    return Principal(role=role, user_id=user_id, agent_id=agent_id, tier=tier)


def require_role(*roles: Role) -> Callable[[Principal], Principal]:
    """Dependency FACTORY: gate a route on the verified principal's role (B1).

    Returns a FastAPI dependency that resolves the verified :class:`Principal` (via
    :func:`get_principal`) and raises ``HTTPException(403)`` unless its role is one
    of ``roles``. (B2's leader-gated routes consume this.) A 401 from
    :func:`get_principal` (no/forged/expired token) propagates unchanged.
    """

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=403, detail="role not permitted for this resource")
        return principal

    return dependency


def actor_principal(authorization: str | None, settings: Settings) -> Principal | None:
    """Best-effort verified principal for the DETECTION edge middleware (B1) — NEVER raises.

    The §7 security middleware cannot use FastAPI ``Depends`` (it runs at the ASGI
    edge), so it calls this to derive the actor identity from the raw
    ``Authorization`` header. It reuses the SAME verification as :func:`get_principal`
    but swallows every failure into ``None`` (no token / no configured secret /
    forged / expired / no trusted role) — the middleware classifies ``None`` as ANON.
    Detection-only and fail-soft by design: it never blocks a request and never
    raises. NEVER reads/sets ``service_role`` (D-RLS-4).
    """
    try:
        return get_principal(settings=settings, authorization=authorization)
    except HTTPException:
        return None


# Singleton security-event feed (M7 Panel B) — the append-only suspicious-signal
# audit log, pre-seeded with the v1 SIMULATED stream (INV-9, labeled). One-slot
# list so `reset_security_event_log` can rebind it for test isolation (the
# `_observability` pattern). The edge middleware records observed signals here
# server-side (never client-exposed; INV-5). Production swaps a Supabase-backed
# impl behind the same interface.
def _build_security_event_log() -> SecurityEventLog:
    """Build the demo security-event feed, pre-seeded with the simulated stream (INV-9)."""
    log = InMemorySecurityEventLog()
    seed_simulated_feed(log)
    return log


_security_event_log: list[SecurityEventLog] = [_build_security_event_log()]


def get_observability_log() -> ObservabilityLog:
    """FastAPI dependency yielding the active NFR-6 audit log (the A-3 store seam)."""
    return _observability[0]


def get_security_event_log() -> SecurityEventLog:
    """FastAPI dependency yielding the active M7 security-event feed (Panel B seam)."""
    return _security_event_log[0]


def reset_security_event_log() -> None:
    """Rebind the security-event feed to a fresh seeded one (test isolation only)."""
    _security_event_log[0] = _build_security_event_log()


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

    Built over the cached settings + params. The params carry the §6.1 pricing
    rates the client uses to charge each live call its real USD (INV-8, INV-11);
    they are passed explicitly (the fallback-resolved singleton) so the client
    never relies on `params/params.yaml` being on the cwd. With no key (or kill
    switch) the client degrades to the deterministic template WITHOUT a live call
    (NFR-5); tests override this with a fake-transport client so no live call runs.
    """
    return AnthropicLLMClient(settings=_settings, params=_params)


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


def get_seam_crm_adapter_dep() -> CRMAdapter:
    """FastAPI dependency yielding the CRM adapter the §4.7 SEAM endpoints read (R1).

    The seam endpoints (``GET /seam`` + ``POST /seam/{id}/reconcile``) reconcile
    the DB record against the CRM mirror, so they need a mirror with real
    multi-field data — not the fresh, empty recorder a per-request
    ``registry.get_crm_adapter()`` hands back (its mirror is push-rebuilt and so
    starts empty every request). The §7 registry owns the simulate-vs-live
    precedence (:func:`app.adapters.registry.effective_crm_mode`):

    - ``simulate`` (the v1 default, or a kill-switched live) ⇒ the process-wide
      SEEDED demo :class:`SimulatedCRMAdapter` (mirror pre-populated per family,
      R1) so the seam stays demoable on synthetic data.
    - ``live`` ⇒ delegate to ``registry.get_crm_adapter()`` (the live HubSpot
      adapter, or its fail-loud misconfig) — the seam then reconciles DB truth
      against the real portal mirror.

    Other routers (enrollment push, ai_actions send, publish) keep
    :func:`get_crm_adapter_dep` unchanged — their fresh-per-request recorder is
    correct for a write-then-read in one request; only the seam needs the seeded,
    persistent mirror. Tests override this with their own seeded adapter.
    """
    if registry.effective_crm_mode(_settings) == "simulate":
        return _simulated_crm_adapter()
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

    Rebuilds the PERSISTENT sqlite library from a CLEAN file (``fresh=True``
    unlinks first) so tests get an isolated seed with no cross-test leakage — the
    same seam pattern as :func:`reset_brand_memory_store`. Production never calls
    this; the boot path keeps the file so past kept assets survive (D-8).
    """
    _content_library[0] = _build_content_library(fresh=True)

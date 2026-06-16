"""Data-access repository — the NFR-8 store seam (ASSUMPTIONS.md A-3).

Defines the :class:`FamilyRepository` interface every read endpoint depends on,
plus :class:`InMemoryFamilyRepository`, the v1 local impl. The local build has no
Supabase credential (A-3), so the in-memory impl hydrates **once at startup** from
``synthetic.generate(n, seed)`` — the only seed writer (ARCHITECTURE.md §1.3) —
and serves reads from those parallel-keyed lists.

NFR-8 seam: the API and core depend only on the :class:`FamilyRepository`
*interface*. Going to production = supplying a Supabase-backed implementation of
this same interface and swapping it at the composition root — with **zero changes
to `core/`** (and none to the routers either). The shape of every method here is
chosen so a SQL-backed impl is a drop-in: filters map to a ``WHERE`` clause,
``get_family`` to a join, ``pipeline_counts`` to a ``GROUP BY current_stage``.

Purity: this module is plain data access. It imports **no** ``app.ai`` /
``app.adapters`` modules — it depends only on the pure data models and the
synthetic generator, so the deterministic core can sit above it untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from app.core.pipeline import pipeline_counts as core_pipeline_counts
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    LeadsNew,
    SeamStatus,
    Stage,
)
from app.data.synthetic import SyntheticDataset, generate

# Documented seed constants (no magic numbers: CLAUDE.md / ARCHITECTURE §8 intent).
# The local in-memory store is hydrated from this fixed seed so the dashboard is
# deterministic and the API tests can assert exact counts. Production reads a live
# store, not these constants.
DEFAULT_SEED = 42
# A small, curated demo cohort: the situation bar and "show full queue" list stay
# scannable (a 200-row queue read like a broken harness). The generator itself is
# unbounded — `test_scale_5000_families` drives it directly at N=5000 (NFR-9) — so
# this only sizes the local demo seed, not the generator's capability.
DEFAULT_FAMILY_COUNT = 24


@dataclass(frozen=True)
class JoinedFamily:
    """A family spine row joined to its four source rows (FR-2.2, basic deal view).

    Mirrors the §4.1 join: exactly one row per family in each source table, all
    keyed by ``family_id``. The full deal view (notes, funding installments) is S1.
    """

    family: FamilyRecord
    lead: LeadsNew | None
    app_form: AppForm | None
    enrollment_forms: EnrollmentForms | None
    community_profile: CommunityProfile | None


class FamilyRepository(ABC):
    """Read interface over the Family Record spine (the NFR-8 store seam).

    Every read endpoint depends on this interface, never on a concrete store.
    Production swaps a Supabase-backed impl with zero changes to `core/`.
    """

    @abstractmethod
    def list_families(
        self,
        *,
        stage: Stage | None = None,
        funding_state: FundingState | None = None,
        seam_status: SeamStatus | None = None,
    ) -> list[FamilyRecord]:
        """List spine rows, optionally filtered by stage / funding_state / seam_status."""
        raise NotImplementedError

    @abstractmethod
    def get_family(self, family_id: UUID) -> JoinedFamily | None:
        """Return the spine row joined to its four source rows, or None if unknown."""
        raise NotImplementedError

    @abstractmethod
    def list_joined(self) -> list[JoinedFamily]:
        """Every family joined to its four source rows (the work-queue's input).

        The FR-2.5 work-queue scorer needs the ``community_profile`` engagement
        signals (only available via the join, not :meth:`list_families`), so the
        ranking router reads the cohort through this seam. A SQL-backed impl maps
        it to the same join ``list_families`` would, over all rows.
        """
        raise NotImplementedError

    @abstractmethod
    def pipeline_counts(self) -> dict[Stage, int]:
        """Per-stage tally over ``family_record.current_stage`` (FR-2.1)."""
        raise NotImplementedError


class InMemoryFamilyRepository(FamilyRepository):
    """In-memory impl seeded once from ``synthetic.generate`` (A-3).

    Builds ``family_id`` → source-row indexes at construction so ``get_family``
    is O(1) and ``list_families`` is a single pass. This is the v1 local store;
    production replaces it with a Supabase-backed :class:`FamilyRepository`.
    """

    def __init__(self, dataset: SyntheticDataset) -> None:
        self._families: list[FamilyRecord] = list(dataset.families)
        # family_id → spine row, so get_family is genuinely O(1) and list_joined
        # is a single O(n) pass (not N linear scans — the O(n²) that made
        # /work-queue + /enrollment/calendar take ~1s on the 5k cohort).
        self._family_index: dict[UUID, FamilyRecord] = {f.family_id: f for f in self._families}
        self._leads: dict[UUID, LeadsNew] = {row.family_id: row for row in dataset.leads}
        self._app_forms: dict[UUID, AppForm] = {row.family_id: row for row in dataset.app_forms}
        self._enrollment_forms: dict[UUID, EnrollmentForms] = {
            row.family_id: row for row in dataset.enrollment_forms
        }
        self._community_profiles: dict[UUID, CommunityProfile] = {
            row.family_id: row for row in dataset.community_profiles
        }

    @classmethod
    def seeded(
        cls,
        *,
        n: int = DEFAULT_FAMILY_COUNT,
        seed: int = DEFAULT_SEED,
    ) -> InMemoryFamilyRepository:
        """Hydrate the store from the synthetic generator (the only seed writer)."""
        return cls(generate(n=n, seed=seed))

    def list_families(
        self,
        *,
        stage: Stage | None = None,
        funding_state: FundingState | None = None,
        seam_status: SeamStatus | None = None,
    ) -> list[FamilyRecord]:
        return [
            family
            for family in self._families
            if (stage is None or family.current_stage == stage)
            and (funding_state is None or family.funding_state == funding_state)
            and (seam_status is None or family.crm_seam_status == seam_status)
        ]

    def _assemble(self, family: FamilyRecord) -> JoinedFamily:
        """Join a spine row to its four source rows via the O(1) indexes."""
        fid = family.family_id
        return JoinedFamily(
            family=family,
            lead=self._leads.get(fid),
            app_form=self._app_forms.get(fid),
            enrollment_forms=self._enrollment_forms.get(fid),
            community_profile=self._community_profiles.get(fid),
        )

    def get_family(self, family_id: UUID) -> JoinedFamily | None:
        family = self._family_index.get(family_id)  # O(1) — no list scan
        if family is None:
            return None
        return self._assemble(family)

    def list_joined(self) -> list[JoinedFamily]:
        # One JoinedFamily per spine row, in stored order. A single O(n) pass —
        # each spine row joined via the O(1) source indexes (no per-family scan).
        return [self._assemble(family) for family in self._families]

    def pipeline_counts(self) -> dict[Stage, int]:
        # Delegate to the pure core counter (FR-2.1): the counting contract lives
        # in `core/pipeline.py` so it is defined once. A SQL-backed store maps the
        # same contract to a `GROUP BY current_stage`.
        return core_pipeline_counts(self._families)

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
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from app.core.params import Params, load_params
from app.core.pipeline import pipeline_counts as core_pipeline_counts
from app.core.stage_machine import FamilyInputs, derive_stage
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    LeadAssignment,
    LeadsNew,
    SeamStatus,
    Stage,
    Student,
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

# The committed example params, the fallback when no local `params/params.yaml`
# exists (it is gitignored / absent in the build + test env). Mirrors the same
# fallback the composition root uses (`app.api.deps`) so a bare-constructed store
# can still derive stages without a local params.yaml — same values either way
# (INV-11). `backend/app/data/repository.py` → `parents[3]` is the repo root.
_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


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


@dataclass(frozen=True)
class JoinedStudent:
    """One child's funnel joined to its OWN app/enrollment + parent household (A-24).

    The per-child analog of :class:`JoinedFamily`: a :class:`Student` joined to
    its own application + enrollment packet (keyed by ``student_id`` — one
    application per child) plus its parent ``family`` and that household's lead +
    community_profile (engagement is a household-level aggregate, shared across
    its children). The board ranks/render these grouped by ``student.family_id``.
    """

    student: Student
    family: FamilyRecord
    lead: LeadsNew | None
    app_form: AppForm | None
    enrollment_forms: EnrollmentForms | None
    community_profile: CommunityProfile | None


@dataclass(frozen=True)
class HouseholdChildStage:
    """One child's DERIVED funnel position within a household roll-up (TODO.md R1).

    The per-child cell of :class:`HouseholdRollUp`: the child's ``student_id``,
    its display label, and the stage DERIVED on read (A-24 M2) from the child's
    own ``app_form`` / ``enrollment_forms`` — never the stored placeholder.
    """

    student_id: UUID
    display_label: str
    stage: Stage


@dataclass(frozen=True)
class HouseholdRollUp:
    """One household's children rolled up to a single row (TODO.md R1).

    Children are grouped by household — keyed by the household's
    ``family_record.user_id`` (the household identity key; ``None`` for a
    server-only / unowned household, kept as its own group). ``family_id`` is the
    household spine's id. ``children`` lists each child's DERIVED stage; the
    ``worst_stage`` rollup is the LEAST-advanced child stage (the household's
    weakest link — the one most in need of attention). Pure derivation (no LLM /
    no write), the per-child analog of the family read (A-24 M2).
    """

    user_id: UUID | None
    family_id: UUID
    children: tuple[HouseholdChildStage, ...]
    worst_stage: Stage


# ---------------------------------------------------------------------------
# Owner scope — the M1 server-side deal-ownership filter (the IDOR atonement).
# ---------------------------------------------------------------------------
# The sentinel for "the unassigned pool" (``assigned_rep_id IS NULL``) — the intake
# desk's view. A named constant, not a magic string (INV-11 spirit): it is the wire
# spelling of ``owner=none`` AND the typed in-repo value. Kept distinct from
# ``None`` (which means "no owner filter at all" — the admin's see-everyone view).
UNASSIGNED: Literal["none"] = "none"

# The typed owner filter passed to ``list_families``:
#   * ``None``        ⇒ NO filter — every row (the admin's default see-all).
#   * ``"none"``      ⇒ only ``assigned_rep_id IS NULL`` (the unassigned pool).
#   * ``UUID``        ⇒ only rows assigned to that agent (rep self-scope / admin slice).
# The role-driven CLAMP (an agent always resolves to its own id) happens ABOVE this
# layer, in the api ``resolve_owner_scope`` chokepoint — the repo just applies the
# already-resolved scope, so the store seam stays a dumb, total filter.
OwnerScope = UUID | Literal["none"] | None


def _matches_owner(family: FamilyRecord, owner: OwnerScope) -> bool:
    """Whether ``family`` passes the owner scope (the shared predicate, both stores).

    ``None`` ⇒ everything; ``"none"`` ⇒ only unassigned (``assigned_rep_id`` is
    ``None``); a ``UUID`` ⇒ only rows assigned to that agent. Defined once so the
    in-memory and Supabase impls scope identically.
    """
    if owner is None:
        return True
    if owner == UNASSIGNED:
        return family.assigned_rep_id is None
    return family.assigned_rep_id == owner


# The §4.8 funnel order, least→most advanced. Used to pick a household's
# ``worst_stage`` (its least-advanced child — the weakest link to recover).
_STAGE_ORDER: tuple[Stage, ...] = (Stage.INTEREST, Stage.APPLY, Stage.ENROLL, Stage.TUITION)


def roll_up_households(students: list[JoinedStudent]) -> list[HouseholdRollUp]:
    """Group children by household → one row per household (the shared core logic).

    The single, pure rollup both store impls call (DRY): the Supabase impl
    delegates here, and the in-memory impl re-derives each child's stage onto the
    student first then calls here — so the two stores produce byte-identical
    rollups. Pure: no I/O, no write (INV-2). Each child's stage is read straight
    off ``js.student.current_stage`` — the caller is responsible for that field
    already being the DERIVED stage (A-24 M2), which both impls guarantee.

    Households are keyed by ``family_record.user_id`` (the household identity key,
    TODO.md R1); a ``None``-owner falls back to its own ``family_id`` so unowned
    households stay separate rather than collapsing into one group. Each row
    carries every child's stage plus a ``worst_stage`` rollup — the household's
    LEAST-advanced child (its weakest link). Deterministic, stable order
    (households by first appearance, children in read order).
    """
    groups: dict[tuple[bool, str, str], list[JoinedStudent]] = {}
    order: list[tuple[bool, str, str]] = []
    for js in students:
        uid = js.family.user_id
        # Group by household: when present, user_id IS the household key (all
        # children sharing it are one household, even across spine rows during
        # the backfill window). A NULL-owner (server-only) household has no
        # user_id, so it falls back to its own family_id — keeping unowned
        # households separate rather than collapsing them into one None group.
        key = (False, str(uid), "") if uid is not None else (True, "", str(js.family.family_id))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(js)

    rollups: list[HouseholdRollUp] = []
    for key in order:
        members = groups[key]
        children = tuple(
            HouseholdChildStage(
                student_id=js.student.student_id,
                display_label=js.student.display_label,
                stage=js.student.current_stage,  # already the DERIVED stage.
            )
            for js in members
        )
        worst = min(
            (c.stage for c in children),
            key=_STAGE_ORDER.index,
        )
        rollups.append(
            HouseholdRollUp(
                user_id=members[0].family.user_id,
                family_id=members[0].family.family_id,
                children=children,
                worst_stage=worst,
            )
        )
    return rollups


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
        owner: OwnerScope = None,
    ) -> list[FamilyRecord]:
        """List spine rows, optionally filtered by stage / funding_state / seam_status.

        ``owner`` is the M1 server-side deal-ownership scope (see :data:`OwnerScope`):
        ``None`` ⇒ every row; ``"none"`` ⇒ the unassigned pool; a ``UUID`` ⇒ one
        agent's book. The role-driven clamp lives above the store (api layer).
        """
        raise NotImplementedError

    @abstractmethod
    def get_family(self, family_id: UUID) -> JoinedFamily | None:
        """Return the spine row joined to its four source rows, or None if unknown."""
        raise NotImplementedError

    @abstractmethod
    def list_joined(self, *, owner: OwnerScope = None) -> list[JoinedFamily]:
        """Every family joined to its four source rows (the work-queue's input).

        The FR-2.5 work-queue scorer needs the ``community_profile`` engagement
        signals (only available via the join, not :meth:`list_families`), so the
        ranking router reads the cohort through this seam. A SQL-backed impl maps
        it to the same join ``list_families`` would, over all rows. ``owner`` is the
        M1 owner scope (see :data:`OwnerScope`), applied identically to
        :meth:`list_families`.
        """
        raise NotImplementedError

    @abstractmethod
    def list_students(self, *, owner: OwnerScope = None) -> list[JoinedStudent]:
        """Every child joined to its own app/enrollment + parent household (A-24).

        The per-child work queue scores STUDENTS, so the board reads the cohort
        through this seam. A SQL-backed impl maps it to a join of ``student`` onto
        its ``app_form``/``enrollment_forms`` (by ``student_id``) and the parent
        ``family_record`` (by ``family_id``). ``owner`` scopes by the PARENT
        household's ``assigned_rep_id`` (the deal owner; see :data:`OwnerScope`).
        """
        raise NotImplementedError

    @abstractmethod
    def pipeline_counts(self) -> dict[Stage, int]:
        """Per-stage tally over ``family_record.current_stage`` (FR-2.1)."""
        raise NotImplementedError

    def household_roll_up(self) -> list[HouseholdRollUp]:
        """Group children by household → one row per household (TODO.md R1).

        Concrete default (not abstract) so the ``GET /households`` route's
        ``getattr`` fallback keeps working for any partial test double, and so the
        real impls can override with the shared :func:`roll_up_households` logic.
        The base seam returns ``[]`` (no children known) — both production stores
        override it. See :func:`roll_up_households` for the grouping contract.
        """
        return []

    # ----------------------------------------------------------- write seam
    # The reconcile flow (FR-2.6) PERSISTS its result through these write methods
    # (TODO.md R1). The deterministic core still owns the *derivation* (INV-2);
    # these only commit the already-approved, already-derived state. A production
    # impl issues a PostgREST PATCH via service_role (server-only — INV-5 /
    # D-RLS-4). Both writes are idempotent: re-applying the same value is a no-op.

    @abstractmethod
    def mark_synced(self, family_id: UUID, synced_at: datetime) -> None:
        """Persist ``crm_synced_at`` for one family (the push_local/accept commit).

        Advances the seam-freshness marker so the family's derived §4.7 status
        reads ``synced`` on the next read. An unknown ``family_id`` is a silent
        no-op (nothing to write).
        """
        raise NotImplementedError

    @abstractmethod
    def apply_field(self, family_id: UUID, field: str, value: object) -> None:
        """Persist one adopted tracked field for the ACCEPT_MIRROR resolution.

        Overwrites ``field`` (e.g. ``current_stage`` / ``funding_state``) on the
        stored record with the mirror-adopted ``value``. An unknown ``family_id``
        is a silent no-op.
        """
        raise NotImplementedError

    @abstractmethod
    def assign_families(
        self, family_ids: list[UUID], agent_id: UUID, assigned_at: datetime
    ) -> list[UUID]:
        """Assign each family to ``agent_id`` — the M4 deterministic write (A-30).

        Sets BOTH ``assigned_rep_id`` and ``assigned_at`` on each known family (the
        owner-authority flip makes ``owner`` DB-authoritative, driven by these two
        columns — ``app/core/seam.py``). The deterministic core owns this write
        (INV-2); it is NEVER an LLM call. Returns the ids actually written (unknown
        ids are skipped — a resilient bulk write, like ``bulk-seed``). Idempotent:
        re-assigning the same agent re-stamps ``assigned_at``.
        """
        raise NotImplementedError

    # --- Lead-assignment seam (LEAD_ASSIGNMENT.md §10). Return-to-pool, the
    # append-only ownership history, and the per-pool round-robin cursor. ---

    def unassign_families(self, family_ids: list[UUID], unassigned_at: datetime) -> list[UUID]:
        """Return families to the intake pool (``assigned_rep_id = NULL``).

        The inverse of :meth:`assign_families` — sets ``assigned_rep_id`` to NULL
        and re-stamps ``assigned_at`` (the SLA-reassign / explicit-transfer path).
        Concrete default raises; both stores override. Unknown ids are skipped.
        """
        raise NotImplementedError

    def append_assignment_event(
        self,
        *,
        family_id: UUID,
        from_rep_id: UUID | None,
        to_rep_id: UUID | None,
        routed_role: str | None,
        assigned_by: str,
        reason: str,
        batch_id: str | None = None,
    ) -> None:
        """Append one row to the immutable ``lead_assignment`` history (§10).

        A who→who/when/why ownership FACT. Append-only (a write, never an
        UPDATE/DELETE); service_role server-side. The deterministic core owns the
        decision (INV-2); this only LOGS it after it happened.
        """
        raise NotImplementedError

    def list_assignments(self, family_id: UUID) -> list[LeadAssignment]:
        """The append-only ownership history for one family, oldest→newest."""
        raise NotImplementedError

    def read_cursors(self) -> dict[str, int]:
        """The persisted per-pool round-robin cursors (``pool_key → next_index``; §7)."""
        raise NotImplementedError

    def write_cursor(self, pool_key: str, next_index: int) -> None:
        """Persist one pool's advanced round-robin cursor (§7)."""
        raise NotImplementedError


class InMemoryFamilyRepository(FamilyRepository):
    """In-memory impl seeded once from ``synthetic.generate`` (A-3).

    Builds ``family_id`` → source-row indexes at construction so ``get_family``
    is O(1) and ``list_families`` is a single pass. This is the v1 local store;
    production replaces it with a Supabase-backed :class:`FamilyRepository`.
    """

    def __init__(self, dataset: SyntheticDataset, *, params: Params | None = None) -> None:
        # `params` is only needed to DERIVE each child's stage on read for the
        # household roll-up (A-24 M2) — kept keyword-only + optional so the many
        # existing call sites (and `.seeded()`) are unchanged. When omitted it is
        # resolved lazily on first use (see `_resolve_params`), never at import, so
        # constructing the store never touches the filesystem.
        self._params: Params | None = params
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
        # A-24 — per-child rows: students + their own app/enrollment indexed by
        # student_id (one application per child).
        self._students: list[Student] = list(dataset.students)
        self._student_app_forms: dict[UUID, AppForm] = {
            row.student_id: row for row in dataset.student_app_forms if row.student_id is not None
        }
        self._student_enrollment_forms: dict[UUID, EnrollmentForms] = {
            row.student_id: row
            for row in dataset.student_enrollment_forms
            if row.student_id is not None
        }
        # Lead-assignment state (LEAD_ASSIGNMENT.md §10): the append-only ownership
        # history + the per-pool round-robin cursors (A-3 in-memory; the Supabase
        # impl persists these to the 0017 tables).
        self._lead_assignments: list[LeadAssignment] = list(dataset.lead_assignments)
        self._cursors: dict[str, int] = {}

    @classmethod
    def seeded(
        cls,
        *,
        n: int = DEFAULT_FAMILY_COUNT,
        seed: int = DEFAULT_SEED,
        params: Params | None = None,
    ) -> InMemoryFamilyRepository:
        """Hydrate the store from the synthetic generator (the only seed writer).

        ``params`` is forwarded for stage derivation (the household roll-up); when
        omitted it is resolved lazily on first use (see :meth:`_resolve_params`).
        """
        return cls(generate(n=n, seed=seed), params=params)

    def list_families(
        self,
        *,
        stage: Stage | None = None,
        funding_state: FundingState | None = None,
        seam_status: SeamStatus | None = None,
        owner: OwnerScope = None,
    ) -> list[FamilyRecord]:
        return [
            family
            for family in self._families
            if (stage is None or family.current_stage == stage)
            and (funding_state is None or family.funding_state == funding_state)
            and (seam_status is None or family.crm_seam_status == seam_status)
            and _matches_owner(family, owner)
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

    def list_joined(self, *, owner: OwnerScope = None) -> list[JoinedFamily]:
        # One JoinedFamily per spine row, in stored order. A single O(n) pass —
        # each spine row joined via the O(1) source indexes (no per-family scan).
        # ``owner`` applies the same M1 deal-ownership scope as ``list_families``.
        return [
            self._assemble(family) for family in self._families if _matches_owner(family, owner)
        ]

    def list_students(self, *, owner: OwnerScope = None) -> list[JoinedStudent]:
        # One JoinedStudent per child, joined via the O(1) indexes: its own app/
        # enrollment (by student_id) + parent household (by family_id). Students
        # whose parent family is absent are skipped (defensive; never happens for
        # generator output, where every student references a real family).
        # ``owner`` scopes by the PARENT household's assigned_rep_id (the deal owner).
        joined: list[JoinedStudent] = []
        for student in self._students:
            family = self._family_index.get(student.family_id)
            if family is None:
                continue
            if not _matches_owner(family, owner):
                continue
            joined.append(
                JoinedStudent(
                    student=student,
                    family=family,
                    lead=self._leads.get(student.family_id),
                    app_form=self._student_app_forms.get(student.student_id),
                    enrollment_forms=self._student_enrollment_forms.get(student.student_id),
                    community_profile=self._community_profiles.get(student.family_id),
                )
            )
        return joined

    def pipeline_counts(self) -> dict[Stage, int]:
        # Delegate to the pure core counter (FR-2.1): the counting contract lives
        # in `core/pipeline.py` so it is defined once. A SQL-backed store maps the
        # same contract to a `GROUP BY current_stage`.
        return core_pipeline_counts(self._families)

    def _resolve_params(self) -> Params:
        """The params for stage derivation, lazily loaded + cached (INV-11).

        The composition root passes the active params in; a bare construction
        (e.g. ``.seeded()`` in a test, or the default demo path) loads them on
        first use — never at import — so the store is cheap to build.
        """
        if self._params is None:
            try:
                self._params = load_params()
            except FileNotFoundError:
                # No local params.yaml (gitignored / absent): fall back to the
                # committed example, exactly as the composition root does.
                self._params = load_params(_EXAMPLE_PARAMS)
        return self._params

    def household_roll_up(self) -> list[HouseholdRollUp]:
        """Group synthetic children by household (TODO.md R1) — see the shared helper.

        Behaviorally identical to the live impl: each child's stage is DERIVED on
        read with the SAME pure stage_machine the family/live path uses (A-24 M2)
        — the synthetic ``student.current_stage`` is a write-time placeholder that
        can disagree with the derived funnel position (an apply-stage child whose
        application is not yet submitted derives to ``interest``). The DERIVED
        stage is written back onto each (frozen) student, then the shared
        :func:`roll_up_households` does the grouping + ``worst_stage`` rollup so
        both stores produce byte-identical rows.
        """
        params = self._resolve_params()
        derived: list[JoinedStudent] = []
        for js in self.list_students():
            stage = derive_stage(
                FamilyInputs(
                    app_form=js.app_form,
                    enrollment_forms=js.enrollment_forms,
                    stalled_since=js.student.stalled_since,
                ),
                params,
            )
            student = js.student.model_copy(update={"current_stage": stage})
            derived.append(
                JoinedStudent(
                    student=student,
                    family=js.family,
                    lead=js.lead,
                    app_form=js.app_form,
                    enrollment_forms=js.enrollment_forms,
                    community_profile=js.community_profile,
                )
            )
        return roll_up_households(derived)

    # ----------------------------------------------------------- write seam
    def _replace(self, family_id: UUID, **update: object) -> None:
        """Write a field-update onto the stored record in BOTH the list and index.

        FamilyRecord is replaced via ``model_copy`` (not mutated in place) so the
        list and the O(1) index stay pointing at the SAME refreshed object — one
        store, no divergence. An unknown id is a silent no-op.
        """
        current = self._family_index.get(family_id)
        if current is None:
            return
        updated = current.model_copy(update=update)
        self._family_index[family_id] = updated
        for i, family in enumerate(self._families):
            if family.family_id == family_id:
                self._families[i] = updated
                break

    def mark_synced(self, family_id: UUID, synced_at: datetime) -> None:
        # Commit the push_local/accept freshness marker onto the in-mem record.
        self._replace(family_id, crm_synced_at=synced_at)

    def apply_field(self, family_id: UUID, field: str, value: object) -> None:
        # Adopt one mirror field (ACCEPT_MIRROR) onto the in-mem record.
        self._replace(family_id, **{field: value})

    def assign_families(
        self, family_ids: list[UUID], agent_id: UUID, assigned_at: datetime
    ) -> list[UUID]:
        # M4 deterministic assignment write (A-30): stamp assigned_rep_id +
        # assigned_at onto each KNOWN family; skip unknown ids (resilient bulk).
        assigned: list[UUID] = []
        for family_id in family_ids:
            if self._family_index.get(family_id) is None:
                continue
            self._replace(family_id, assigned_rep_id=agent_id, assigned_at=assigned_at)
            assigned.append(family_id)
        return assigned

    def unassign_families(self, family_ids: list[UUID], unassigned_at: datetime) -> list[UUID]:
        # Return to the intake pool: NULL assigned_rep_id + re-stamp assigned_at.
        unassigned: list[UUID] = []
        for family_id in family_ids:
            if self._family_index.get(family_id) is None:
                continue
            self._replace(family_id, assigned_rep_id=None, assigned_at=unassigned_at)
            unassigned.append(family_id)
        return unassigned

    def append_assignment_event(
        self,
        *,
        family_id: UUID,
        from_rep_id: UUID | None,
        to_rep_id: UUID | None,
        routed_role: str | None,
        assigned_by: str,
        reason: str,
        batch_id: str | None = None,
    ) -> None:
        # Append one immutable ownership-history fact (LEAD_ASSIGNMENT.md §10).
        self._lead_assignments.append(
            LeadAssignment(
                assignment_id=uuid4(),
                family_id=family_id,
                from_rep_id=from_rep_id,
                to_rep_id=to_rep_id,
                routed_role=routed_role,
                assigned_by=assigned_by,
                reason=reason,
                batch_id=batch_id,
            )
        )

    def list_assignments(self, family_id: UUID) -> list[LeadAssignment]:
        return [e for e in self._lead_assignments if e.family_id == family_id]

    def read_cursors(self) -> dict[str, int]:
        return dict(self._cursors)

    def write_cursor(self, pool_key: str, next_index: int) -> None:
        self._cursors[pool_key] = next_index

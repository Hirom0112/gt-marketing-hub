"""In-memory repository write-seam tests (TODO.md R1 — persist reconcile).

The reconcile flow now PERSISTS its result through the store seam: a
``push_local``/``accept_mirror`` apply writes ``crm_synced_at`` (and, on accept,
the adopted field) back through :class:`FamilyRepository`. These tests prove the
in-memory impl's write methods mutate the stored record so a subsequent read
reflects the advance — the seam endpoint's idempotency fence depends on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.data.models import Stage
from app.data.repository import InMemoryFamilyRepository


def _repo() -> InMemoryFamilyRepository:
    return InMemoryFamilyRepository.seeded()


def test_mark_synced_advances_crm_synced_at_in_store() -> None:
    """mark_synced writes crm_synced_at onto the stored record (read reflects it)."""
    repo = _repo()
    family = next(iter(repo.list_families()))
    when = datetime(2030, 1, 1, tzinfo=UTC)

    repo.mark_synced(family.family_id, when)

    reloaded = repo.get_family(family.family_id)
    assert reloaded is not None
    assert reloaded.family.crm_synced_at == when
    # The list view reads the same mutated record (one store, not a copy).
    listed = next(f for f in repo.list_families() if f.family_id == family.family_id)
    assert listed.crm_synced_at == when


def test_mark_synced_unknown_family_is_noop() -> None:
    """marking an unknown family neither raises nor mutates any record."""
    repo = _repo()
    before = {f.family_id: f.crm_synced_at for f in repo.list_families()}
    repo.mark_synced(uuid4(), datetime(2030, 1, 1, tzinfo=UTC))
    after = {f.family_id: f.crm_synced_at for f in repo.list_families()}
    assert before == after


def test_apply_field_adopts_value_on_store_record() -> None:
    """apply_field overwrites a tracked field on the stored record (ACCEPT_MIRROR)."""
    repo = _repo()
    family = next(iter(repo.list_families()))
    other_stage = next(s for s in Stage if s != family.current_stage)

    repo.apply_field(family.family_id, "current_stage", other_stage)

    reloaded = repo.get_family(family.family_id)
    assert reloaded is not None
    assert reloaded.family.current_stage == other_stage

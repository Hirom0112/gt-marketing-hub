"""Persistent brand-memory store — FR-3.2, TECH_STACK D-8, A-11, INV-5.

Brand memory MUST be server-side **persistent**, not browser localStorage (D-8):
a kept item SURVIVES store re-instantiation. No Postgres in this env (A-3), so
per A-11 the local impl is backed by stdlib ``sqlite3`` (no new dependency) and
the Postgres migration (`0002_brand_memory.sql`) is authored for production.

The defining RED test (`test_kept_item_survives_reinstantiation`) upserts an
item, then opens a BRAND-NEW store against the SAME on-disk path and reads it
back — proving real disk persistence, not an in-memory/localStorage fallback.

The static migration test mirrors `tests/unit/test_migrations_rls.py`: the
`0002_brand_memory.sql` DDL must `ENABLE ROW LEVEL SECURITY` with a null-guarded
policy (deny-by-default, INV-5 / D-RLS-1/2).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.adapters.brand_memory.base import BrandMemoryStore
from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
from app.adapters.registry import get_brand_memory_store
from app.ai.schemas.brand import (
    BrandMemoryItem,
    BrandMemoryKind,
    BrandMemorySignal,
)
from app.ai.schemas.content import Channel, GeneratedBy, Provenance

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "migrations"
    / "0002_brand_memory.sql"
)


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-06-14T00:00:00Z")


def _item(
    item_id: str = "bm-1",
    *,
    weight: float = 1.0,
    active: bool = True,
    version: int = 1,
    channel_scope: list[Channel] | None = None,
) -> BrandMemoryItem:
    return BrandMemoryItem(
        id=item_id,
        kind=BrandMemoryKind.VOICE_ATTRIBUTE,
        content="Warm, plain-spoken, never hypey.",
        weight=weight,
        channel_scope=channel_scope or [],
        active=active,
        version=version,
        provenance=_provenance(),
    )


def test_sqlite_store_is_a_brand_memory_store(tmp_path: Path) -> None:
    """`SqliteBrandMemoryStore` implements the `BrandMemoryStore` ABC."""
    store = SqliteBrandMemoryStore(tmp_path / "bm.db")
    assert isinstance(store, BrandMemoryStore)


def test_kept_item_survives_reinstantiation(tmp_path: Path) -> None:
    """A kept item SURVIVES store re-instantiation — proves persistence (D-8).

    Upsert into a store at a real on-disk path, then build a BRAND-NEW store
    against the SAME path: `get`/`list_active` return the prior item. No
    in-memory/localStorage fallback — the second instance reads from disk.
    """
    db_path = tmp_path / "bm.db"
    item = _item("bm-survives")

    first = SqliteBrandMemoryStore(db_path)
    first.upsert(item)

    # Brand-new instance against the same path — reads prior state from disk.
    second = SqliteBrandMemoryStore(db_path)
    fetched = second.get("bm-survives")
    assert fetched is not None
    assert fetched == item
    assert item in second.list_active()


def test_upsert_returns_and_updates(tmp_path: Path) -> None:
    """`upsert` returns the stored item and a re-upsert replaces it (idempotent id)."""
    store = SqliteBrandMemoryStore(tmp_path / "bm.db")
    returned = store.upsert(_item("bm-x", content_weight := 1.0))  # noqa: F841
    assert returned.id == "bm-x"

    updated = _item("bm-x", weight=2.0)
    store.upsert(updated)
    assert store.get("bm-x") == updated  # replaced, not duplicated
    assert len([i for i in store.list_active() if i.id == "bm-x"]) == 1


def test_get_missing_returns_none(tmp_path: Path) -> None:
    """`get` of an unknown id returns None."""
    store = SqliteBrandMemoryStore(tmp_path / "bm.db")
    assert store.get("nope") is None


def test_affirm_bumps_weight_and_version_persisted(tmp_path: Path) -> None:
    """`affirm` (a keep) bumps weight + version; the bump survives re-instantiation."""
    db_path = tmp_path / "bm.db"
    store = SqliteBrandMemoryStore(db_path)
    store.upsert(_item("bm-a", weight=1.0, version=1))

    affirmed = store.affirm("bm-a", BrandMemorySignal.KEPT)
    assert affirmed.weight > 1.0
    assert affirmed.version == 2
    assert affirmed.signal == BrandMemorySignal.KEPT

    # Survives re-instantiation (persisted, not just returned).
    reopened = SqliteBrandMemoryStore(db_path).get("bm-a")
    assert reopened is not None
    assert reopened.weight == affirmed.weight
    assert reopened.version == 2


def test_weaken_lowers_weight_and_marks_discarded_persisted(tmp_path: Path) -> None:
    """`weaken` (a discard) strengthens a discarded signal; bump survives reopen."""
    db_path = tmp_path / "bm.db"
    store = SqliteBrandMemoryStore(db_path)
    store.upsert(_item("bm-w", weight=2.0, version=1))

    weakened = store.weaken("bm-w", BrandMemorySignal.DISCARDED)
    assert weakened.weight < 2.0
    assert weakened.version == 2
    assert weakened.signal == BrandMemorySignal.DISCARDED

    reopened = SqliteBrandMemoryStore(db_path).get("bm-w")
    assert reopened is not None
    assert reopened.weight == weakened.weight
    assert reopened.version == 2


def test_list_active_filters_inactive_and_by_channel(tmp_path: Path) -> None:
    """`list_active` returns only active items; channel-scope filter works."""
    store = SqliteBrandMemoryStore(tmp_path / "bm.db")

    active_all = _item("bm-active-all", active=True, channel_scope=[])
    active_email = _item("bm-active-email", active=True, channel_scope=[Channel.EMAIL])
    active_ig = _item("bm-active-ig", active=True, channel_scope=[Channel.INSTAGRAM])
    inactive = _item("bm-inactive", active=False, channel_scope=[])
    store.upsert(active_all)
    store.upsert(active_email)
    store.upsert(active_ig)
    store.upsert(inactive)

    # No filter: only active items.
    all_active = store.list_active()
    ids = {i.id for i in all_active}
    assert ids == {"bm-active-all", "bm-active-email", "bm-active-ig"}
    assert inactive not in all_active

    # Channel-scoped: empty-scope (applies to all) + the matching channel.
    email_scoped = store.list_active(channel=Channel.EMAIL.value)
    email_ids = {i.id for i in email_scoped}
    assert email_ids == {"bm-active-all", "bm-active-email"}
    assert "bm-active-ig" not in email_ids


def test_registry_returns_sqlite_store() -> None:
    """`get_brand_memory_store` returns a `SqliteBrandMemoryStore` (A-11 local impl)."""
    store = get_brand_memory_store()
    assert isinstance(store, BrandMemoryStore)
    assert isinstance(store, SqliteBrandMemoryStore)


# ---------------------------------------------------------------------------
# Static migration guard — mirrors tests/unit/test_migrations_rls.py (INV-5).
# ---------------------------------------------------------------------------

_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ENABLE_RLS = re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_CREATE_POLICY = re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE)
_NULL_GUARD = re.compile(r"auth\.uid\(\)\s*\)?\s*IS\s+NOT\s+NULL", re.IGNORECASE)
_SECURITY_DEFINER = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)


def test_migration_has_rls() -> None:
    """`0002_brand_memory.sql` is deny-by-default with a null-guarded policy (INV-5)."""
    sql = _MIGRATION.read_text(encoding="utf-8")

    n_tables = len(_CREATE_TABLE.findall(sql))
    n_rls = len(_ENABLE_RLS.findall(sql))
    assert n_tables > 0, "expected a CREATE TABLE in 0002_brand_memory.sql"
    assert n_tables == n_rls, "every table must ENABLE ROW LEVEL SECURITY (D-RLS-1)"

    assert _CREATE_POLICY.search(sql), "expected at least one CREATE POLICY"
    assert _NULL_GUARD.search(sql), "policy must carry the auth.uid() null guard (D-RLS-2)"
    assert not _SECURITY_DEFINER.search(sql), "no SECURITY DEFINER in exposed schema (D-RLS-7)"

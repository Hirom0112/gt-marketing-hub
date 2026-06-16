"""Persistent content library — a kept asset survives a store rebuild (FR-3.4; D-8).

`InMemoryContentLibrary` loses kept assets on restart. `SqliteContentLibrary`
(mirroring `SqliteBrandMemoryStore`, A-11) is the persistent v1 impl behind the
SAME `ContentLibrary` interface: an asset added against an on-disk path is read
back by a brand-new store instance opened on that path, and search still works.
Stdlib `sqlite3` only — no new dependency. v1 local persistence (Postgres in prod).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import Channel, ContentFormat, GeneratedBy, LifecycleStage, Provenance
from app.marketing.library import SqliteContentLibrary


def _asset(
    *,
    asset_id: str,
    title: str,
    search_text: str,
    tags: list[str],
    lifecycle: LifecycleStage = LifecycleStage.KEPT,
) -> LibraryAsset:
    return LibraryAsset(
        id=asset_id,
        title=title,
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        body="Body text.",
        tags=tags,
        search_text=search_text,
        validation="vr-pass-1",
        lifecycle=lifecycle,
        provenance=Provenance(
            generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-01-01T00:00:00+00:00"
        ),
    )


def test_kept_asset_survives_store_rebuild(tmp_path: Path) -> None:
    """An asset kept via one store instance is read back by a fresh one (D-8)."""
    db_path = tmp_path / "content_library.sqlite3"
    store = SqliteContentLibrary(db_path)
    store.add(
        _asset(
            asset_id="lib-mastery",
            title="Mastery caption",
            search_text="mastery-based gifted k-8 caption",
            tags=["mastery", "k8"],
        )
    )

    # A brand-new instance opened on the SAME path reads the prior asset from disk.
    reopened = SqliteContentLibrary(db_path)
    got = reopened.get("lib-mastery")
    assert got is not None
    assert got.id == "lib-mastery"
    assert got.title == "Mastery caption"


def test_search_works_after_rebuild(tmp_path: Path) -> None:
    """search returns kept+validated assets matching text/tags after a rebuild."""
    db_path = tmp_path / "content_library.sqlite3"
    store = SqliteContentLibrary(db_path)
    store.add(
        _asset(
            asset_id="lib-mastery",
            title="Mastery caption",
            search_text="mastery-based gifted k-8 caption",
            tags=["mastery", "k8"],
        )
    )
    store.add(
        _asset(
            asset_id="lib-funding",
            title="Funding email",
            search_text="tefa funding next steps email",
            tags=["funding"],
        )
    )

    reopened = SqliteContentLibrary(db_path)
    by_text = reopened.search(search_text="mastery")
    assert [a.id for a in by_text] == ["lib-mastery"]
    by_tag = reopened.search(tags=["funding"])
    assert [a.id for a in by_tag] == ["lib-funding"]
    assert len(reopened.search()) == 2


def test_search_excludes_non_kept(tmp_path: Path) -> None:
    """A non-kept asset is never surfaced by search (FR-3.4), even after rebuild."""
    db_path = tmp_path / "content_library.sqlite3"
    store = SqliteContentLibrary(db_path)
    store.add(
        _asset(
            asset_id="lib-review",
            title="In review",
            search_text="some draft in review",
            tags=["draft"],
            lifecycle=LifecycleStage.DRAFT,
        )
    )
    reopened = SqliteContentLibrary(db_path)
    assert reopened.search() == []
    # get() still returns it (storage is faithful); search filters defensively.
    assert reopened.get("lib-review") is not None


def test_add_is_idempotent_on_id(tmp_path: Path) -> None:
    """Re-adding the same id replaces, not duplicates (idempotent upsert)."""
    db_path = tmp_path / "content_library.sqlite3"
    store = SqliteContentLibrary(db_path)
    store.add(_asset(asset_id="lib-x", title="First", search_text="first", tags=[]))
    store.add(_asset(asset_id="lib-x", title="Second", search_text="second", tags=[]))
    reopened = SqliteContentLibrary(db_path)
    got = reopened.get("lib-x")
    assert got is not None
    assert got.title == "Second"
    assert len(reopened.search()) == 1


def test_seeded_loads_inventory(tmp_path: Path) -> None:
    """The seed (imported + synthetic fallback) loads on first build (non-empty)."""
    db_path = tmp_path / "content_library_seeded.sqlite3"
    store = SqliteContentLibrary.seeded(db_path)
    assert len(store.search()) > 0


def test_composition_root_rebuild_preserves_past_kept_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wired singleton (`_build_content_library`) keeps PAST content (D-8; FR-3.4).

    The composition root must mirror the persistence guarantee the
    :class:`SqliteContentLibrary` class already gives: an operator who keeps an
    asset in one session must still see it after the cockpit restarts. This drives
    the actual wiring in `app.api.deps._build_content_library` (not just the class),
    which previously deleted the backing sqlite file on every process start —
    wiping every previously-kept row ("nor past content"). It points the fixed temp
    path at an isolated tmp file so the test is hermetic, builds the library, keeps
    a fresh asset, then rebuilds (simulating a restart) and asserts the kept asset
    survives AND the deterministic seed is still present.
    """
    import tempfile

    from app.api import deps

    # `_build_content_library` calls `tempfile.gettempdir()` (a local import), so
    # patching the stdlib function redirects the fixed db path into our tmp dir.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    first = deps._build_content_library()
    seeded_count = len(first.search())
    assert seeded_count > 0  # the seed shows on a fresh boot

    kept = _asset(
        asset_id="lib-operator-kept-001",
        title="Operator kept this last session",
        search_text="operator kept gifted mastery caption",
        tags=["mastery"],
    )
    first.add(kept)

    # Simulate a cockpit restart: rebuild the singleton against the SAME temp path.
    second = deps._build_content_library()
    survived = second.get("lib-operator-kept-001")
    assert survived is not None, "a previously-kept asset must survive a restart (D-8)"
    assert survived.title == "Operator kept this last session"
    # The deterministic seed is still present too (re-seed is idempotent on id).
    assert any(a.id == "lib-operator-kept-001" for a in second.search())
    assert len(second.search()) == seeded_count + 1

"""Test-isolation regression: the content library never throws "no such table".

A pre-existing order-dependent gate flake: `reset_content_library(fresh=True)`
unlinks the singleton's backing sqlite file, but `SqliteContentLibrary._connect()`
opens a brand-new connection per call — so a connection opened against an
unlinked-then-recreated file saw an EMPTY file with no table and raised
`sqlite3.OperationalError: no such table: content_library`.

This reproduces the two failure modes and asserts both self-heal:
  1. the class is robust — an instance whose backing file is unlinked out from
     under it re-creates its schema on the next operation (belt-and-suspenders);
  2. the composition root isolates the TEST path — `reset_content_library`
     yields a usable library and never leaks "no such table" under repeated reset
     interleavings, while production keeps a STABLE path (D-8 unchanged).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import (
    Channel,
    ContentFormat,
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.api import deps
from app.marketing.library import SqliteContentLibrary


def _asset(asset_id: str = "lib-probe") -> LibraryAsset:
    return LibraryAsset(
        id=asset_id,
        title="Probe",
        asset_type=LibraryAssetType.COPY,
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        body="Body.",
        tags=["mastery"],
        search_text="mastery probe caption",
        validation="vr-pass",
        lifecycle=LifecycleStage.KEPT,
        provenance=Provenance(
            generated_by=GeneratedBy.SYNTHETIC_SEED, created_at="2026-01-01T00:00:00+00:00"
        ),
    )


def test_store_self_heals_when_backing_file_unlinked(tmp_path: Path) -> None:
    """An instance whose backing file is unlinked re-creates its table, not throws.

    This is the exact gate-flake mechanism: a shared backing path is unlinked by
    one test's reset while another holds a live :class:`SqliteContentLibrary`. The
    next ``search``/``add`` opens a fresh connection against the (now missing) file
    — which must self-heal via the idempotent schema, never raise "no such table".
    """
    db_path = tmp_path / "content_library.sqlite3"
    store = SqliteContentLibrary(db_path)
    store.add(_asset())
    assert len(store.search()) == 1

    # Simulate the reset/unlink interleaving: the file is removed out from under
    # the live instance (a *different* code path unlinked the shared path).
    db_path.unlink(missing_ok=True)

    # The next operations must NOT raise "no such table" — the connection re-asserts
    # the idempotent schema and the store keeps working (empty, since the file is new).
    assert store.search() == []
    store.add(_asset("lib-probe-2"))
    assert len(store.search()) == 1


def test_reset_content_library_isolated_and_resilient() -> None:
    """Repeated `reset_content_library` never leaks "no such table" (gate flake).

    Drives the composition root: each reset yields a usable, seeded library, and
    no interleaving of reset + use surfaces a missing-table error. The TEST path is
    isolated from the stable production path so concurrent runs cannot clobber it.
    """
    try:
        for _ in range(3):
            deps.reset_content_library()
            lib = deps.get_content_library_dep()
            # A seeded library is non-empty and search must not throw.
            assert len(lib.search()) > 0
            lib.add(_asset("lib-reset-probe"))
            assert any(a.id == "lib-reset-probe" for a in lib.search())
    finally:
        deps.reset_content_library()


def test_production_path_is_stable_across_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production (`fresh=False`) keeps a STABLE path so kept assets survive (D-8).

    The isolation fix must NOT randomize the production path: a non-fresh rebuild
    must read back the SAME backing file, preserving previously-kept assets.
    """
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    first = deps._build_content_library()
    first.add(_asset("lib-stable-kept"))

    # A non-fresh rebuild (a restart) reads the same file — the kept asset survives.
    second = deps._build_content_library()
    assert second.get("lib-stable-kept") is not None

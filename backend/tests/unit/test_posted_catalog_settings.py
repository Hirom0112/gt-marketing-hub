"""`GT_POSTED_CATALOG_ROOT` env wiring + the static-mount decision (Task 2/3; INV-1).

The REAL posted catalog is read AT RUNTIME from an external, env-configured path — the
scoped INV-1 exception (ASSUMPTIONS). These pin the env seam: unset / empty / a
``<placeholder>`` sentinel ⇒ ``None`` (fall back to the library gallery), a real path ⇒ a
``Path``; and the pure helper that decides whether to mount the static media route (an
existing dir ⇒ mount; missing/unset ⇒ no mount). No real path is set here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings import Settings, posted_catalog_mount_root


def test_unset_catalog_root_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No GT_POSTED_CATALOG_ROOT ⇒ None ⇒ the gallery falls back to the library."""
    monkeypatch.delenv("GT_POSTED_CATALOG_ROOT", raising=False)
    assert Settings.from_env().posted_catalog_root is None


def test_empty_catalog_root_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty value ⇒ None (treated as unset)."""
    monkeypatch.setenv("GT_POSTED_CATALOG_ROOT", "   ")
    assert Settings.from_env().posted_catalog_root is None


def test_placeholder_sentinel_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """The .env.example angle-bracket sentinel ⇒ None (treated as unset)."""
    monkeypatch.setenv("GT_POSTED_CATALOG_ROOT", "<path to the GT scrape root>")
    assert Settings.from_env().posted_catalog_root is None


def test_real_path_is_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A concrete path is read into a Path (existence is the mount helper's concern)."""
    monkeypatch.setenv("GT_POSTED_CATALOG_ROOT", str(tmp_path))
    assert Settings.from_env().posted_catalog_root == tmp_path


def test_mount_root_returns_dir_only_when_it_exists(tmp_path: Path) -> None:
    """The static mount is decided ONLY when the configured root exists (Task 3)."""
    # Unset ⇒ no mount.
    assert posted_catalog_mount_root(Settings(posted_catalog_root=None)) is None
    # Set + exists ⇒ mount that dir.
    assert posted_catalog_mount_root(Settings(posted_catalog_root=tmp_path)) == tmp_path
    # Set but missing ⇒ no mount (graceful — the gallery falls back).
    missing = tmp_path / "does-not-exist"
    assert posted_catalog_mount_root(Settings(posted_catalog_root=missing)) is None


def test_app_mounts_posted_media_only_when_root_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The /posted-media static route is wired into the app only when the root exists."""
    import importlib

    import app.main as main_module

    def _route_names(app_obj: object) -> set[str]:
        return {getattr(r, "name", "") for r in app_obj.routes}  # type: ignore[attr-defined]

    # No env ⇒ no posted-media mount.
    monkeypatch.delenv("GT_POSTED_CATALOG_ROOT", raising=False)
    reloaded = importlib.reload(main_module)
    assert "posted-media" not in _route_names(reloaded.app)

    # Existing root ⇒ the mount is present.
    monkeypatch.setenv("GT_POSTED_CATALOG_ROOT", str(tmp_path))
    reloaded = importlib.reload(main_module)
    assert "posted-media" in _route_names(reloaded.app)

    # Restore the unset module state for the rest of the suite.
    monkeypatch.delenv("GT_POSTED_CATALOG_ROOT", raising=False)
    importlib.reload(main_module)

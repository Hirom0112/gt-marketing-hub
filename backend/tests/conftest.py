"""Shared pytest fixtures — test-isolation for process-shared singletons.

Isolates the content library's backing sqlite file so the suite is deterministic.

`app.api.deps._build_content_library` derives its backing path from
`tempfile.gettempdir()`. With the real temp dir, EVERY test process shares the one
file `gt_cockpit_content_library.sqlite3`; a test's `reset_content_library(fresh=True)`
unlinks it, and an interleaving connection from a live store then saw a tableless
file → `sqlite3.OperationalError: no such table: content_library` (a pre-existing
order-dependent gate flake). Redirecting `gettempdir` to a per-SESSION temp dir
gives each test process its own backing file — no cross-process sharing — while the
PRODUCTION default (a stable path under the real temp dir) is untouched, so the D-8
"kept content survives a restart" guarantee is preserved.

The redirect is also defended in depth by an idempotent `CREATE TABLE IF NOT EXISTS`
on every `SqliteContentLibrary` connection (self-healing), so a reused file recovers
rather than throwing.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_temp_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point `tempfile.gettempdir()` at a per-session temp dir for the whole suite.

    Process-shared file-backed singletons (the content library) thus never collide
    across test processes. pytest owns the dir's lifecycle, so nothing leaks. A
    function-scoped `monkeypatch.setattr(tempfile, "gettempdir", ...)` inside an
    individual test still takes precedence and is restored afterwards.
    """
    session_tmp = tmp_path_factory.mktemp("gt_session_tmp")
    original = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(session_tmp)  # type: ignore[assignment]
    try:
        yield
    finally:
        tempfile.gettempdir = original  # type: ignore[assignment]

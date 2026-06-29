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
from typing import Annotated

import pytest
from fastapi import Header

from tests.api._jwt import TEST_JWT_SECRET

# The STABLE main-app reference the shim overrides on. Captured once (lazily, at the
# first test setup) so it stays pinned to the app object every test module's
# module-level ``client = TestClient(app)`` is bound to — even after a test that does
# ``importlib.reload(app.main)`` (test_cors) swaps ``app.main.app`` to a NEW instance.
# Overriding the swapped attribute would leave the already-bound clients shim-less.
_captured_app: list = []


def _main_app():
    """The pinned main FastAPI app the shim targets (see :data:`_captured_app`)."""
    if not _captured_app:
        from app.main import app

        _captured_app.append(app)
    return _captured_app[0]


def _token_aware_principal(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    """TEST-ONLY ``get_principal`` shim — verify a Bearer token, else default to admin (B1).

    The S1 fix made PRODUCTION ``get_principal`` default-DENY (no token ⇒ 401). The
    suite, however, has dozens of pre-existing tests that hit owner-scoped routes with
    NO auth header and rely on the old admin-default behavior. This shim preserves that
    for the unrelated tests WITHOUT weakening production:

    - a ``Bearer`` token present ⇒ delegate to the REAL :func:`get_principal` (verified
      against :data:`TEST_JWT_SECRET`), so the migrated IDOR tests get true
      operator/admin scoping and forged/expired/bad-role tokens still 401/403; and
    - NO token ⇒ return an ``admin`` principal (the back-compat convenience).

    This lives ONLY here (test conftest). The production default-deny is untouched; the
    S1 regression test pops this override to exercise the real deny path.
    """
    from app.api.deps import Principal, get_principal
    from app.core.settings import Settings

    if authorization and authorization.strip():
        return get_principal(
            settings=Settings(supabase_jwt_secret=TEST_JWT_SECRET),
            authorization=authorization,
        )
    return Principal(role="admin")


def install_test_principal_override(*, settings: bool = True, app=None) -> None:
    """(Re)assert the conftest auth overrides on the app (order-independent).

    Test modules whose own fixtures call ``app.dependency_overrides.clear()`` at SETUP
    wipe the autouse override; they call this right after clearing so the token-aware
    shim is back in place before the request. Overrides ``get_principal`` (the shim)
    and — when ``settings`` is True — ``get_settings_dep`` (real settings + the test
    secret) so the S1 regression, which pops only the principal override, runs the real
    verifier with a configured secret (no-token ⇒ 401, the meaningful default-deny).
    Pass ``settings=False`` from a module that manages its OWN ``get_settings_dep``
    override (e.g. the coworker gate's keyed settings) — the shim verifies tokens with
    :data:`TEST_JWT_SECRET` directly, so it never needs ``get_settings_dep``. ``app``
    defaults to the pinned main app; pass an explicit one for a freshly reloaded app
    (test_cors).
    """
    from app.api import deps

    target = app if app is not None else _main_app()
    if settings:
        target.dependency_overrides[deps.get_settings_dep] = lambda: deps._settings.model_copy(
            update={"supabase_jwt_secret": TEST_JWT_SECRET}
        )
    target.dependency_overrides[deps.get_principal] = _token_aware_principal


@pytest.fixture(autouse=True)
def _verified_principal_shim() -> Iterator[None]:
    """Autouse: install the token-aware principal shim on the main app for every test.

    Keeps the suite green under the S1 default-deny rewrite — owner-scoped routes used
    to read an admin-default demo header; now they read a verified JWT, and this shim
    supplies the same admin-on-no-token convenience for the (many) tests that send no
    auth header. Production ``get_principal`` stays default-DENY.
    """
    install_test_principal_override()
    yield


@pytest.fixture(autouse=True)
def _reset_crm_ops_snapshot_cache() -> Iterator[None]:
    """Autouse: drop the LIVE CRM-Ops snapshot caches before each test (isolation).

    The CRM-Ops parity/overview reads memoize the live snapshot per program for a short
    TTL (``app.api._crm_ops_cache``); without this reset a snapshot computed against one
    test's injected repo/adapter could leak into the next test (same program key). Mirrors
    the ``deps.reset_crm_adapter`` pattern.
    """
    from app.api._crm_ops_cache import reset_crm_ops_cache

    reset_crm_ops_cache()
    yield


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

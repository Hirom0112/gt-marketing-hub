"""`COCKPIT_REPO` override — the explicit data-source selector (deps + settings).

`_build_repository` (A-24 M5) binds the LIVE Supabase store whenever `SUPABASE_URL`
is set, else the in-memory synthetic cohort. That single-source default means
sourcing the full `.env` (for the HubSpot token / Anthropic key / gallery path)
silently binds the empty cloud Supabase and the dashboard reads near-zero. The
`COCKPIT_REPO` override lets the operator source `.env` yet still force the rich
synthetic cohort — without changing either repo's behavior (doctrine-neutral; it
only chooses the data source). Three modes: `synthetic` forces in-memory even with
`SUPABASE_URL` set; `supabase` REQUIRES the live repo (raise on misconfig, fail
loud — mirror the CRM adapter posture); `auto` (default / unset / blank / sentinel)
preserves the current A-24 M5 behavior exactly.

These read the override through `Settings` (the §5 registry mirror) and exercise
`_build_repository` directly, monkeypatching `build_supabase_repository` so no live
cloud is needed.
"""

from __future__ import annotations

import pytest

from app.api import deps
from app.core.params import Params
from app.core.settings import Settings
from app.data.repository import InMemoryFamilyRepository


def _params() -> Params:
    """The committed-example params (no local params.yaml in this env; INV-11)."""
    return deps._load_params_with_fallback()


# --- Settings: the COCKPIT_REPO field ------------------------------------------


def test_cockpit_repo_unset_is_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """No COCKPIT_REPO ⇒ `auto` (the default, current behavior)."""
    monkeypatch.delenv("COCKPIT_REPO", raising=False)
    assert Settings.from_env().cockpit_repo == "auto"


def test_cockpit_repo_blank_is_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty / whitespace value ⇒ `auto` (treated as unset)."""
    monkeypatch.setenv("COCKPIT_REPO", "   ")
    assert Settings.from_env().cockpit_repo == "auto"


def test_cockpit_repo_sentinel_is_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """The .env.example angle-bracket sentinel ⇒ `auto` (treated as unset)."""
    monkeypatch.setenv("COCKPIT_REPO", "<auto | synthetic | supabase>")
    assert Settings.from_env().cockpit_repo == "auto"


def test_cockpit_repo_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """The value is lower-cased so `SYNTHETIC` == `synthetic`."""
    monkeypatch.setenv("COCKPIT_REPO", "SYNTHETIC")
    assert Settings.from_env().cockpit_repo == "synthetic"


# --- deps._build_repository: the three modes -----------------------------------


def test_synthetic_forces_in_memory_even_with_supabase_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 1: COCKPIT_REPO=synthetic + SUPABASE_URL set ⇒ in-memory, NOT Supabase.

    The supabase builder is monkeypatched to a sentinel object; if `synthetic`
    leaked into the Supabase branch we'd get that sentinel back. We assert the
    in-memory type instead — the builder result was ignored.
    """
    monkeypatch.setenv("COCKPIT_REPO", "synthetic")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")
    monkeypatch.delenv("COCKPIT_SCENARIO", raising=False)

    sentinel = object()
    monkeypatch.setattr(deps, "build_supabase_repository", lambda params: sentinel)

    repo = deps._build_repository(_params())
    assert isinstance(repo, InMemoryFamilyRepository)
    assert repo is not sentinel


def test_supabase_required_raises_when_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED 2: COCKPIT_REPO=supabase + no SUPABASE_URL ⇒ RuntimeError (fail loud)."""
    monkeypatch.setenv("COCKPIT_REPO", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    # The real builder returns None with no URL; keep it real so the test pins the
    # actual misconfig path (no monkeypatch on the builder here).
    with pytest.raises(RuntimeError):
        deps._build_repository(_params())


def test_supabase_required_returns_live_when_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """COCKPIT_REPO=supabase + a built repo ⇒ that live repo (never falls back)."""
    monkeypatch.setenv("COCKPIT_REPO", "supabase")
    sentinel = object()
    monkeypatch.setattr(deps, "build_supabase_repository", lambda params: sentinel)
    assert deps._build_repository(_params()) is sentinel


def test_auto_binds_supabase_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED 3a: COCKPIT_REPO unset (auto) + a built repo ⇒ Supabase (A-24 M5 preserved)."""
    monkeypatch.delenv("COCKPIT_REPO", raising=False)
    sentinel = object()
    monkeypatch.setattr(deps, "build_supabase_repository", lambda params: sentinel)
    assert deps._build_repository(_params()) is sentinel


def test_auto_falls_back_to_in_memory_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 3b: COCKPIT_REPO unset (auto) + no Supabase ⇒ in-memory (A-24 M5 preserved)."""
    monkeypatch.delenv("COCKPIT_REPO", raising=False)
    monkeypatch.delenv("COCKPIT_SCENARIO", raising=False)
    monkeypatch.setattr(deps, "build_supabase_repository", lambda params: None)
    repo = deps._build_repository(_params())
    assert isinstance(repo, InMemoryFamilyRepository)

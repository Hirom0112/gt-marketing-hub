"""Store-seam tests for the Module-13 Website store (app.data.website_store).

Covers the deterministic demo seed (shape + idempotency), the write methods (page-flag +
analysis-request create/update), the unknown-id KeyError, and program isolation on the
in-memory store. No I/O (the Supabase impl is exercised only via its construction guard).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.program import Program
from app.data.website_store import InMemoryWebsiteStore, build_supabase_website_store

_PROGRAM = Program.FALL_ENROLLMENT
_OTHER = Program.SUMMER_CAMP


def _seeded() -> InMemoryWebsiteStore:
    store = InMemoryWebsiteStore()
    store.seed_demo(_PROGRAM)
    return store


def test_seed_shape() -> None:
    store = _seeded()
    flags = store.list_page_flags(_PROGRAM)
    requests = store.list_analysis_requests(_PROGRAM)
    assert len(flags) == 2
    assert sum(1 for f in flags if f.status == "open") == 1
    assert any(f.brief_entry_id is not None and f.decision_id is not None for f in flags)
    assert len(requests) == 2
    assert sum(1 for r in requests if r.status == "open") == 1
    assert {r.target_kind for r in requests} == {"page", "campaign"}


def test_seed_is_idempotent() -> None:
    store = _seeded()
    store.seed_demo(_PROGRAM)
    assert len(store.list_page_flags(_PROGRAM)) == 2


def test_create_and_resolve_page_flag() -> None:
    store = _seeded()
    flag = store.create_page_flag(
        _PROGRAM,
        page_path="/pricing",
        site="gt.school",
        reason="high exit",
        created_at=datetime.now(UTC),
    )
    assert flag.status == "open"
    updated = store.update_page_flag(
        _PROGRAM, flag.flag_id, status="resolved", resolved_at=datetime.now(UTC)
    )
    assert updated.status == "resolved"
    assert updated.resolved_at is not None


def test_create_and_resolve_analysis_request() -> None:
    store = _seeded()
    req = store.create_analysis_request(
        _PROGRAM, target="june_blast", target_kind="campaign", question="did it convert?"
    )
    updated = store.update_analysis_request(_PROGRAM, req.request_id, status="resolved")
    assert updated.status == "resolved"


def test_update_unknown_raises_keyerror() -> None:
    store = _seeded()
    with pytest.raises(KeyError):
        store.update_page_flag(_PROGRAM, UUID(int=0xDEAD), status="resolved")
    with pytest.raises(KeyError):
        store.update_analysis_request(_PROGRAM, UUID(int=0xDEAD), status="resolved")


def test_program_isolation() -> None:
    store = _seeded()
    assert store.list_page_flags(_OTHER) == []
    assert store.list_analysis_requests(_OTHER) == []


def test_supabase_builder_returns_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert build_supabase_website_store() is None

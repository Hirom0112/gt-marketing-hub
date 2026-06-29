"""Store-seam tests for the Module-9 Admissions store (app.data.admissions_store).

Covers the deterministic demo seed (shape + idempotency), the write methods (objection
upsert, feedback create/update, bridge upsert/mark-produced), and program isolation on the
in-memory store. No I/O (the Supabase impl is exercised only via its construction guard).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.program import Program
from app.data.admissions_store import InMemoryAdmissionsStore

_PROGRAM = Program.FALL_ENROLLMENT


def _seeded() -> InMemoryAdmissionsStore:
    store = InMemoryAdmissionsStore()
    store.seed_demo(_PROGRAM)
    return store


def test_seed_shape() -> None:
    store = _seeded()
    assert len(store.list_objections(_PROGRAM)) == 7
    assert {o.theme for o in store.list_objections(_PROGRAM)} >= {"cost", "accreditation"}
    quotes = store.list_voice_quotes(_PROGRAM)
    assert len(quotes) == 8
    assert sum(1 for q in quotes if q.is_quote_of_week) == 1
    assert {q.sentiment for q in quotes} == {"positive", "neutral", "negative"}
    assert len(store.list_feedback(_PROGRAM)) == 6
    assert len(store.list_admission_stats(_PROGRAM)) == 5
    assert len(store.list_content_bridges(_PROGRAM)) == 4


def test_seed_quote_of_week() -> None:
    qow = _seeded().get_quote_of_week(_PROGRAM)
    assert qow is not None
    assert qow.is_quote_of_week is True
    assert qow.sentiment == "positive"


def test_seed_bridges_produced_split() -> None:
    bridges = _seeded().list_content_bridges(_PROGRAM)
    produced = [b for b in bridges if b.produced]
    pending = [b for b in bridges if not b.produced]
    assert len(produced) == 2
    assert len(pending) == 2
    assert all(b.published_at is not None for b in produced)
    assert all(b.published_at is None for b in pending)


def test_seed_stats_sorted_by_week() -> None:
    stats = _seeded().list_admission_stats(_PROGRAM)
    weeks = [s.week_of for s in stats]
    assert weeks == sorted(weeks)


def test_seed_is_idempotent() -> None:
    store = _seeded()
    store.seed_demo(_PROGRAM)  # guarded no-op
    assert len(store.list_objections(_PROGRAM)) == 7
    assert len(store.list_voice_quotes(_PROGRAM)) == 8


def test_upsert_objection_updates_existing() -> None:
    store = _seeded()
    oid = UUID(int=0xAD91_0000)  # the seeded cost objection
    updated = store.upsert_objection(
        _PROGRAM, objection_id=oid, theme="cost", week_count=99, trend="down"
    )
    assert updated.week_count == 99
    assert len(store.list_objections(_PROGRAM)) == 7  # updated, not appended


def test_create_feedback_appends() -> None:
    store = _seeded()
    item = store.create_feedback(_PROGRAM, summary="new signal", category="urgent", actionable=True)
    assert item.owner == "admissions"
    assert item.status == "open"
    assert len(store.list_feedback(_PROGRAM)) == 7


def test_update_feedback_sets_status_and_actioned_at() -> None:
    store = _seeded()
    iid = UUID(int=0xAD93_0000 + 1)  # the seeded open persona_mismatch item
    now = datetime.now(UTC)
    updated = store.update_feedback(_PROGRAM, iid, status="actioned", actioned_at=now)
    assert updated.status == "actioned"
    assert updated.actioned_at == now


def test_update_unknown_feedback_raises_keyerror() -> None:
    store = _seeded()
    try:
        store.update_feedback(_PROGRAM, UUID(int=0xDEAD), status="closed")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError on unknown feedback item")


def test_upsert_and_mark_bridge_produced() -> None:
    store = _seeded()
    bridge = store.upsert_bridge(
        _PROGRAM, objection_theme="cost", freq_before=12, surfaced_at=datetime.now(UTC)
    )
    assert bridge.produced is False
    published = datetime.now(UTC)
    marked = store.mark_bridge_produced(_PROGRAM, bridge.bridge_id, published_at=published)
    assert marked.produced is True
    assert marked.published_at == published


def test_mark_unknown_bridge_raises_keyerror() -> None:
    store = _seeded()
    try:
        store.mark_bridge_produced(_PROGRAM, UUID(int=0xDEAD))
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError on unknown bridge")


def test_program_isolation() -> None:
    store = _seeded()
    assert store.list_objections(Program.SUMMER_CAMP) == []
    assert store.list_feedback(Program.SUMMER_CAMP) == []
    assert store.get_quote_of_week(Program.SUMMER_CAMP) is None

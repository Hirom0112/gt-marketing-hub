"""Store-seam tests for the Module-5 Nurture store (app.data.nurture_store).

Covers the deterministic demo seed (shape + idempotency), the SMS thread update, and
the segment create path on the in-memory store. No I/O (the Supabase impl is exercised
only via its construction guard in deps tests).
"""

from __future__ import annotations

from uuid import UUID

from app.core.program import Program
from app.data.nurture_store import InMemoryNurtureStore

_PROGRAM = Program.FALL_ENROLLMENT


def _seeded() -> InMemoryNurtureStore:
    store = InMemoryNurtureStore()
    store.seed_demo(_PROGRAM)
    return store


def test_seed_shape() -> None:
    store = _seeded()
    segments = store.list_segments(_PROGRAM)
    sequences = store.list_sequences(_PROGRAM)
    threads = store.list_sms_threads(_PROGRAM)
    sla = store.list_sla_contacts(_PROGRAM)
    assert len(segments) == 6
    assert {s.tier for s in segments} == {"T1", "T2", "T3"}
    assert len(sequences) == 5
    assert {s.seq_type for s in sequences} == {
        "welcome",
        "nurture",
        "re_engagement",
        "event",
        "waitlist",
    }
    assert len(threads) == 14
    # Every status appears across the seeded inbox.
    assert {"unread", "no_reply", "objection", "hot_family", "ready"} <= {t.status for t in threads}
    assert len(sla) == 30


def test_seed_sla_buckets_are_deterministic() -> None:
    store = _seeded()
    sla = store.list_sla_contacts(_PROGRAM)
    contacted_in_window = sum(
        1
        for c in sla
        if c.contacted_at is not None
        and (c.contacted_at - c.entered_at).total_seconds() <= 24 * 3600
    )
    uncontacted = sum(1 for c in sla if c.contacted_at is None)
    assert contacted_in_window == 10  # a third contacted in the 24h window
    assert uncontacted == 10


def test_seed_is_idempotent() -> None:
    store = _seeded()
    store.seed_demo(_PROGRAM)  # second call is a guarded no-op
    assert len(store.list_segments(_PROGRAM)) == 6
    assert len(store.list_sms_threads(_PROGRAM)) == 14


def test_update_thread_marks_replied_and_status() -> None:
    store = _seeded()
    tid = UUID(int=0x4E53_0000 + 2)  # a seeded thread
    updated = store.update_thread(_PROGRAM, tid, status="hot_family", replied=True)
    assert updated.status == "hot_family"
    assert updated.replied is True
    # Persisted.
    assert store.get_thread(_PROGRAM, tid).status == "hot_family"  # type: ignore[union-attr]


def test_update_unknown_thread_raises_keyerror() -> None:
    store = _seeded()
    try:
        store.update_thread(_PROGRAM, UUID(int=0xDEAD), status="ready")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError on unknown thread")


def test_create_segment_appends() -> None:
    store = _seeded()
    seg = store.create_segment(
        _PROGRAM,
        tier="T1",
        sub_bucket="custom",
        label="Custom",
        attribute_filters={"engagement_tier": ["clicked"]},
        size=12,
        reachability_pct=80.0,
    )
    assert seg.tier == "T1"
    assert seg.owner == "nurture"
    assert len(store.list_segments(_PROGRAM)) == 7


def test_program_isolation() -> None:
    store = _seeded()
    # A different program has no seeded rows (program-scoped).
    assert store.list_segments(Program.SUMMER_CAMP) == []
    assert store.list_sms_threads(Program.SUMMER_CAMP) == []

"""Pure dashboard-layout starter-pack merge (TODO_v2 §B3 task 2; INV-2/A-7).

The composable Home lets a user arrange widgets; the backend stores a saved
layout (a list of RGL placements). When serving it, the layout is reconciled
against the current widget registry so the UI never renders a removed widget
(the crash-guard) and a starter widget is always present (re-hydrated if
missing). This is the pure reconcile — no I/O — so it lives in app.core and is
unit-tested with plain data here.
"""

from __future__ import annotations

from app.core.dashboard_layout import merge_starter_pack

# A small synthetic registry: three starter widgets + one optional extra.
_REGISTRY = {"pipeline", "queue", "funding", "geo_extra"}
_STARTER = {"pipeline", "queue", "funding"}


def _ids(layout: list[dict]) -> set[str]:
    return {p["i"] for p in layout}


def test_unknown_widget_is_dropped() -> None:
    """A placement whose id is not in the registry never renders (crash-guard)."""
    saved = [
        {"i": "pipeline", "x": 0, "y": 0, "w": 6, "h": 3},
        {"i": "queue", "x": 6, "y": 0, "w": 6, "h": 3},
        {"i": "funding", "x": 0, "y": 3, "w": 6, "h": 3},
        {"i": "ghost_removed", "x": 6, "y": 3, "w": 6, "h": 3},  # not in registry
    ]
    out = merge_starter_pack(saved, registry_ids=_REGISTRY, starter_ids=_STARTER)
    assert "ghost_removed" not in _ids(out)


def test_missing_starter_widget_is_rehydrated() -> None:
    """A starter widget absent from the saved layout is re-added (documented)."""
    saved = [
        {"i": "pipeline", "x": 0, "y": 0, "w": 6, "h": 3},
        {"i": "queue", "x": 6, "y": 0, "w": 6, "h": 3},
        # "funding" (a starter) deleted by the user — must come back.
    ]
    out = merge_starter_pack(saved, registry_ids=_REGISTRY, starter_ids=_STARTER)
    assert "funding" in _ids(out)
    assert _STARTER <= _ids(out)


def test_full_layout_with_extras_is_preserved() -> None:
    """All starters present + a valid extra ⇒ survivors keep their placement/order."""
    saved = [
        {"i": "pipeline", "x": 0, "y": 0, "w": 6, "h": 3},
        {"i": "queue", "x": 6, "y": 0, "w": 6, "h": 3},
        {"i": "funding", "x": 0, "y": 3, "w": 6, "h": 3},
        {"i": "geo_extra", "x": 6, "y": 3, "w": 6, "h": 3},
    ]
    out = merge_starter_pack(saved, registry_ids=_REGISTRY, starter_ids=_STARTER)
    # Same widget set, nothing re-hydrated, order + placement of survivors intact.
    assert out == saved
    assert _ids(out) == {"pipeline", "queue", "funding", "geo_extra"}


def test_empty_layout_yields_full_starter_pack() -> None:
    """A new/empty user gets the full starter pack in a deterministic arrangement."""
    out_empty = merge_starter_pack([], registry_ids=_REGISTRY, starter_ids=_STARTER)
    assert _ids(out_empty) == _STARTER
    # None-ish saved behaves like empty, and the arrangement is deterministic.
    out_none = merge_starter_pack(None, registry_ids=_REGISTRY, starter_ids=_STARTER)  # type: ignore[arg-type]
    assert out_none == out_empty
    # Every re-hydrated placement is a full RGL cell (no missing geometry).
    for placement in out_empty:
        assert {"i", "x", "y", "w", "h"} <= placement.keys()

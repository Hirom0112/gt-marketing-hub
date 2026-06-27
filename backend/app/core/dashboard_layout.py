"""Pure dashboard-layout starter-pack merge (TODO_v2 §B3; INV-2/A-7).

The composable Home lets each user arrange widgets. The backend persists a saved
layout — a list of RGL (react-grid-layout) placements, each ``{"i", "x", "y",
"w", "h"}``. Before serving it, the layout must be reconciled against the current
widget **registry** so the UI:

1. never renders a widget that has since been removed/renamed (the crash-guard),
   and
2. always shows the default *starter pack* widgets a new (or stripped-down) user
   should see.

:func:`merge_starter_pack` is that reconcile. It is the deterministic *pure* core
(A-7): a function of ``(saved, registry_ids, starter_ids)`` alone — no
repository, adapter, or httpx import (the core-purity test guards this). The
API/frontend own the actual ~30-widget catalog and feed the id sets in; this
module only performs the merge given a registry.

**Placement shape.** Each placement is a ``dict`` carrying its widget id under
key ``"i"`` (the react-grid-layout convention), falling back to ``"id"`` for
tolerance. ``"i"`` is the canonical key this module reads and writes.

**Re-hydrate behavior (documented).** Per the PLAN, a *missing* starter widget is
re-added: a new user with an empty saved layout receives the **full** starter
pack, and a user who deleted a starter widget gets it back. Survivors keep their
exact saved placement and order — only the missing starters are appended, in a
deterministic grid arrangement (sorted by widget id; no randomness — the repo
forbids it in core).
"""

from __future__ import annotations

WidgetId = str
# An RGL placement: an id under "i" plus integer x/y/w/h geometry. Modeled
# loosely (values are str | int) — the API owns the strict serialization schema.
Placement = dict[str, object]

# Default geometry for a re-hydrated placement (RGL cells on a 12-column grid).
_GRID_COLS = 12
_DEFAULT_W = 4
_DEFAULT_H = 2
_PER_ROW = _GRID_COLS // _DEFAULT_W  # 3 widgets per row


def _widget_id(placement: Placement) -> WidgetId | None:
    """The widget id of a placement — canonical ``"i"``, tolerant of ``"id"``."""
    raw = placement.get("i", placement.get("id"))
    return raw if isinstance(raw, str) else None


def _coord(placement: Placement, key: str) -> int:
    """Read an integer geometry value, defaulting to 0 for absent/odd shapes."""
    val = placement.get(key, 0)
    return val if isinstance(val, int) else 0


def _default_placement(widget_id: WidgetId, slot: int, base_y: int) -> Placement:
    """A deterministic default RGL cell for a re-hydrated widget.

    Lays widgets out left-to-right, top-to-bottom on the 12-col grid starting at
    ``base_y`` (the first free row below the surviving placements). ``slot`` is
    the 0-based index among the widgets being appended.
    """
    col = slot % _PER_ROW
    row = slot // _PER_ROW
    cell: Placement = {
        "i": widget_id,
        "x": col * _DEFAULT_W,
        "y": base_y + row * _DEFAULT_H,
        "w": _DEFAULT_W,
        "h": _DEFAULT_H,
    }
    return cell


def merge_starter_pack(
    saved: list[Placement] | None,
    *,
    registry_ids: set[str],
    starter_ids: set[str],
) -> list[Placement]:
    """Reconcile a saved widget layout against the current registry.

    Args:
        saved: The user's persisted layout as a list of RGL placements, or a
            ``None``-ish empty value for a brand-new user. Each placement carries
            its widget id under ``"i"`` (or ``"id"``).
        registry_ids: The ids of all currently-valid widgets. A saved placement
            whose id is not in this set is dropped (the crash-guard).
        starter_ids: The ids of the starter-pack widgets that must always be
            present. Any starter missing from the post-drop layout is re-added.

    Returns:
        The reconciled layout: surviving placements first (original order and
        geometry preserved), followed by deterministically-arranged default
        placements for any missing starter widgets. An empty/``None`` ``saved``
        yields the full starter pack.
    """
    survivors = [p for p in (saved or []) if _widget_id(p) in registry_ids]
    present = {_widget_id(p) for p in survivors}

    # Re-hydrate starter widgets that are valid but absent from the saved layout.
    # Sorted for determinism (starter_ids is an unordered set).
    missing = sorted(wid for wid in starter_ids if wid in registry_ids and wid not in present)

    base_y = max((_coord(p, "y") + _coord(p, "h") for p in survivors), default=0)
    rehydrated = [_default_placement(wid, slot, base_y) for slot, wid in enumerate(missing)]
    return [*survivors, *rehydrated]

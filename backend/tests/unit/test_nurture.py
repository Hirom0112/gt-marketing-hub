"""Nurture / later-lifecycle policy tests (rep close-loop core).

The cockpit's existing recovery machine only moves forward to "won" — it has no
COLD / PRESUMED_LOST / LOST / DORMANT vocabulary and no re-engagement cadence.
This slice adds that lifecycle as DETERMINISTIC, params-homed policy (INV-11):
cold/lost thresholds, the school-year re-engagement anchors, and the nurture
cadence are all dials in `params.yaml`, never code literals. Every threshold
asserted here reads from the committed example file, so param drift fails the
build (CLAUDE.md §4.1).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.nurture import anchor_pressure
from app.core.params import load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_ANCHORS = load_params(EXAMPLE_PARAMS).nurture.anchors


def test_nurture_params_load_with_committed_defaults() -> None:
    """The nurture block parses into typed params with the committed dials.

    These defaults are BUSINESS policy, not engineering — each is a tunable the
    team owns (cold/lost are their call). The test pins the example-file values
    so a drift or rename fails loudly (INV-11).
    """
    n = load_params(EXAMPLE_PARAMS).nurture

    assert n.cold_after_days == 14
    assert n.presumed_lost.after_attempts == 5
    assert n.presumed_lost.within_days == 21
    assert n.presumed_lost.requires_human_confirm is True
    assert n.base_recontact_interval_months == 6
    assert n.max_touches == 8
    assert n.channel_priority == ["sms", "email"]
    assert n.long_horizon.drip_months == 18

    # School-year re-engagement anchors, keyed by name → (month, day, ramp_days).
    anchors = {a.name: a for a in n.anchors}
    assert anchors["tefa_window"].month == 3
    assert anchors["tefa_window"].day == 17
    assert anchors["tefa_window"].ramp_days == 45
    assert anchors["school_selection"].month == 6
    assert anchors["school_selection"].day == 1
    assert anchors["back_to_school"].month == 8
    assert anchors["back_to_school"].day == 13
    assert anchors["back_to_school"].ramp_days == 60


# --- anchor-date re-engagement pressure (deterministic, params-driven) ---
# Pressure rises from 0 (>= ramp_days before the next occurrence of an anchor's
# recurring month/day) to 1.0 ON the anchor date. The funnel pulses on these
# windows (spring voucher deadline, school-selection, back-to-school), so a
# parked family's re-engagement ramps up as the nearest window approaches.


def test_anchor_pressure_zero_far_from_every_window() -> None:
    """No anchor within its ramp window ⇒ zero pressure, no anchor."""
    p = anchor_pressure(date(2026, 1, 1), _ANCHORS)
    assert p.pressure == 0.0
    assert p.anchor is None


def test_anchor_pressure_full_on_the_anchor_date() -> None:
    """On the exact anchor date, pressure is 1.0 and names that anchor."""
    p = anchor_pressure(date(2026, 3, 17), _ANCHORS)  # tefa_window
    assert p.anchor == "tefa_window"
    assert p.pressure == 1.0


def test_anchor_pressure_ramps_linearly_within_window() -> None:
    """15 days before back_to_school (8/13, ramp 60) ⇒ 1 - 15/60 = 0.75."""
    p = anchor_pressure(date(2026, 7, 29), _ANCHORS)
    assert p.anchor == "back_to_school"
    assert round(p.pressure, 4) == 0.75

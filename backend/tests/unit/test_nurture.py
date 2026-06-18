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

from pathlib import Path

from app.core.params import load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


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

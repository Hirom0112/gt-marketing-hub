"""Stood-in GA4 adapter tests (Module 13) — shape, honesty, determinism, no live read.

The simulated analytics adapter is a pure, offline source: the snapshot is aggregate-only
(``source_mode='simulated'``, never ``ga4_live``), deterministic across instances, and has
NO network client by construction (provable from the source text — INV-9).
"""

from __future__ import annotations

import inspect

import app.adapters.analytics.simulated as simulated_module
from app.adapters.analytics.base import AnalyticsWindow
from app.adapters.analytics.simulated import SimulatedAnalyticsAdapter

_WINDOW = AnalyticsWindow(start="2026-06-08", end="2026-06-15")


def test_snapshot_shape_and_source_mode() -> None:
    snap = SimulatedAnalyticsAdapter().snapshot(_WINDOW)
    assert snap.source_mode == "simulated"  # honest — never implied live (INV-6/9)
    assert {s.site for s in snap.sites} == {"gt.school", "anywhere.gt.school"}
    assert len(snap.pages) == 15
    assert len(snap.campaigns) == 6
    assert len(snap.downloads) == 6
    assert len(snap.funnel) == 5
    # Funnel is monotonically non-increasing (a real drop-off).
    sessions = [stage.sessions for stage in snap.funnel]
    assert sessions == sorted(sessions, reverse=True)


def test_snapshot_is_deterministic_across_instances() -> None:
    a = SimulatedAnalyticsAdapter().snapshot(_WINDOW)
    b = SimulatedAnalyticsAdapter().snapshot(_WINDOW)
    assert a.model_dump() == b.model_dump()


def test_three_campaigns_carry_broken_utms() -> None:
    # Deliberate broken tags so the website→CRM-Ops validation has real teeth.
    snap = SimulatedAnalyticsAdapter().snapshot(_WINDOW)
    blank_campaign = [c for c in snap.campaigns if c.utm_campaign == ""]
    assert blank_campaign  # the missing-campaign case
    assert any(c.utm_medium == "qr_code" for c in snap.campaigns)  # unallowed medium
    assert any(c.utm_medium != c.utm_medium.lower() for c in snap.campaigns)  # uppercase


def test_no_network_client_structural() -> None:
    # "No live read" is structural: the module imports no transport.
    src = inspect.getsource(simulated_module)
    assert "httpx" not in src
    assert "requests" not in src

"""Website & Digital Analytics boundary (Module 13) — the GA4 adapter seam.

The §7-style external boundary for Google Analytics (GA4). v1 wires a STOOD-IN
simulated impl (:class:`app.adapters.analytics.simulated.SimulatedAnalyticsAdapter`):
no live GA4 credentials are provisioned for this portal, so the website-analytics
surface reads aggregate, synthetic, offline data with ``source_mode="simulated"`` —
surfaced honestly, never implied live (INV-6/INV-9). Going live = supplying a GA4
Data-API impl behind the SAME :class:`app.adapters.analytics.base.AnalyticsAdapter`
interface, with zero changes to ``core/`` or the API.
"""

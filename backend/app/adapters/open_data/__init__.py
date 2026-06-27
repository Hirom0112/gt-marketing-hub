"""The Open Data enrichment boundary — tryopendata.ai Texas-education (E1).

The §7-style ``OpenDataAdapter`` seam (RESEARCH_v2 §II.3): a Seeded v1 default
(INV-9; pure, no network) and a Live impl over ``httpx`` against the documented
``POST https://api.tryopendata.ai/v1/query`` REST endpoint, selected at startup by
config in :mod:`app.adapters.registry`. Aggregate/district-level only (INV-1/INV-6).
"""

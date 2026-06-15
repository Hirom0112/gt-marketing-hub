"""GEO sampling adapter package — the §7.6 boundary (FR-3.7, FR-4.4, INV-9).

Repeated, variance-reported sampling of an AI engine's citations for a prompt
set. v1 ships only the **simulated** impl (no live engine poll, PROJECT §7);
``live`` is reserved and fails loud in :mod:`app.adapters.registry`.
"""

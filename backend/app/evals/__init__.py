"""Eval metrics package — golden sets, scoreboard, and pure metric functions.

Owns INV-3/INV-4: every AI action is gated by a tested threshold, and the
grounding gate fails closed. Metric modules here are PURE (stdlib + typing
only); thresholds live in `params/params.yaml` and are read by the callers
that consume these metrics, never imported here (INV-11).
"""

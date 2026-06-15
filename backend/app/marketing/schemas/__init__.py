"""Marketing schema records — content-as-data contracts (CONTENT_SPEC §2–§7).

Pure data per CLAUDE.md §3 / ARCHITECTURE.md §3: no `anthropic` / `langgraph` /
I/O imports. The pass/fail gate (`ValidationResult`, §9.6) lives in
`app/core/eval_gate.py`; records here only model the data so they CAN be gated.
"""

from __future__ import annotations

from app.marketing.schemas.geo import GeoContentPiece, GeoStructure

__all__ = ["GeoContentPiece", "GeoStructure"]

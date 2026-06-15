"""Leadership scoreboard endpoint — the P2 cross-funnel view (FR-6.1; ARCH §6).

The composition layer wiring the pure :func:`app.core.scoreboard.build_scoreboard`
rollup into HTTP. It is deliberately thin (the analog of ``app/api/geo.py``): the
whole aggregation — enrollment funnel counts, GEO coverage lift vs the 0% baseline,
per-eval pass/fail + overall green/red — lives in the pure core module this router
merely orchestrates (INV-2). The route reads the append-only NFR-6 audit log and
``params`` (for the GEO baseline, INV-11) and returns the frozen
:class:`~app.core.scoreboard.Scoreboard` directly. No business logic, no magic
numbers.

  ``GET /scoreboard``
    Aggregates the current audit log into the cross-funnel leadership summary and
    returns it. Read-only — nothing is logged. Same log + params ⇒ same scoreboard
    (the core rollup is deterministic).

This module imports only the pure core rollup + the observability/params deps; it
makes no live call and writes no state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_observability_log, get_params
from app.core.params import Params
from app.core.scoreboard import Scoreboard, build_scoreboard
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["scoreboard"])

# --- dependency aliases (Annotated keeps the call in the type, not a default arg
# — avoids ruff B008; the idiomatic FastAPI style, matching app/api/geo.py). ---
ParamsDep = Annotated[Params, Depends(get_params)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]


@router.get("/scoreboard", response_model=Scoreboard)
def get_scoreboard(log: LogDep, params: ParamsDep) -> Scoreboard:
    """The cross-funnel leadership scoreboard over the current audit log (FR-6.1).

    A thin wrapper over the pure :func:`build_scoreboard` rollup: reads the
    append-only NFR-6 audit log and ``params`` (the GEO baseline, INV-11) and
    returns the frozen :class:`Scoreboard`. Read-only, deterministic — same log +
    params ⇒ same scoreboard.
    """
    return build_scoreboard(log, params=params)

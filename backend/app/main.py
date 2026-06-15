"""FastAPI application entrypoint + AWS Lambda handler.

S0 wires the read-only landing surface (ARCHITECTURE.md §6): the deterministic
pipeline + Family Record GET endpoints, served over the in-memory repository
(ASSUMPTIONS A-3). The AI edge and write paths arrive in later slices.
"""

from fastapi import FastAPI
from mangum import Mangum

from app.api.ai_actions import router as ai_actions_router
from app.api.content import router as content_router
from app.api.families import router as families_router
from app.api.funding import router as funding_router
from app.api.geo import router as geo_router
from app.api.notes import router as notes_router
from app.api.seam import router as seam_router

app = FastAPI(title="GT Growth Cockpit", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns 200 with a fixed status body."""
    return {"status": "ok"}


# Read-only landing API (FR-2.1/2.2) — /pipeline, /families, /families/{id}.
app.include_router(families_router)

# Eval-gated AI action surface (FR-2.4; ARCH §5.2/§6) — /ai/enrollment/draft,
# /proposals/{id}/decision, /proposals. The decision route is the sole state
# write (INV-2); every proposal + eval + decision is logged (NFR-6).
app.include_router(ai_actions_router)

# Per-family notes timeline (FR-2.3; A-8) — /families/{id}/notes GET + POST.
# Manual notes + deterministic state-change auto-notes; no LLM, no proposals.
app.include_router(notes_router)

# Funding view + GT-controlled signal advance (FR-2.7; ARCH §6, §5.4) —
# /families/{id}/funding GET + /families/{id}/funding/signal POST. Deterministic
# TEFA math + the §5.4 funding-state machine; the signal is GT-controlled (INV-10),
# never an Odyssey API.
app.include_router(funding_router)

# Supabase↔HubSpot seam (FR-1.3/2.6; ARCH §4.7/§6) — /seam GET +
# /seam/{id}/reconcile POST. The reconcile is human-gated and LOGGED (NFR-6); a
# flagged conflict fails closed (INV-4).
app.include_router(seam_router)

# Content engine (FR-3.1/3.4/3.5; ARCH §5.3) — /ai/content/generate (gated batch),
# /content/{id}/decision (the sole content state write — keep promotes library +
# brand memory, discard strengthens a dont signal), /content/library (kept+
# validated search). Nothing publishes without an explicit keep (INV-2/INV-3).
app.include_router(content_router)

# GEO tracking (FR-3.7/4.4; ARCH §5.5/§6) — /geo GET (coverage vs the 0% baseline
# + lift, a deterministic default pass) + /geo/sample POST (a fresh repeated-
# sampling run, logged to the audit spine, NFR-6). Sampling is offline/simulated
# (INV-9); insufficient samples fail closed and disable the action (INV-3).
app.include_router(geo_router)


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)

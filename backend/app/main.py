"""FastAPI application entrypoint + AWS Lambda handler.

S0 wires the read-only landing surface (ARCHITECTURE.md §6): the deterministic
pipeline + Family Record GET endpoints, served over the in-memory repository
(ASSUMPTIONS A-3). The AI edge and write paths arrive in later slices.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.ai_actions import router as ai_actions_router
from app.api.content import router as content_router
from app.api.enrollment import router as enrollment_router
from app.api.evals import router as evals_router
from app.api.families import router as families_router
from app.api.funding import router as funding_router
from app.api.geo import router as geo_router
from app.api.marketing import router as marketing_router
from app.api.notes import router as notes_router
from app.api.publish import router as publish_router
from app.api.scoreboard import router as scoreboard_router
from app.api.seam import router as seam_router
from app.core.settings import get_settings

app = FastAPI(title="GT Pulse", version="0.1.0")

# CORS — the React app runs on a separate origin (Vite dev server / built host),
# so the browser sends cross-origin requests the API must explicitly allow-list
# (§5.1 GT_CORS_ALLOW_ORIGINS). Without this every front-end fetch fails the
# browser's same-origin check ("Load failed") even though the API answers 200.
# Origins come from the typed env seam (INV-11) — never `*`, which would let any
# site call the API. Read once at construction (mirrors the deps singleton).
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

# "Seed to HubSpot" deterministic write-action (S10 W3; ARCH §7.1; INV-2/9) —
# /enrollment/families/{id}/seed POST. Pushes a synthetic family across the
# CRMAdapter seam (live writes a Contact+Deal into the real portal behind the
# four guards; simulate records). The deal id returned is the captured live id.
app.include_router(enrollment_router)

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

# Marketing breadth (FR-3.6/3.8/3.10/3.11/3.12; ARCH §6) — /creators, /sentiment,
# /kpi, /content/schedule (GET+POST), /pipeline (GET + /advance), /recipes. The
# schedule gate + pipeline guard are fail-closed (blocked vs simulated_sent, INV-3);
# dispatch is SIMULATED, never live (INV-9); recipes attribute Tom Babb (INV-7).
app.include_router(marketing_router)

# Publish fan-out + dual-screen monitor (FR-3.6; ARCH §6) — /content/publish POST
# (validate→fan-out across N channels→HubSpot GT Social Post mirror→placeholder
# media→persist+log), /publish/monitor GET (the cockpit observability feed),
# /publish/status GET (the eval-gate flag the UI reads to disable publish). Dispatch
# is SIMULATED + mirror/media simulated/placeholder (INV-9, OUT-1); a red grounding
# eval refuses the action fail-closed (INV-3).
app.include_router(publish_router)

# Consolidated eval suite (FR-4.5; ARCH §6) — /evals/run POST (run all four FR-4.x
# evals over deterministic offline inputs + record the live suite-level kill state)
# + /evals GET (the green/red scoreboard + per-row disabled map). A red row disables
# the gated action in the LIVE path, fail-closed (INV-3); no live LLM call (INV-9).
app.include_router(evals_router)

# Leadership scoreboard (FR-6.1; ARCH §6) — /scoreboard GET. A pure deterministic
# rollup over the append-only audit spine (enrollment funnel, GEO lift vs the 0%
# baseline, per-eval green/red). Read-only; nothing is logged.
app.include_router(scoreboard_router)


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)

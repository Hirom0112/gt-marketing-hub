"""FastAPI application entrypoint + AWS Lambda handler.

S0 wires the read-only landing surface (ARCHITECTURE.md §6): the deterministic
pipeline + Family Record GET endpoints, served over the in-memory repository
(ASSUMPTIONS A-3). The AI edge and write paths arrive in later slices.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

from app.api.ai_actions import router as ai_actions_router
from app.api.auth import router as auth_router
from app.api.budget import router as budget_router
from app.api.contact_outcome import router as contact_outcome_router
from app.api.content import router as content_router
from app.api.crm_status import router as crm_status_router
from app.api.crm_sync import router as crm_sync_router
from app.api.decisions import router as decisions_router
from app.api.deps import get_params, get_security_event_log
from app.api.enrollment import router as enrollment_router
from app.api.evals import router as evals_router
from app.api.families import router as families_router
from app.api.funding import router as funding_router
from app.api.geo import router as geo_router
from app.api.layouts import router as layouts_router
from app.api.marketing import router as marketing_router
from app.api.merge import router as merge_router
from app.api.notes import router as notes_router
from app.api.payments import router as payments_router
from app.api.publish import router as publish_router
from app.api.scoreboard import router as scoreboard_router
from app.api.scorecard import router as scorecard_router
from app.api.seam import router as seam_router
from app.api.security import SecurityEdgeMiddleware
from app.api.security import router as security_router
from app.core.settings import get_settings, posted_catalog_mount_root

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

# M7 security edge — DETECTION (defense-in-depth), NOT inline blocking (MULTI_AGENT
# §7). It OBSERVES each request/response and records a `security_event` for a
# suspicious signal (401/403 burst, oversized result, user_id-reassign attempt,
# anon hit on an admin/service route), each carrying an OWASP mapping. RLS + the
# app-layer owner clamp remain the inline boundary; this only feeds Panel B. It
# writes server-side via the singleton service_role feed (INV-5; never client-
# exposed) and reads every threshold from params.security (INV-11).
app.add_middleware(
    SecurityEdgeMiddleware,
    log=get_security_event_log(),
    params=get_params(),
    settings=_settings,
)


# Posted-media static mount (FR-3.4; the scoped INV-1 exception, ASSUMPTIONS). When
# GT_POSTED_CATALOG_ROOT is set AND the directory exists, serve the scrape root's real
# media at /posted-media so the posted gallery's `media_ref` (/posted-media/<media_file>)
# resolves to the real file. When unset/missing, the mount is SKIPPED (graceful) — the
# gallery degrades to the library placeholders. Nothing real is committed; this serves an
# EXTERNAL, machine-local path at runtime. StaticFiles ships with starlette/fastapi.
_posted_media_root = posted_catalog_mount_root(_settings)
if _posted_media_root is not None:
    app.mount(
        "/posted-media",
        StaticFiles(directory=_posted_media_root),
        name="posted-media",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns 200 with a fixed status body."""
    return {"status": "ok"}


# Demo-auth bridge (B1 task 5a; the demo-login token endpoint) — POST
# /auth/demo-token. Mints a SIGNED seat JWT over synthetic data so the frontend can
# authenticate against `get_principal` without a real Supabase login (v1 has none).
# AUTH_MODE-gated: ABSENT (404) under AUTH_MODE=live (real Supabase issues tokens),
# 503 with no JWT secret. NOT the spoofable S1 header — tokens are verified on every
# request; this only issues them in demo mode (ASSUMPTIONS A-40).
app.include_router(auth_router)

# Read-only landing API (FR-2.1/2.2) — /pipeline, /families, /families/{id}.
app.include_router(families_router)

# Eval-gated AI action surface (FR-2.4; ARCH §5.2/§6) — /ai/enrollment/draft,
# /proposals/{id}/decision, /proposals. The decision route is the sole state
# write (INV-2); every proposal + eval + decision is logged (NFR-6).
app.include_router(ai_actions_router)

# Per-family notes timeline (FR-2.3; A-8) — /families/{id}/notes GET + POST.
# Manual notes + deterministic state-change auto-notes; no LLM, no proposals.
app.include_router(notes_router)

# Rep close-loop WRITE surface (A-19) — /families/{id}/contact-outcome POST (log a
# call result) + /families/{id}/presumed-lost-confirm POST (the human-confirm gate
# that records LOST). Append-only spine events the recovery deriver reads; both
# owner-scoped (INV-5), the confirm fail-closed so a warm lead is never auto-dropped.
app.include_router(contact_outcome_router)

# Identity merge queue (ENROLLMENT_REFACTOR §5.2/§6; INV-2/INV-4/INV-9) —
# /merge-queue GET. The dedup human-review pile: the deterministic propose_merge
# core flags ambiguous duplicate households (fail-closed, never auto-merged) and
# logs each as a proposal on the §10 spine (NFR-6); the existing
# /proposals/{id}/decision route resolves it (approve = a SIMULATED fold).
app.include_router(merge_router)

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

# Stripe webhook (A3; PLAN_v2 §A3; RESEARCH_v2 §II.2) — /payments/webhook POST. Reads
# the RAW body, verifies the Stripe-Signature (forged/expired ⇒ 400, never a 2xx),
# dedupes on event.id, runs the deterministic decision, and on FULFILL records the
# payment + advances the GT funding signal one legal §5.4 step (INV-10 — the receipt IS
# the GT-controlled signal; never written from the payload), logging each (NFR-6).
# Dispatch/verify is simulated v1 (INV-9); a fast 2xx in every processed case.
app.include_router(payments_router)

# Supabase↔HubSpot seam (FR-1.3/2.6; ARCH §4.7/§6) — /seam GET +
# /seam/{id}/reconcile POST. The reconcile is human-gated and LOGGED (NFR-6); a
# flagged conflict fails closed (INV-4).
app.include_router(seam_router)

# CRM seam status (S14 W4; INV-3/INV-8 surfaced) — /crm/status GET. A read-only
# window onto the effective HubSpot seam (configured CRM_MODE, the kill switch, the
# mode the registry would actually select, whether a token is set — NEVER the token,
# the per-run call cap). The frontend reads it to show "CRM: Simulated/LIVE/Kill
# switch ON" and to FAIL CLOSED — disable the live-push control when the kill switch
# is on (the INV-3 "red eval disables the action in the UI" pattern). The kill
# switch's MECHANISM stays the server env var; this only surfaces state.
app.include_router(crm_status_router)

# CRM-as-truth incremental poll (A2; PLAN_v2 §A2; RESEARCH_v2 §II.1) —
# /crm/sync/poll POST + /crm/sync/status GET. The poll pulls deals modified since
# the persisted per-program watermark (window-chunked under the 10k cap), reconciles
# each through the §4.7 seam (CRM wins stage/owner; funding_state stays
# DB-authoritative, INV-10), advances the watermark, and LOGS each proposal+decision
# (NFR-6). Dispatch is simulated v1 (INV-9); status is read-only.
app.include_router(crm_sync_router)

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

# Weekly KPI scorecard (B5; ARCH §6) — /scorecard/weekly GET. The canonical weekly
# metric table (this-week/last-week/delta/sparkline/status/pace projection per metric),
# identical for everyone (any authenticated seat — no role gate). The API samples the
# per-metric weekly series from the audit spine (bucketed by ISO week) and threads it
# through the pure build_weekly_scorecard transform (the status band + pacing goal_date
# from params, INV-11). Read-only; nothing is logged.
app.include_router(scorecard_router)

# M7 security/observability surface (MULTI_AGENT_COCKPIT §7) — /security/posture
# (Panel A: the LIVE RLS posture — the same test_migrations_rls invariants run at
# runtime, RED when a table loses FORCE), /security/events (Panel B: the append-only
# suspicious-activity feed, a SIMULATED labeled stream in v1, INV-9), and the §7
# acknowledge action. DETECTION, not inline blocking; the populate path is the
# app-layer service_role feed (no public definer-rights helper, D-RLS-7).
app.include_router(security_router)

# Cross-module human Decision Queue (B2; PLAN_v2 §B2; INV-2) — /decisions GET
# (leader-gated open queue) + POST (open submit — any module/principal flags an
# item) + /decisions/{id}/action POST (leader-gated decide: approve/reject/need_info
# through the pure state machine, fail-closed on an illegal transition). The actor is
# the VERIFIED principal; every decided action is LOGGED to the §10 spine (NFR-6).
app.include_router(decisions_router)

# Composable Home layout (B3; PLAN_v2 §B3; INV-5/INV-11) — /home/layout GET + PUT.
# Per-user dashboard widget arrangement, scoped to the VERIFIED principal's user_id
# (no owner param — the app-layer IDOR defense; RLS on the 0029 table is the DB
# backstop). GET reconciles the saved RGL placements against the server-side widget
# registry via the pure merge_starter_pack (new user ⇒ starter pack, removed widgets
# dropped, missing starters re-hydrated); PUT upserts the layout and returns the
# merged result. No LLM, no external send.
app.include_router(layouts_router)

# Budget Tracker + variance→Decision feeder (B4; PLAN_v2 §B4; INV-2/INV-11) — /budget
# GET (the params-seeded workstream tracker: per-workstream planned/actual/committed/
# remaining/variance/flagged + roll-up + burn series; any authenticated VIEW) +
# /budget/entry POST (admin/leader-gated append to the spend ledger). On a >10% overrun
# the POST emits EXACTLY ONE open `budget_variance` Decision-Queue item via the B2
# feeder (idempotent per workstream until decided). Variance is the pure core's; no LLM,
# no external send.
app.include_router(budget_router)


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)

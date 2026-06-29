"""FastAPI application entrypoint + AWS Lambda handler.

S0 wires the read-only landing surface (ARCHITECTURE.md §6): the deterministic
pipeline + Family Record GET endpoints, served over the in-memory repository
(ASSUMPTIONS A-3). The AI edge and write paths arrive in later slices.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

from app.api.admissions import router as admissions_router
from app.api.ai_actions import router as ai_actions_router
from app.api.ambassadors import router as ambassadors_router
from app.api.auth import router as auth_router
from app.api.budget import router as budget_router
from app.api.contact_outcome import router as contact_outcome_router
from app.api.content import router as content_router
from app.api.content_analytics import router as content_analytics_router
from app.api.content_kanban import router as content_kanban_router
from app.api.crm_ops import router as crm_ops_router
from app.api.crm_status import router as crm_status_router
from app.api.crm_sync import router as crm_sync_router
from app.api.decisions import router as decisions_router
from app.api.deps import get_params, get_security_event_log
from app.api.enrollment import router as enrollment_router
from app.api.evals import router as evals_router
from app.api.families import router as families_router
from app.api.field_events import router as field_events_router
from app.api.funding import router as funding_router
from app.api.geo import router as geo_router
from app.api.grassroots import router as grassroots_router
from app.api.layouts import router as layouts_router
from app.api.marketing import router as marketing_router
from app.api.merge import router as merge_router
from app.api.notes import router as notes_router
from app.api.nurture import router as nurture_router
from app.api.open_data import router as open_data_router
from app.api.payments import router as payments_router
from app.api.publish import router as publish_router
from app.api.scoreboard import router as scoreboard_router
from app.api.scorecard import router as scorecard_router
from app.api.seam import router as seam_router
from app.api.security import SecurityEdgeMiddleware
from app.api.security import router as security_router
from app.api.summer import router as summer_router
from app.api.website import router as website_router
from app.core.settings import get_settings, posted_catalog_mount_root

# ---------------------------------------------------------------------------
# Cache pre-warmer (perf). The Home / Executive-Command dashboard reads these
# endpoints live; several recompute the short-TTL Supabase⇄HubSpot snapshot against
# the live portal (CRM_MODE=live), which is the few-second "sample → live" flip the
# UI shows on a cold cache. A tiny in-process loop periodically self-calls them so the
# single-flight snapshot cache (app.api._crm_ops_cache, 60s TTL) stays hot — the next
# real request is served warm. Opt-in via CACHE_WARM_SECONDS (0/unset = OFF, the
# default in tests/CI); production (Railway) sets it just under the snapshot TTL. A
# warmed LIVE read is STILL live (the cache never changes the computed values).
_WARM_PATHS = (
    "/crm/ops/overview",
    "/nurture/overview",
    "/scorecard/weekly",
    "/content/performance",
    "/website/overview",
)


async def _warm_caches_forever(interval: float) -> None:
    """Self-call the dashboard's live endpoints every ``interval`` s (fails silently)."""
    base = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        await asyncio.sleep(5.0)  # let uvicorn finish binding before the first warm
        while True:
            with contextlib.suppress(Exception):
                resp = await client.post("/auth/demo-token", json={"role": "leader"})
                token = resp.json().get("access_token") if resp.status_code == 200 else None
                if token:
                    headers = {"Authorization": f"Bearer {token}"}
                    # Sequential (not concurrent) so the live HubSpot reads don't burst the
                    # rate limit — a gentle warm keeps the snapshot caches reliably hot.
                    for path in _WARM_PATHS:
                        with contextlib.suppress(Exception):
                            await client.get(path, headers=headers)
                        await asyncio.sleep(0.5)
            await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the optional cache-warmer task for the app's lifetime (clean cancel on stop)."""
    interval = 0
    with contextlib.suppress(ValueError):
        interval = int(os.environ.get("CACHE_WARM_SECONDS", "0") or 0)
    task: asyncio.Task[None] | None = None
    if interval > 0:
        task = asyncio.create_task(_warm_caches_forever(interval))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="GT Pulse", version="0.1.0", lifespan=_lifespan)

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

# CRM/Marketing-Operations data-quality view (C1; TODO_v2 §C1) — /crm/ops GET. A
# read-only window COMPOSING the committed C1 cores over the active-program cohort:
# A4 sync-parity (REUSED, not forked), the auto data-quality queue, per-entity
# UTM-health, and the honest field-reliability flags — with the cross-module
# data-confidence banner when parity drops below params.crm_ops.parity_floor.
# Gated only by Depends(get_principal) (any authenticated seat, like /crm/status);
# no state write, no live call (INV-2/INV-9).
app.include_router(crm_ops_router)

# Content engine (FR-3.1/3.4/3.5; ARCH §5.3) — /ai/content/generate (gated batch),
# /content/{id}/decision (the sole content state write — keep promotes library +
# brand memory, discard strengthens a dont signal), /content/library (kept+
# validated search). Nothing publishes without an explicit keep (INV-2/INV-3).
app.include_router(content_router)

# Content & Thought-Leadership analytics (Module 3; ARCH §5.3/§6) — GET
# /content/overview|calendar|performance|testimonial-stubs (any seat); owner-gated
# POST /content/calendar/reschedule|entry (operator must own 'content'; leaders/admins
# any); POST /content/brand-voice/suggest (advisory V-4 suggest-edits — INV-2 proposal,
# never applied). Pure core (app.core.content_analytics) + store seam
# (app.data.content_metrics_store, 0036) + the V-4 brand judge. INV-1/6/11.
app.include_router(content_analytics_router)

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

# Field & Events → Decision Queue (Module 11; INV-1/INV-2) — /field/events/proposal
# POST. The Field & Events Owner's priority recommendations land as OPEN leadership
# decisions on the `field_events` workstream via the B2 feeder; `raised_by` is the
# VERIFIED principal (never the body). No LLM, no external send.
app.include_router(field_events_router)

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

# Open Data enrichment → Decision Queue (E1; TODO_v2 §E1; INV-2/8/11) —
# /open-data/enrich POST. Runs a Texas-district Open Data query through the
# OpenDataAdapter seam (seeded v1 / live go-live, §7 registry), applies the pure
# enrich_decision rule, and — when the recommendation CHANGES — feeds exactly one
# open card into the B2 Decision Queue via the shared flag_decision feeder, carrying
# full provenance + the data SOURCE (live OpenData vs the seeded fallback). An
# unchanged rec enqueues nothing (honest); the response surfaces the change + source
# either way. Any authenticated seat; no live external write (INV-9).
app.include_router(open_data_router)

# Grassroots ambassador dual-source reconcile (HubSpot ⊕ community.gt.school) —
# GET /ambassadors/reconcile. Pure reconciler (app.core.ambassador_reconcile) over two
# synthetic sources, deduped to the union with conflicts surfaced for human review
# (INV-2/4). Aggregate, adult-only (INV-6). Backs the Grassroots RECONCILED badge.
app.include_router(ambassadors_router)

# Grassroots Engine (Module 2) — roster/sprints/market-map/events + cross-links.
# GET /grassroots/overview|ambassadors|market-map|sprints|events (any seat); owner-gated
# POST writes (operator must own 'grassroots'; leaders/admins any). Three cross-module
# links: POST /grassroots/hot-family → Decision Queue (flag_decision), POST
# /grassroots/testimonial → Content library DRAFT stub, and ambassador_event as the
# READ-ONLY source the Field & Events module consumes (GET /grassroots/events). Pure
# core (app.core.grassroots) + store seam (app.data.grassroots_store, 0035). INV-1/6/11.
app.include_router(grassroots_router)

# Summer Camp (Module 4) — fed from the camp store seam (app.data.camp_store, 0032 +
# 0037). GET /summer/reconcile: dual-source reconcile (summer.gt.school + standalone
# form) with NO double-count + Phase-1 dimensions (signup-channel breakdown, funnel,
# registrations-this-week, camp-start countdown, session calendar, per-campus waitlist;
# optional ?campus/?grade_band/?source slicing). GET /summer/content: the camp_-tagged
# slice of the live content kanban. POST /summer/session-change: owner-gated leadership
# cross-link → Decision Queue (flag_decision). Pure reconciler (app.core.summer_reconcile);
# program isolation (program_id='summer_camp'). Revenue stays synthetic (paid × price).
app.include_router(summer_router)

# Content production kanban synced to Google Sheets (INV-9 simulated/live) —
# GET/POST /content/kanban. Reads/writes content rows through the SheetsAdapter seam
# (simulate default; live flips on SHEETS_MODE + a present key, behind a per-run cap +
# kill switch, INV-8). Backs the Content module's real two-way Sheet sync.
app.include_router(content_kanban_router)

# Nurture & Lifecycle (Module 5) — the segment/sequence-mirror/SMS-inbox/SLA seam
# (app.data.nurture_store, 0040) + LIVE HubSpot AGGREGATE reads (engagement-tier mix +
# deal-pipeline distribution + handoff — INV-6, never per-person). GET /nurture/overview|
# segments|pipeline|sequences|sms|sla (+ /kpi-feed, /attribution) any seat; owner-gated
# POST /nurture/segments/build (operator must own 'nurture'; demo operator owns grassroots
# ⇒ admin/leader-only). Four cross-links: POST /nurture/sms/{id}/flag-hot-family → Decision
# Queue (flag_decision), POST /nurture/sms/objection-brief → Content calendar DRAFT, the
# /kpi-feed for the Dashboard module, and /attribution for Content Performance. Pure core
# (app.core.nurture); program isolation; sequences are a read-only synthetic mirror. INV-1/6/11.
app.include_router(nurture_router)

# Admissions & Voice of Customer (Module 9) — the listening post: objection log, voice/
# quote feed + §7.5 aggregate sentiment (placeholder, never live; INV-6), feedback→
# marketing loop with a 7-day closure rate, weekly admission stats, and the objection→
# content bridge tracker (app.data.admissions_store, 0042; pure core app.core.admissions).
# GET /admissions/overview|objections|voice|feedback|bridge (any seat). Owner-gated
# (admissions) writes: POST /admissions/objections/{id}/brief → a Content calendar DRAFT
# brief + a bridge row (CROSS-LINK to Module 3), POST /admissions/feedback → an item that,
# when actionable, enqueues an OPEN `admissions` Decision-Queue card (CROSS-LINK to Module
# 11). PATCH /admissions/feedback/{id} (action/close) is leader/admin. `owner`/`raised_by`
# are the VERIFIED principal (never the body). No LLM, no external send. INV-1/6/11.
app.include_router(admissions_router)


# Website & Digital Analytics (Module 13) — the GA4 surface for gt.school +
# anywhere.gt.school. Site/page/source/download/conversion-path METRICS are read off the
# GA4 boundary (app.adapters.analytics — a STOOD-IN simulated adapter in v1,
# source_mode="simulated"; no live GA4 credential in this portal, INV-6/INV-9); the pure
# core (app.core.website) derives every rollup. The Hub OWNS only the leadership-input
# state (page flags + analysis requests), persisted to Supabase (0043). GET /website/
# overview|subpages|traffic|downloads|paths|inputs (any seat). LEADERSHIP-gated
# (leader/admin) writes: POST /website/pages/flag → a Content calendar DRAFT refresh brief
# (CROSS-LINK to Module 3) + a `website` Decision-Queue card (CROSS-LINK to Module 11);
# POST /website/analysis → a `website` Decision card (CROSS-LINK to Module 11). The traffic
# view runs the SAME check_utm rule set CRM Ops uses over the tagged campaigns at the ORIGIN
# (CROSS-LINK to Module 7). `raised_by` is the VERIFIED principal (never the body). INV-1/6/11.
app.include_router(website_router)


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)

# GT Marketing Hub

**🔗 Live demo: https://gtpulse-marketing-hub.vercel.app**
_(runs standalone on seed data — no login, click through every module; use the **"VIEWING AS"** switcher to change roles)_

**The marketing operations product for GT School — a 13-module web app on a sync engine that keeps a
CRM (HubSpot) and the app's own database in agreement, with exactly one authoritative source per
number: the database (`app_form`) owns the funnel / TEFA / income / grade truth, the CRM owns
contacts, deals, and engagement, and the Hub itself owns budget. Payments, dual-source
reconciliation, program isolation, and an eval-gated AI edge — all provable against synthetic data
built to stress them.**

This is the GT technical project: **Phase 1** a data backbone (a bidirectional CRM↔database sync
engine with strict per-program isolation and idempotent payments), and **Phase 2** the product on
top of it (the 13-module Hub the spec describes). The same isolation and reconciliation discipline
carries through both — Phase 2 *spends* the backbone, it doesn't re-solve it.

> **No production PII, ever.** Every record in this repo is synthetic, shaped like GT's real schema.
> Real services (HubSpot, Stripe, Supabase, Open Data, Google Sheets) are wired live; the data
> inside them is generated. Sources we can't reach (Meta, GA4, X, summer.gt.school,
> community.gt.school) are stood in behind the same interfaces and **labeled as such**.

---

## Table of contents

1. [What's real vs. stood-in vs. synthetic](#whats-real-vs-stood-in-vs-synthetic)
2. [Quickstart](#quickstart)
3. [Demo walkthrough — "show us it works"](#demo-walkthrough--show-us-it-works)
4. [Where I spent depth (and what I cut)](#where-i-spent-depth-and-what-i-cut)
5. [Architecture](#architecture)
6. [Test data is part of the deliverable](#test-data-is-part-of-the-deliverable)
7. [Known gaps, surfaced honestly](#known-gaps-surfaced-honestly)
8. [Verify it yourself (the quality gate)](#verify-it-yourself-the-quality-gate)
9. [Repo layout](#repo-layout)

---

## What's real vs. stood-in vs. synthetic

The cleanest way to hold it: **the services are real, the data inside them is synthetic, and a few
unreachable sources are stood in behind the same interface.**

| Category | Sources | Status |
|---|---|---|
| 🟢 **Live (real accounts, real API calls)** | **HubSpot** CRM (private-app token), **Stripe** test-mode + signed webhooks, **Supabase**/Postgres (program stores + Hub state), **Open Data** (tryopendata.ai — real Texas PEIMS/STAAR/accountability), **Google Sheets** (real read+write for the Content kanban) | Real adapters, default `simulate`, flip live with a token/key. Verified working. |
| 🟡 **Stood-in (unreachable — seeded behind the real interface, labeled)** | Meta Business Suite (FB+IG), X/Twitter, GA4, summer.gt.school, community.gt.school | Honest placeholders; the UI labels them `STOOD-IN` / `SIMULATED`, never faked green. |
| ⚙️ **Synthetic (generated, regardless of which service holds it)** | families, deals, enrollments, engagement, budgets, segments — shaped to the spec and the **$365K** budget, with deliberate edge cases | `backend/app/data/synthetic*.py` — deterministic, reproducible, PII-safe. |

Every adapter follows the same seam (`backend/app/adapters/`): a `simulate`/`placeholder` default and
a `live` implementation, switched by env, behind a hard per-run cap + kill switch. So nothing makes a
live external call unless you opt in, and `simulate` still *verifies* (e.g. the Stripe webhook checks
real HMAC signatures offline).

> **Single source of truth:** every number resolves to exactly one authoritative source (per the
> table above) and is never computed two ways — the discipline the spec scores.

---

## Quickstart

**Prerequisites:** Python 3.12 + [`uv`](https://docs.astral.sh/uv/), Node 20+, git.

```bash
# 1. Activate the enforced quality gate (run once per clone).
git config core.hooksPath .githooks

# 2. Tunables — every magic number lives here (INV-11: no magic numbers in code).
cp params/params.example.yaml params/params.yaml

# 3. Backend (FastAPI, Python 3.12 via uv).
cd backend && uv sync

# 4. Run the API (http://localhost:8000).
uv run uvicorn app.main:app --reload
```

```bash
# 5. The Hub (Next.js 14). In a second terminal:
cd web && npm install && npm run dev      # http://localhost:3001
```

The Hub runs **standalone on seed data** out of the box, so you can click through every module with
no backend. With the API running on `:8000`, the wired modules (Budget, KPI Scorecard, Decision Queue,
Grassroots, Content, Summer Camp, Field & Events, Nurture & Lifecycle, CRM / Marketing Ops, Admissions & VoC) read
**live** from the backbone — each fully fleshed out across its sub-view tabs with real owner-gated writes — and fall back to seed if the API is
unreachable. Use the **"VIEWING AS"** switcher (top bar) to change roles.

> **Real integrations are opt-in.** Each live source needs its own env var + credential (the full
> registry is in `backend/app/core/settings.py`). Defaults are `simulate`, so the system is fully
> demoable with zero secrets. Secrets are never committed (`.gitignore` covers `.secrets/`, `.env*`).

---

## Demo walkthrough — "show us it works"

The brief asks to *watch a payment propagate, a budget reconcile to the total, a role be denied the
Decision Queue, and the data-confidence banner appear when parity drops.* Here is exactly how to see
each, plus the dual-source reconciles.

### 1. A payment propagates — and is idempotent (Phase 1, no setup)

```bash
cd backend && uv run python scripts/demo/stripe_edge_cases.py
```

Drives four **signed** Stripe events through the real `POST /payments/webhook` (verify → dedupe →
decide → fulfill → fast-2xx) and prints what propagated:

| Scenario | Result |
|---|---|
| **Normal** `checkout.session.completed` | FULFILL — payment recorded, funding signal advances one legal step |
| **Duplicate** (same `event.id`) | **NOOP** — no second row, no double-advance (the `stripe_events` PK ledger) |
| **Failed** `payment_intent.payment_failed` | ACK — recorded for audit, never fulfilled |
| **Late / illegal** (family not at the legal predecessor) | payment recorded, advance **refused**, no crash, anomaly logged |

Resets to a clean state every run.

### 2. The budget reconciles to $365K

Hub → **Budget**. Workstream rows (grassroots/content/guerrilla/ops) **sum to $365,000** — never
hardcoded twice; the total is computed from the parts (`params.budget`). A >10% variance auto-flags to
the Decision Queue.

### 3. A role is denied the Decision Queue

Hub → "VIEWING AS" → **Operator** → click **Decision Queue**. Denied (Leaders/Admins only). Switch to
**Leader** and it opens. The gate is real client-side enforcement backed by the RBAC params.

### 4. The data-confidence banner appears when parity drops

Hub → **CRM Ops**. The Supabase↔HubSpot seam parity sits **well below the 0.95 threshold** (~12% of
seeded families carry genuinely divergent CRM values), so the data-confidence banner shows and the
data-quality queue lists the conflicts — surfaced, not faked.

### 5. Dual-source reconciliation, no double-count

```bash
# With the API running:
curl localhost:8000/ambassadors/reconcile   # HubSpot ⊕ community.gt.school, deduped union + conflicts
# /summer/reconcile is the same discipline (auth-gated; the Hub mints a demo token)
```

Both merge two overlapping sources on a stable key, count each entity **once**, and hold ambiguous
matches for human review (never auto-merge). Summer Camp ties to Phase-1 **program isolation**
(`program_id='summer_camp'`, RESTRICTIVE per-program RLS).

---

## Where I spent depth (and what I cut)

The judgment being scored is *which* modules to build deep. I built the **data backbone** plus the
**eleven modules that exercise it hardest** — each end-to-end across every sub-view tab, persisted to
live Supabase (migrations `0032`–`0043`, program-scoped RLS), with real owner-gated writes verified
live — and deliberately left the breadth/viz surfaces as honest seed.

**Built deep, on the real backbone:**

- **Phase 1 backbone** — bidirectional CRM↔Supabase seam with last-write-wins + conflict detection,
  **idempotent Stripe payments** (signature verify + replay-NOOP ledger), **per-program isolation**
  (RESTRICTIVE RLS keyed on the JWT `program_id`), identity/household **merge queue** (fail-closed),
  **sync-parity** + data-quality derivers, and **Open Data** enrichment that changes a decision.
- **Budget** — the clearest "a number means the same everywhere" test ($365K, variance → Decision
  Queue); burn series, per-owner spend gating, leadership re-plan.
- **KPI Scorecard** — per-metric provenance (where every number comes from), trends, SLA, goal pacing.
- **Decision Queue** — leadership-only gate; first-class decision columns; cross-module auto-flags
  (budget variance, hot families, event proposals) and a resolved-toast back to submitters.
- **Grassroots** — ambassador roster + **dual-source reconcile** (HubSpot ⊕ community), market map,
  referral sprints, parent community (honest stood-in for un-instrumented NPS), parent-led events.
- **Content & Thought Leadership** — production kanban with **real two-way Google Sheets sync**,
  editorial calendar with conflict detection, channel performance (UTM honesty), searchable library,
  and an **advisory brand-voice auditor** (LLM-proposal → heuristic fallback, INV-2).
- **Summer Camp** — **dual-source reconcile** + program isolation, registration funnel, sessions, and
  **revenue collected via real Stripe** (test-mode PaymentIntents → signed webhook → camp ledger).
- **Field Marketing & Events** — event tracker, a **month-grid calendar** that overlays Grassroots
  ambassador events **read-only**, and priority-event proposals into the Decision Queue.
- **Nurture & Lifecycle** — the most data-rich module, and the one that reads **live from HubSpot**:
  engagement-tier mix, deal-pipeline distribution, and marketing→onboarding handoff are **aggregate
  reads off the real HubSpot Pro portal** (synthetic contacts/deals pushed behind the four guards,
  read back INV-6 aggregate-only); the engagement×attribute heatmap **joins** that to the `app_form`
  source-of-truth; sequences + SMS inbox are a labeled synthetic mirror (Sales-Hub Sequences /
  Conversations APIs aren't exposed in the portal); SLA tracker, segment builder, and **four
  cross-links** (hot-family→Decision Queue, SMS-objection→Content brief, pipeline+handoff→KPI,
  conversion→Content Performance) all verified live.
- **CRM / Marketing Operations** — the data-infrastructure health module, built on the Phase-1 seam:
  sync-parity (overall + field-level) + the always-on data-confidence banner, a **data-driven UTM
  health flag** with an **audited repair workflow** (deterministic lowercase/trim + a params alias map
  fix the losslessly-repairable, an audit log records who/what/when, the genuinely-ambiguous route to a
  manual queue — never faked green), field-reliability flags (TEFA/income/source — a
  documented modeling call), a **live HubSpot lead-score histogram** (aggregate `gt_lead_score` bands,
  read-only), and a **persisted data-quality queue with auto-detection** (a scan upserts sync-drift +
  UTM-breakage issues idempotently on a signature) with a leadership lifecycle (acknowledge / prioritize
  / resolve) + resolution log, and a leader-only scoring-change → Decision Queue.
- **Admissions & Voice of Customer** — the listening post that closes the loop back to marketing:
  objection log (theme × frequency × 4-week trend × source × verbatim), an **objection→content-brief
  bridge** that reuses the Content pipeline and tracks **hit rate + did-objection-frequency-drop**,
  Voice-of-Families quotes + sentiment (placeholder adapter, labeled aggregate), and a
  **feedback-to-marketing loop** where actionable items flag to the **Decision Queue** + surface to the
  Marketing Lead, with a **7-day closure rate**. Admission numbers (applicants/Shadow Days/offers/deposits) by week.
- **Website & Digital Analytics** — GA4 for `gt.school` + `anywhere.gt.school`: site/page/source/
  download/conversion-path metrics read off a **GA4 boundary** that is a **stood-in simulated adapter**
  in v1 (`source_mode="simulated"`, no live GA4 credential in this portal — labeled in the header,
  never implied live), with a pure core deriving the session-weighted rollups, top landing pages,
  channel breakdown, and **landing→application funnel drop-off**. The Hub owns only the
  **leadership-input** state (page-refresh flags + analysis requests). **Three cross-links:** a flagged
  page drafts a **Content refresh brief** (Module 3) + raises a **Decision** (Module 11); an analysis
  request raises a **Decision**; and the traffic view runs the **same UTM rule set CRM Ops uses** over
  the tagged campaigns at the **origin** of the tags (Module 7; detect-only — broken flagged, never
  auto-fixed).

**Executive Command (Home)** now aggregates **live** from every owning module — each of the
composable widgets reads its module's real aggregate and carries an honest **● LIVE / ◐ stood-in
(GA4) / ○ sample** pill, so you can see at a glance which number is live wire data. (It streams
per-endpoint, so a slow live read never blocks the rest, and a backend cache pre-warmer keeps the
flip fast.)

**Left as honest seed (labeled, behind the right shape):** Resource Library — a
breadth/aggregation surface that doesn't further test the backbone.

---

## The datasets behind the live numbers

Every number in the Hub is **synthetic** (INV-1 — no real PII) and either lives in **live Supabase**
(seeded via each module's `seed_demo` → PostgREST), reads back as a **live HubSpot aggregate** (300
synthetic contacts/deals pushed behind the four guards, read INV-6 aggregate-only), or comes from a
**stood-in adapter** (GA4) that is labeled, never implied live. The **Executive Command** Home reads
these straight from the owning modules — each widget carries a **● LIVE / ◐ stood-in / ○ sample** pill.

| Module | Where it lives | The dataset (synthetic) |
|---|---|---|
| **Nurture & Lifecycle** | live HubSpot aggregate + Supabase `app_form` | engagement mix **100 clicked / 100 opened / 100 cold** (300, 200 reachable); tiers **T1 128 / T2 3,100 / T3 1,124**; pipeline interest 63 · apply 62 · enroll 62 · tuition 64 · closed-lost 62; **126** handoffs; 24-hr SLA 33% |
| **CRM / Marketing Ops** | live HubSpot + Supabase⇄HubSpot parity | lead-score bands **36 / 68 / 65 / 65 / 66** (300, mean 53.8, hot 66); sync parity **83.3%**; **3 broken UTMs**; data-quality queue |
| **Admissions & VoC** | Supabase (migration 0042) | **7** objections (top: Cost 14 · Accreditation 11 · Gifted-enough 8) · **8** voice quotes (1 quote-of-week) · **6** feedback (75% 7-day closure) · **5** weekly admission stats · **4** content bridges |
| **Website & Digital** | GA4 **stood-in** adapter + Supabase (0043) | **2 sites · 15 pages**; **11,530 sessions / 28,570 pageviews**; 5 channels + 3 social platforms; **6 campaigns (3 broken UTM at origin)**; 6 PDFs (523/wk); funnel 11,530 → 460; 2 page-flags + 2 analysis-requests (Hub-owned) |
| **Budget Tracker** | the Hub (Supabase, migration 0030) | **$365K** plan / **$293K** spent (80%); workstreams Grassroots $150K · Content $80K · Guerrilla $45K · Ops $18K; >10% variance → Decision Queue |
| **Summer Camp** | Supabase + **real Stripe** test-mode | 4 campuses (350 seats), $975/seat, $260K target; revenue collected via signed Stripe webhook |
| **Grassroots / Field & Events / Content / Decision Queue** | Supabase (0035–0041) | ambassador roster + dual-source reconcile · month-grid event calendar · production kanban w/ **real Google Sheets sync** · leader-only first-class decision queue (auto-flags from budget/hot-families/events/website) |

The full generators are `backend/app/data/synthetic*.py` and each store's `seed_demo`; the seed scripts
that replay them into live Supabase live under `backend/scripts/` (+ session scratchpad). The exact
HubSpot read-back figures are recorded in [WRITEUP.md](WRITEUP.md).

---

## Architecture

```
Stripe ─webhook─▶ ┌───────────────────────────────────────────┐
HubSpot ◀──sync──▶│  Phase 1 backbone  (FastAPI, pure core)   │
Open Data ─query─▶│  • CRM↔DB seam + parity + conflict detect │──▶ Supabase
Google Sheets ◀──▶│  • idempotent payments (replay = NOOP)    │   (per-program
                  │  • per-program RLS isolation              │    stores +
                  │  • identity merge + dual-source reconcile │    Hub state)
                  │  • eval-gated AI edge (proposal-only)     │
                  └───────────────────┬───────────────────────┘
                                      │  FastAPI  (app/api/*)
                                      ▼
                        Next.js Hub (web/) — 13 modules, 3 roles
```

### Live deployment — three tiers

```
  Browser
     │  https://gtpulse-marketing-hub.vercel.app
     ▼
┌──────────────────────────┐   /api/* rewrite      ┌──────────────────────────────┐
│  Vercel  (Next.js Hub)   │ ───(GT_API_BASE_URL)─▶ │  Railway  (FastAPI backend)   │
│  • static + seed fallback│                        │  • holds service_role + tokens│
│  • Home: ●LIVE/◐/○ pills │ ◀──── JSON aggregates ──│  • CRM_MODE=live, cache-warmed│
└──────────────────────────┘                        └───────────────┬───────────────┘
                                                                     │ service_role (RLS-bypass, server-only)
                                                     live HubSpot ◀───┤
                                                                     ▼
                                                      Supabase  (Postgres · RLS ENFORCED)
```

- **Supabase = database.** RLS is enforced (RESTRICTIVE per-program policies + FORCE, deny-by-default);
  the anon/browser key can't read app rows — only the **`service_role`** key bypasses RLS, and it lives
  **only on the backend**.
- **Railway = backend.** The FastAPI container holds every secret (Supabase `service_role`, HubSpot
  token, JWT secret) as **encrypted env vars — none in git** (`backend/Dockerfile` + `.dockerignore`
  exclude `.env*`). `CACHE_WARM_SECONDS` keeps the live Supabase⇄HubSpot snapshot cache hot so the
  dashboard's sample→live flip is near-instant.
- **Vercel = frontend.** `next.config.mjs` proxies `/api/*` to the Railway backend (`GT_API_BASE_URL`,
  baked at build); when the backend is unreachable it falls back to labelled seed (○ SAMPLE) so it
  never blanks. *Why not host the backend on Vercel or Supabase? Vercel functions are short-lived /
  stateless (a poor fit for this app's import-time singletons + heavy deps); Supabase is the DB (Deno
  edge functions only, no Python host). Railway runs the long-lived container cleanly.*

- **Deterministic core owns all writes.** Every LLM result is a schema-validated *proposal* requiring
  human approval — the AI never writes state directly, and a red eval disables the action in the UI.
- **Ports & adapters.** Every external service sits behind an adapter with `simulate`/`live` impls,
  swappable by env, each with a hard cost cap + kill switch.
- **Single source of truth.** Supabase `app_form` owns funnel/TEFA/income/grade; HubSpot owns
  pipeline/engagement; the Hub owns budget. No figure is computed two ways.

The pure logic lives in `backend/app/core/` (seam, parity, payments, identity, the reconcilers — no
I/O); the adapters in `backend/app/adapters/`; the data model, synthetic generators, and Supabase
migrations (RLS + program isolation) in `backend/app/data/`.

---

## Test data is part of the deliverable

The data is generated, not shipped — `backend/app/data/synthetic*.py` (deterministic, seeded,
PII-safe: `@example.invalid` emails, `555-01xx` phones, aggregate regions only, no child-keyed data).
Cohorts scale from a 12-family on-camera demo to a ~5,150-family realistic set, with the **$365K**
budget partitioned to spec.

**Deliberate edge cases, built to stress the backbone:**

- **Duplicate households** (same email+region, typo'd phone) → feed the merge queue as REVIEW_QUEUE
- **Conflicting CRM-vs-app values** (~12%) → real divergent mirror values → seam conflicts + parity drop
- **Late / failed / duplicate payments** → exercised via the Stripe demo above
- **Mojibake / missing fields** → flagged by the data-quality detector
- **A family across two programs** + **per-program isolation** → Summer Camp vs Fall

Reset to a clean known state by re-running any demo script (they stand up fresh in-memory stores) or
re-seeding from the generators.

---

## Known gaps, surfaced honestly

The brief rewards honesty over fake green. Current limitations:

- **UTM attribution is broken** and **event-to-consult is uninstrumented** — surfaced in CRM Ops as
  red, per the spec, not hidden. (CRM Ops now also offers an audited repair that fixes the
  losslessly-repairable UTMs and queues the rest for a manual decision; the flag is data-driven.)
- **Some HubSpot fields are unreliable** — flagged by field-reliability, not silently trusted.
- **Stood-in sources** (Meta/GA4/X/summer.gt.school/community.gt.school) are seeded and labeled, not
  live. Event-to-consult and parent NPS are **manual / un-instrumented**, surfaced as such — never a
  faked auto-metric.
- **Sub-view tabs** are fully built out for the eleven deep modules (above); the remaining seed
  modules render their data behind the correct shape rather than fake per-tab depth.
- The Hub falls back to **seed data** when the backend isn't running — by design, so it's always
  demoable.

---

## Verify it yourself (the quality gate)

The committed git hooks (`.githooks/`) are the gate. Run the full suite:

```bash
python scripts/pii_scan.py                 # 1. PII / secret scan (fails on PII-shaped fixtures)
python scripts/check_dep_budget.py         # 2. runtime dependency budget
cd backend
uv run ruff check . && uv run ruff format --check .   # 3-4. lint + format
uv run mypy app                            # 5. strict types
uv run pytest -q                           # 6. tests  → 1451 passed, 6 skipped
cd ../web && npx tsc --noEmit              # frontend typecheck
```

The `pre-push` hook runs all six backend checks; `commit-msg` enforces Conventional Commits. Never
bypass with `--no-verify`.

---

## Repo layout

```
backend/            FastAPI backbone — app/{api,core,adapters,data,observability}
  app/core/         pure logic: seam, parity, payments, identity, reconcilers (no I/O)
  app/adapters/     ports & adapters: hubspot, payments, open_data, sheets, social, ...
  app/data/         synthetic generators, models, Supabase migrations (RLS + isolation)
  scripts/demo/     runnable walkthroughs (stripe_edge_cases.py)
web/                Next.js 14 Hub — components/modules/* (the 13 modules), lib/
params/             params.yaml — every tunable (budget, weights, eval thresholds, caps)
scripts/            pii_scan, dep budget, seed/provision scripts
```

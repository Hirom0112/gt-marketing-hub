# GT Technical Project — Write-up

## 1. What I built

A FastAPI backbone with a pure, side-effect-free core (`backend/app/core/`) that owns every
write, fronted by adapters for each external service (`backend/app/adapters/`), with a Next.js
Hub (`web/`) reading it over a thin REST surface (`backend/app/api/`). Phase 1 is the sync
engine: a Supabase↔HubSpot seam with conflict detection, an idempotent Stripe webhook, per-program
RLS isolation, and identity/dual-source reconciliation. Phase 2 is the Hub on top — it *spends*
that backbone rather than re-deriving it; the modules that matter call the same core the tests
exercise. All data is synthetic and generated (`backend/app/data/synthetic*.py`); real services are
wired live but default to `simulate`.

## 2. Deep vs. stubbed, and why

**The backbone is where I spent the depth** — it's what the brief actually tests, and every Phase-2
claim rests on it. On top of it I built **nine modules end-to-end** — each persisted to live Supabase
(migrations `0032`–`0041`, program-scoped RLS), wired across every sub-view tab, with real owner-gated
writes verified live. The backbone primitives are all real and unit-tested (**1351 passing**):

- **Stripe webhook** (`app/api/payments.py` → `core/payments.py:decide_payment_event`): verify HMAC
  on the raw body → dedupe on `event.id` → `FULFILL`/`NOOP`/`ACK` → record payment → advance the
  funding signal through the legal gate. *Proves idempotency and clean state propagation.*
- **Seam + parity** (`core/seam.py:derive_seam_status`; `core/parity.py:compute_parity`):
  last-write-wins with genuine conflict detection, rolled up to overall + per-field scores.
- **Program isolation** (RESTRICTIVE per-program RLS keyed on the JWT `app_metadata.program_id`, app
  connects as `app_runtime`/`NOBYPASSRLS`). *No cross-program bleed even on the server path.*
- **Dual-source reconcilers** (`core/identity.py:propose_merge`, `ambassador_reconcile`,
  `summer_reconcile`): exact-key match, count once, fail closed to a review queue on ambiguity.

**Modules built deep (live endpoint per tab, seed fallback when the API is down):**

- **Budget** (`/budget`) — workstreams sum to the `$365K` total from `params.budget`; burn series,
  per-owner spend gating, leadership re-plan, >10% variance → Decision Queue.
- **KPI Scorecard** (`/scorecard/*`) — per-metric provenance, trends, SLA, goal pacing; reads, owns nothing.
- **Decision Queue** (`/decisions`) — leader-only gate; first-class decision columns; cross-module
  auto-flags (budget variance, hot families, event proposals) + resolved-toast to submitters.
- **Grassroots** (`/grassroots/*`, `/ambassadors/reconcile`) — roster + dual-source reconcile, market
  map, referral sprints, parent community (honest stood-in for un-instrumented NPS), parent-led events.
- **Content** (`/content/*`) — production kanban with **real two-way Google Sheets sync**, editorial
  calendar + conflict detection, channel performance (UTM honesty), library, and an **advisory
  brand-voice auditor** (LLM-proposal → heuristic fallback; INV-2 — never writes state).
- **Summer Camp** (`/summer/*`) — dual-source reconcile + program isolation, funnel, sessions, and
  **revenue collected via real Stripe** (test-mode PaymentIntents → signed webhook → `camp_payment`
  ledger; revenue basis flips synthetic → `stripe_collected`).
- **Field & Events** (`/field/events/*`) — event tracker, a **month-grid calendar** overlaying
  Grassroots ambassador events **read-only**, priority-event proposals → Decision Queue.
- **Nurture & Lifecycle** (`/nurture/*`) — the most data-rich view, and the one that reads **live from
  HubSpot**: engagement-tier mix, deal-pipeline distribution, and marketing→onboarding handoff are
  **aggregate reads off the real HubSpot Pro portal** (300 synthetic contacts + deals pushed behind
  the four guards, read back INV-6 aggregate-only — counts by tier/stage, never a per-person row); the
  engagement×attribute heatmap **joins** that to the `app_form` source-of-truth; sequences + SMS inbox
  are a **labeled synthetic mirror** (the Sales-Hub Sequences / Conversations APIs aren't exposed in
  this portal — surfaced honestly, not faked live); plus SLA tracker, an owner-gated segment builder,
  and **four cross-links** (hot-family→Decision Queue, SMS-objection→Content brief, pipeline+handoff→
  KPI, conversion→Content Performance).
- **CRM / Marketing Operations** (`/crm/ops/*`, `/seam`) — data-infrastructure health on the Phase-1
  seam: sync-parity (overall + field-level) driving the always-on **data-confidence banner**, UTM
  health flagged **permanently broken** (never faked green), field-reliability flags (TEFA/income/source
  — a documented modeling call), a **live HubSpot lead-score histogram** (aggregate `gt_lead_score`
  bands, display-only), and a **persisted data-quality queue with auto-detection** (a scan upserts
  sync-drift + UTM-breakage issues idempotently on a signature) with a leadership lifecycle
  (acknowledge/prioritize/resolve) + resolution log, and a leader-only scoring-change → Decision Queue.

**Left as honest seed (real shape, labeled):** **Home, Admissions, Website Analytics** (GA4 stood-in),
**Resource Library.** These are breadth/aggregation/viz surfaces that don't further test the backbone.

## 3. Key technical trade-offs

- **Idempotency: a dedupe ledger, not "check-before-insert."** `stripe_events.event_id` is a PRIMARY
  KEY (`0026_stripe_payments.sql`); a redelivered event conflicts and is a `NOOP`. Rejected an
  application-level "have I seen this?" read — it races under concurrent redelivery; the PK makes
  exactly-once a database invariant.
- **Conflict resolution: detect + flag, never auto-resolve silently.** Tracked fields reconcile by
  recency (last-write-wins), but a genuine divergence with no clear winner becomes a surfaced
  `CONFLICT`, not a guess. Rejected auto-merge — a false merge is the IDOR-grade failure this avoids.
- **Isolation: one DB + RESTRICTIVE RLS, not separate schemas/projects.** RLS keyed on the JWT
  program claim is AND-ed with owner policies and holds on the server path too. Rejected schema- or
  project-per-program — heavier, and it moves the boundary out of the database where a leaked query
  would bypass it.
- **Dedup: exact normalized-key match, fail closed.** `propose_merge` matches on email+region with a
  phone tiebreak; same-key/conflicting-attr → `REVIEW_QUEUE`. Rejected fuzzy/threshold scoring — a
  tunable similarity cutoff invites false merges of minors' households; deterministic + human-gated is
  safer and testable.
- **No magic numbers.** Every tunable (the `$365K` split, `variance_threshold: 0.10`,
  `data_confidence.min_parity: 0.95`, the `summer_camp` capacities/price) lives in `params/params.yaml`
  and is read by tests, so a drifted constant fails the build.

## 4. Honoring / bending the spec's rules

- **Source of truth, honored:** Supabase `app_form` owns funnel/TEFA/income/grade; HubSpot owns
  contacts/deals/engagement; the Hub owns budget. Each number has one home; nothing is computed twice.
  *(One ratified tension: funnel **stage** is currently HubSpot-authoritative via the seam's
  last-write-wins, not `app_form` — a deliberate call, logged.)*
- **>10% budget variance auto-flags** to the Decision Queue from `params.budget.variance_threshold`.
- **Role gating** is real: the Decision Queue is leader-only, write affordances are owner-scoped
  (`canEditWorkstream`).
- **Data-confidence banner** trips because seeded parity sits well under `0.95` — ~12% of families
  carry genuinely divergent CRM mirror values (`deps.py` materializes the conflict tail), so it's real,
  not staged.
- **Known-broken, surfaced not faked:** UTM attribution is flagged `broken` by `core/utm_health`;
  unreliable fields are flagged by `core/field_reliability`; stood-in sources (Meta/GA4/X/the two
  `.gt.school` sites) are labeled `STOOD-IN`/`SIMULATED` in the UI.

## 5. With another week

1. Wire the remaining seed UIs (Admissions/VoC, Home, Website Analytics) to live endpoints — a React
   data-layer pass, not new logic. Also: widen the Nurture
   engagement×attribute heatmap's source cohort (today it joins to the 24-row default `app_form`
   sample, so per-cell conversion %s are small-sample-noisy — honest, but a larger seeded cohort
   would make the heatmap read cleanly).
2. Surface Open Data: the adapter and `/open-data/enrich` exist, but the specific decision a query
   *changes* isn't shown in the Hub yet.
3. The GT Challenge end-to-end: a public quiz → lead → program store → auto-score → a CAC/CPQL KPI
   row, exercising capture→assess→reconcile→report in one flow.
4. Replace the in-memory voucher-timeline no-op so a webhook fulfill mutates the funding *column*
   (today the legal advance is asserted via the decision/anomaly, not a field write — see the demo).

---

**Word count: ~900.**

**Verified vs. inferred:** all module/table/function names, the live-vs-seed split, the params
values, and the **1351-test count** are verified from the source this session. The nine deep modules
were each run against a **live API + live Supabase** (migrations applied, data seeded) and verified
end-to-end via Playwright across roles — including a real Stripe test-mode payment flow recorded
through the signed webhook, a real two-way Google Sheets sync, and **live HubSpot aggregate reads**
(engagement-tier mix + deal-pipeline distribution + handoff + the CRM-Ops lead-score histogram read
back off the real portal: clicked 100 / opened 100 / cold 100, interest 61 / apply·enroll·tuition·closed
60 each, 120 handoffs, lead-score bands 36/68/65/65/66). The
remaining modules (Home, Admissions, Website, Resource Library) render seed and are labeled
as such. The stage-SoT "ratified tension" is my call, recorded in the decisions log, not a spec
instruction.

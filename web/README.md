# GT Marketing Hub (`web/`)

The Phase-2 product from the GT School technical brief: a centralized marketing operating
console where every GT Anywhere marketing function has its own module, with hard role gates,
a composable per-user Home, and the cross-module rules the spec requires (single source of
truth per number, auto-flag cross-links, an app-wide data-confidence banner).

Built in **Next.js 14 (App Router) + TypeScript**. It consumes the existing Phase-1 backbone
in `../backend` (FastAPI + Supabase + live HubSpot + Stripe + Open Data) — the Hub does not
re-implement sync; it spends it.

## Run it

```bash
cd web
npm install
npm run dev        # http://localhost:3001  (redirects to /home)
```

`npm run build` does a full prod build + typecheck; `npm run typecheck` is types only.

The backend is optional for the seed-data UI; to wire the deep modules to live data, run the
FastAPI app from `../backend` on `:8000` (the Hub proxies `/api/*` to it — override with
`GT_API_BASE_URL`).

## The three roles (hard gates — a brief requirement)

Switch roles live in the sidebar footer ("VIEWING AS"). No real auth in v1; the `Session`
shape (`lib/registry.ts`) is auth-ready.

| Role | Sees | Can do |
|---|---|---|
| **Admin** (the Marketing Lead) | all modules | edit any workstream · submit decisions · **views the Decision Queue but never decides** |
| **Leader** (leadership) | all modules | **the only role that can approve / reject / need-info** the Decision Queue · set goals · comment anywhere |
| **Operator** (a function owner) | all modules (read) | edit **only owned** modules · submit into the Decision Queue from their module, but **cannot view the full queue** (sees only their own submissions) |

The operator demo identity is the Grassroots Owner (`ownedModules: ['grassroots']`), so Grassroots
shows an **OWN / EDITABLE** badge and every other module is **READ-ONLY**. Every gate lives in one
place: `lib/registry.ts`.

## Modules (13) — depth is deliberate (also a brief criterion)

Domain-grouped sidebar (COMMAND / GROWTH / OPERATIONS); the canonical spec index (01–13) rides as a
mono badge.

- **Deep:** Home (composable starter pack), Decision Queue (full role-gated workflow), KPI Scorecard,
  Budget Tracker (reconciles to `$365K`, >10% variance → Decision Queue), CRM Ops (sync parity /
  UTM-broken / data-quality queue), Nurture & Lifecycle (segments, heatmap, SLA).
- **Stubbed honestly** (spec-faithful brief: owner, summary, KPIs, cross-links, what's-broken note):
  Grassroots, Content, Summer Camp, Field & Events, Admissions, Website, Resource Library.

Known-broken things are surfaced, not faked green (UTM attribution broken, event-to-consult
uninstrumented, income/source/TEFA HubSpot fields unreliable → read from Supabase `app_form`).

## Brand

Ported from the approved design file (`../design-import/GT_Marketing_Hub.dc.html`): warm
"Analog Futurism" — cream paper, gold accent, signal red / ok green; **Archivo** (display),
**Public Sans** (UI), **IBM Plex Mono** (telemetry). Light default, dark toggle (⌥T). Tokens
live in `app/globals.css`.

## Layout

```
web/
  app/
    layout.tsx          root: fonts + providers + shell
    page.tsx            redirect → /home
    [module]/page.tsx   one route per registry module
    globals.css         brand tokens (light + dark)
  components/
    AppShell.tsx Sidebar.tsx TopBar.tsx TabBar.tsx ModuleRouter.tsx
    modules/            HomeModule, DecisionModule, BudgetModule, DashboardModule,
                        CrmModule, NurtureModule, GenericModule, LockedModule
  lib/
    registry.ts         the 13 modules, grouping, owners, tabs, ROLE GATES
    session.tsx         role + theme provider (the "VIEWING AS" switcher)
    decisions.ts        Decision Queue seed + helpers
    moduleData.ts       module briefs for the stubbed screens
```

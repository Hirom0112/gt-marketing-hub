// Spec-faithful "module brief" content for each module: a one-paragraph summary,
// three headline stats, cross-module links, and an honest "what's broken / manual
// v1" note. Ported from the design file's genData and extended to the detailed
// modules (dashboard, decision, budget, crm, nurture) until those get full
// sub-view builds. Stat colors use token vars: 'ink' | 'ok' | 'signal' | 'broken'.

import type { ModuleId } from './registry';

export type StatColor = 'ink' | 'ok' | 'signal' | 'broken' | 'gold' | 'warn';

export interface Stat {
  label: string;
  value: string;
  note: string;
  color?: StatColor;
}

export interface ModuleBrief {
  summary: string;
  stats: Stat[];
  links: string[];
  broken: string | null;
}

export const MODULE_BRIEFS: Partial<Record<ModuleId, ModuleBrief>> = {
  grassroots: {
    summary:
      'Parent-ambassador program, market map of gifted-family nodes, referral sprints, and the parent-led event calendar (ambassador events live HERE; Field Marketing reads them read-only). Reconciles HubSpot + community.gt.school. This is the demo Operator’s module — editable here, read-only elsewhere.',
    stats: [
      { label: 'AMBASSADORS ACTIVE', value: '47 / 25', note: 'goal exceeded', color: 'ok' },
      { label: 'WARM INTROS', value: '138 / 200', note: 'running total' },
      { label: 'INFLUENCED ENROLL.', value: '18 / 30', note: 'attribution chain' },
    ],
    links: ['CRM (reconcile dual source)', 'Field & Events (read-only)', 'Admissions (hot families)', 'Content (testimonial stubs)'],
    broken: null,
  },
  content: {
    summary:
      'Editorial calendar, production kanban (synced read+write to a Google Sheet), per-piece UTM attribution, and a brand-voice auditor in suggest-edits mode. Does NOT include summer-camp content (Module 4). Founder content in flight: Pam (AGL podcast), Joe (sizzle reel), advisor series.',
    stats: [
      { label: 'PRODUCTIONS IN FLIGHT', value: '14', note: '4 in review' },
      { label: 'PUBLISHED · MTD', value: '9 / 12', note: 'plan' },
      { label: 'X / TWITTER CONV.', value: '42%', note: 'the pre-sold engine' },
    ],
    links: ['Nurture (testimonials)', 'Admissions (objection briefs)', 'Resource Library'],
    broken:
      'UTM attribution per piece is unreliable — Module 7 tracks the rebuild. Substack + podcast counts are manual v1 (API later).',
  },
  camp: {
    summary:
      'GT summer-camp registrations, capacity, and revenue across 4 campuses (3× two-week + 1× one-week). Separate audience, timeline, and P&L from the Fall push. Reconciles summer.gt.school + a registration form without double-counting. No paid acquisition — ads are paused.',
    stats: [
      { label: 'CAPACITY SOLD', value: '82%', note: '288 / 350 seats' },
      { label: 'REGISTERED → PAID', value: '76%', note: 'reg form + summer.gt' },
      { label: 'REVENUE vs TARGET', value: '$214K', note: 'of $260K' },
    ],
    links: ['Content (camp content)', 'Phase-1 program isolation', 'KPI Scorecard'],
    broken: null,
  },
  events: {
    summary:
      'GT-organized external events — Shadow Days, chess tournaments, AMAs, festivals. Does NOT include ambassador-hosted events (those live in Grassroots, shown here read-only). Proposes priority events → leadership approves via Decision Queue.',
    stats: [
      { label: 'UPCOMING · 30D', value: '7', note: '3 Shadow Days' },
      { label: 'RSVP → ATTENDANCE', value: '64%', note: 'manual' },
      { label: 'EVENT → CONSULT', value: '——', note: 'uninstrumented', color: 'broken' },
    ],
    links: ['Grassroots (ambassador events, read-only)', 'Decision Queue (proposals)', 'Budget Tracker'],
    broken:
      'Event-to-consult conversion is uninstrumented. v1 is manual entry per event ("how many RSVPs booked a consult?"); auto-tracking deferred.',
  },
  nurture: {
    summary:
      'The most data-rich module — engagement tier is the top conversion predictor (clicked → 52% commit vs cold 16%). T1/T2/T3 segments, the engagement×attribute heatmap, parent+child pipeline stages, read-only HubSpot sequences, the SMS inbox, and the 24-hr SLA tracker.',
    stats: [
      { label: 'CLICKED TIER', value: '31%', note: 'top predictor', color: 'gold' },
      { label: '24-HR SLA', value: '78%', note: 'target 90% · 9 late', color: 'warn' },
      { label: 'HANDOFFS · WK', value: '26', note: 'marketing → onboarding' },
    ],
    links: ['CRM (lead score, parity)', 'Admissions (hot families, objections)', 'Content (UTM attribution)', 'KPI Scorecard'],
    broken: 'TEFA cohort frozen until ~2027 (selection closed Jun 1). Income/source fields unreliable in HubSpot — read funnel/income from Supabase app_form.',
  },
  dashboard: {
    summary:
      'The canonical, shared weekly scorecard — where Home is personal, this is the fixed board everyone references. "Are we hitting our numbers," versioned by week. Reads from every module; owns nothing. Also embeddable as a Home widget.',
    stats: [
      { label: 'KPIs TRACKED', value: '9', note: 'core weekly set' },
      { label: 'BIGGEST MOVER', value: 'Handoffs ▲18%', note: 'w/w', color: 'ok' },
      { label: 'AT RISK', value: '24-hr SLA', note: '78% vs 90%', color: 'signal' },
    ],
    links: ['All modules (primary metric)', 'Nurture (pipeline + handoff)', 'HubSpot Reporting API'],
    broken: 'Event-to-consult is uninstrumented (manual v1). UTM-broken attribution caps channel-ROI confidence.',
  },
  crm: {
    summary:
      'Data infrastructure health: UTM attribution, lead-scoring visibility (read-only from HubSpot), Supabase⇄HubSpot sync parity, and an auto-detecting data-quality queue. Source-of-truth reminder is always visible: funnel/TEFA/income read from Supabase app_form.',
    stats: [
      { label: 'SYNC PARITY', value: '96.2%', note: 'income/source/TEFA low', color: 'warn' },
      { label: 'UTM ATTRIBUTION', value: 'BROKEN', note: 'permanent red until fixed', color: 'signal' },
      { label: 'OPEN DQ ISSUES', value: '5', note: 'auto-detected + filed' },
    ],
    links: ['Broadcasts data-confidence banner → all modules', 'Content + Nurture (UTM/attribution)', 'KPI Scorecard (lead score)'],
    broken:
      'UTM attribution is broken end-to-end and surfaces as a permanent red flag until the rebuild lands. Income/source/TEFA HubSpot fields are unreliable by design — Supabase app_form is the source of truth.',
  },
  admissions: {
    summary:
      'Admission pipeline numbers + Voice of Customer. Surfaces family objections and closes the feedback loop to marketing — top objections auto-create content briefs in Module 3. "What are families saying, and what content answers it?"',
    stats: [
      { label: 'OBJECTIONS LOGGED · WK', value: '47', note: 'top: cost, accreditation' },
      { label: 'FAMILY SENTIMENT', value: '+47', note: 'NPS · n=212', color: 'ok' },
      { label: 'CONTENT BRIDGE HIT', value: '68%', note: 'briefs → published' },
    ],
    links: ['Content (auto-stub briefs)', 'Nurture (hot families)', 'Decision Queue'],
    broken: null,
  },
  website: {
    summary:
      'Website performance across gt.school and anywhere.gt.school. Sessions, subpage performance, PDF downloads, and conversion paths. Sessions and acquisition data are reliable; UTM-tagged attribution is broken (Module 7 tracks the fix).',
    stats: [
      { label: 'SESSIONS · 7D', value: '18.4K', note: 'both sites · GA4' },
      { label: 'TOP CHANNEL', value: 'Organic', note: '41% of sessions' },
      { label: 'UTM ATTRIBUTION', value: 'BROKEN', note: 'needs rebuild', color: 'signal' },
    ],
    links: ['CRM Ops (UTM source)', 'Content (top pages)', 'Resource Library (PDF downloads)'],
    broken:
      'UTM attribution is broken end-to-end (the website is where UTMs originate). GA4 cross-site linking between gt.school + anywhere.gt.school is still TBD. Sessions are trustworthy; channel ROI is not.',
  },
  budget: {
    summary:
      'Marketing budget plan vs committed vs actual vs remaining, by workstream. Each function owner enters their own spend; the Hub is the system of record (no Google Sheet). A >10% variance auto-flags into the Decision Queue.',
    stats: [
      { label: 'TOTAL PLAN', value: '$365K', note: '4 workstreams' },
      { label: 'COMMITTED', value: '$334K', note: '92% of plan' },
      { label: 'OVER PLAN', value: 'Guerrilla +18%', note: 'auto-flagged → DQ', color: 'signal' },
    ],
    links: ['Decision Queue (variance auto-flag)', 'All workstream owners (spend entry)', 'KPI Scorecard'],
    broken: null,
  },
  resources: {
    summary:
      'A flat, tag-filterable document shelf — strategy materials, persona dossiers, brand strategy, the outcomes tracker, Brainlifts. Deliberately simple: no automation, no versioning. Pre-loaded resources are mocked as sample uploads so the library has content to filter.',
    stats: [
      { label: 'RESOURCES', value: '38', note: 'mocked sample set' },
      { label: 'TYPES', value: '6', note: 'DOC·SHEET·SLIDES·PDF·MD·HTML' },
      { label: 'MOST ACCESSED', value: 'Persona v2', note: 'dossier' },
    ],
    links: ['Content', 'Website (PDF downloads)', 'Grassroots'],
    broken: null,
  },
  decision: {
    summary:
      'The async decision layer. Anyone submits an idea/proposal/budget ask from their module; leadership reviews and decides. Leadership-only to view + act (approve / reject / need-info); operators submit but cannot open the full queue. Budget variance and hot-family flags auto-route here.',
    stats: [
      { label: 'OPEN', value: '4', note: 'awaiting leadership' },
      { label: 'AUTO-FLAGGED', value: '2', note: 'budget + sync drift', color: 'gold' },
      { label: 'DECIDED · 30D', value: '11', note: 'full audit trail' },
    ],
    links: ['Budget (variance >10%)', 'Grassroots/Nurture (hot families)', 'Field & Events (event proposals)'],
    broken: null,
  },
};

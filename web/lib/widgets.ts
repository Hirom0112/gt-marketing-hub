// GT Marketing Hub — Home widget catalog (44 widgets).
// Pure data + types. No React, no 'use client'. Consumed by Home/widget pickers.

export type WidgetSize = 'small' | 'medium' | 'large';

export type Category =
  | 'Volume & conversion' | 'Audience & segments' | 'Funnel & pipeline'
  | 'Content & engagement' | 'Grassroots & ambassadors' | 'Voice of customer'
  | 'Narrative & sprint' | 'Calendar & budget' | 'Website';

export type WidgetContent =
  | { kind: 'stat'; value: string; delta?: string; deltaColor?: string; sub?: string }
  | { kind: 'progress'; value: string; pct: number; color?: string; sub?: string }
  | { kind: 'bars'; rows: { name: string; pct: string; width: number; muted?: boolean }[] }
  | { kind: 'split'; segs: { w: number; label: string; value: string; color: string; textColor?: string }[]; sub?: string }
  | { kind: 'tiers'; items: { n: string; label: string }[]; sub?: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'narrative'; fields: { label: string; text: string }[] };

export interface WidgetDef {
  id: string;          // kebab-case stable id
  n: number;           // 1..44 spec index within the overall library
  label: string;
  category: Category;
  source: string;      // data source tag
  homeModule: string;  // which module owns the number
  size: WidgetSize;    // default size
  content: WidgetContent;
}

export const WIDGETS: WidgetDef[] = [
  {
    id: 'applicants-total',
    n: 1,
    label: 'Applicants total + w/w delta',
    category: 'Volume & conversion',
    source: 'Supabase app_form',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'stat', value: '12', delta: '▲12 w/w', deltaColor: 'var(--ok)', sub: 'Supabase app_form funnel (all stages)' },
  },
  {
    id: 'deposits-vs-goal',
    n: 2,
    label: 'Deposits vs Fall goal (target 180)',
    category: 'Volume & conversion',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'progress', value: '0 / 180', pct: 0, color: 'var(--gold)', sub: '0% of the Fall goal · Stripe deposit ledger' },
  },
  {
    id: 'conversion-by-channel',
    n: 3,
    label: 'Conversion by channel (top 5)',
    category: 'Volume & conversion',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'X', pct: '42%', width: 100 },
        { name: 'Email', pct: '16%', width: 38 },
        { name: 'Podcast', pct: '15%', width: 36 },
        { name: 'Substack', pct: '13%', width: 31 },
        { name: 'Instagram', pct: '11%', width: 26 },
      ],
    },
  },
  {
    id: 'channel-volume-mix',
    n: 4,
    label: 'Channel volume mix',
    category: 'Volume & conversion',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'Facebook', pct: '38%', width: 100 },
        { name: 'Organic', pct: '27%', width: 71 },
        { name: 'X/Twitter', pct: '19%', width: 50 },
        { name: 'Ambassador', pct: '16%', width: 42 },
      ],
    },
  },
  {
    id: 'volume-conversion-quadrant',
    n: 5,
    label: 'Volume vs conversion quadrant',
    category: 'Volume & conversion',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'Facebook — high volume, low conversion (volume trap)',
        'X/Twitter — low volume, high conversion (pre-sold engine)',
        'Ambassador — mid volume, high conversion (scale this)',
        'Organic — steady volume, mid conversion (hold)',
      ],
    },
  },
  {
    id: 'deposits-per-week',
    n: 6,
    label: 'Deposits per week (8-wk)',
    category: 'Volume & conversion',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'Wk 1', pct: '8', width: 40 },
        { name: 'Wk 2', pct: '11', width: 55 },
        { name: 'Wk 3', pct: '9', width: 45 },
        { name: 'Wk 4', pct: '14', width: 70 },
        { name: 'Wk 5', pct: '13', width: 65 },
        { name: 'Wk 6', pct: '16', width: 80 },
        { name: 'Wk 7', pct: '18', width: 90 },
        { name: 'Wk 8', pct: '20', width: 100 },
      ],
    },
  },
  {
    id: 't1t2t3-counts',
    n: 7,
    label: 'T1/T2/T3 active counts + reachability',
    category: 'Audience & segments',
    source: 'Supabase + HubSpot',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'tiers',
      items: [
        { n: '128', label: 'T1 · 90% reach' },
        { n: '3,100', label: 'T2 · 60% reach' },
        { n: '1,124', label: 'T3 · 17% reach' },
      ],
      sub: 'Audience size + reachability by tier',
    },
  },
  {
    id: 'engagement-tier-mix',
    n: 8,
    label: 'Engagement tier mix',
    category: 'Audience & segments',
    source: 'HubSpot',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'split',
      segs: [
        { w: 33, label: 'Clicked', value: '33%', color: 'var(--gold)' },
        { w: 33, label: 'Opened', value: '33%', color: 'var(--gold)', textColor: 'var(--ink)' },
        { w: 34, label: 'Cold', value: '33%', color: 'var(--broken)' },
      ],
      sub: '200 of 300 reachable',
    },
  },
  {
    id: 't3-sub-buckets',
    n: 9,
    label: 'T3 sub-buckets',
    category: 'Audience & segments',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'ESA-planned', pct: '512', width: 100 },
        { name: 'ESA-ineligible', pct: '388', width: 76 },
        { name: 'No indicator', pct: '224', width: 44, muted: true },
      ],
    },
  },
  {
    id: 'geo-mix',
    n: 10,
    label: 'Geo mix TX vs out-of-state',
    category: 'Audience & segments',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'split',
      segs: [
        { w: 50, label: 'Texas', value: '50%', color: 'var(--signal)' },
        { w: 50, label: 'Out-of-state', value: '50%', color: 'var(--ink)' },
      ],
      sub: 'Even split — TX ESA pull vs national reach',
    },
  },
  {
    id: 'income-mix',
    n: 11,
    label: 'Income mix',
    category: 'Audience & segments',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: '<$65K', pct: '34%', width: 68 },
        { name: '$65–160K', pct: '47%', width: 100 },
        { name: '$160K+', pct: '19%', width: 40 },
      ],
    },
  },
  {
    id: 'grade-mix',
    n: 12,
    label: 'Grade mix',
    category: 'Audience & segments',
    source: 'Supabase',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'K–2', pct: '47%', width: 100 },
        { name: '3–5', pct: '28%', width: 60 },
        { name: '6–8', pct: '19%', width: 40 },
        { name: '9–12', pct: '6%', width: 13, muted: true },
      ],
    },
  },
  {
    id: 'top-personas',
    n: 13,
    label: 'Top 3 personas by volume',
    category: 'Audience & segments',
    source: 'Supabase + Persona Dossier v2',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'ESA-Optimizer Mom — 412 (K–2, TX, $65–160K)',
        'Faith-First Family — 298 (multi-child, organic)',
        'Microschool-Curious — 187 (X-sourced, high intent)',
      ],
    },
  },
  {
    id: 'lead-score-dist',
    n: 14,
    label: 'Lead score distribution',
    category: 'Audience & segments',
    source: 'HubSpot',
    homeModule: 'CRM Ops',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: '0–20', pct: '36', width: 53 },
        { name: '20–40', pct: '68', width: 100 },
        { name: '40–60', pct: '65', width: 96 },
        { name: '60–80', pct: '65', width: 96 },
        { name: '80–100', pct: '66', width: 97 },
      ],
    },
  },
  {
    id: 'funnel-stages',
    n: 15,
    label: 'Funnel stages',
    category: 'Funnel & pipeline',
    source: 'Supabase + HubSpot',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'Interest', pct: '63', width: 98 },
        { name: 'Apply', pct: '62', width: 97 },
        { name: 'Enroll', pct: '62', width: 97 },
        { name: 'Tuition', pct: '64', width: 100 },
        { name: 'Closed Lost', pct: '62', width: 97 },
      ],
    },
  },
  {
    id: 'pipeline-velocity',
    n: 16,
    label: 'Pipeline velocity',
    category: 'Funnel & pipeline',
    source: 'HubSpot',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'stat', value: '23.4 days', delta: '▼2.1 vs last mo', deltaColor: 'var(--ok)', sub: 'avg lead → deposit' },
  },
  {
    id: 'stuck-in-stage',
    n: 17,
    label: 'Stuck-in-stage',
    category: 'Funnel & pipeline',
    source: 'HubSpot',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'stat', value: '47', delta: '▲8 w/w', deltaColor: 'var(--warn)', sub: 'deals >14d no movement' },
  },
  {
    id: 'sla-24h',
    n: 18,
    label: '24-hr follow-up SLA',
    category: 'Funnel & pipeline',
    source: 'HubSpot',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'progress', value: '78%', pct: 78, color: 'var(--warn)', sub: 'target 90% · 9 late' },
  },
  {
    id: 'email-send-health',
    n: 19,
    label: 'Latest email send health',
    category: 'Content & engagement',
    source: 'HubSpot',
    homeModule: 'Content',
    size: 'small',
    content: { kind: 'stat', value: '41% open', delta: '6.2% click · 0.3% unsub', deltaColor: 'var(--ok)', sub: 'Shadow Day invite · 3,142 sent' },
  },
  {
    id: 'top-content',
    n: 20,
    label: 'Top content this week',
    category: 'Content & engagement',
    source: 'HubSpot',
    homeModule: 'Content',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        '“What is an ESA?” explainer — 4.1K views',
        'Shadow Day recap thread (X) — 2.8K impressions',
        'K–2 day-in-the-life reel — 1.9K plays',
      ],
    },
  },
  {
    id: 'content-pipeline-status',
    n: 21,
    label: 'Content pipeline status',
    category: 'Content & engagement',
    source: 'Google Sheet',
    homeModule: 'Content',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'In production', pct: '7', width: 70 },
        { name: 'Scheduled', pct: '5', width: 50 },
        { name: 'Live', pct: '12', width: 100 },
      ],
    },
  },
  {
    id: 'social-engagement',
    n: 22,
    label: 'Social engagement (FB+IG+X)',
    category: 'Content & engagement',
    source: 'Meta + X/Twitter',
    homeModule: 'Content',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'X/Twitter', pct: '9.2K', width: 100 },
        { name: 'Instagram', pct: '5.4K', width: 59 },
        { name: 'Facebook', pct: '3.1K', width: 34, muted: true },
      ],
    },
  },
  {
    id: 'ambassador-influenced',
    n: 23,
    label: 'Ambassador-influenced enrollments',
    category: 'Grassroots & ambassadors',
    source: 'Supabase ⋈ Ambassador DB',
    homeModule: 'Grassroots',
    size: 'small',
    content: { kind: 'progress', value: '18 / 30', pct: 60, color: 'var(--gold)', sub: '60% of ambassador target' },
  },
  {
    id: 'p2p-calls',
    n: 24,
    label: 'P2P calls this week',
    category: 'Grassroots & ambassadors',
    source: 'Manual + DB',
    homeModule: 'Grassroots',
    size: 'small',
    content: { kind: 'stat', value: '214', delta: '▲37 w/w', deltaColor: 'var(--ok)', sub: '63 reached · 29% pickup' },
  },
  {
    id: 'events-rsvps',
    n: 25,
    label: 'Events + RSVPs',
    category: 'Grassroots & ambassadors',
    source: 'Manual',
    homeModule: 'Field',
    size: 'small',
    content: { kind: 'stat', value: '6 events', delta: '184 RSVPs', deltaColor: 'var(--ok)', sub: 'next: Austin info night Jul 9' },
  },
  {
    id: 'referral-pool',
    n: 26,
    label: 'Referral pool size',
    category: 'Grassroots & ambassadors',
    source: 'Ambassador DB',
    homeModule: 'Grassroots',
    size: 'small',
    content: { kind: 'stat', value: '342', delta: '▲21 w/w', deltaColor: 'var(--ok)', sub: 'active referrers' },
  },
  {
    id: 'top-objections',
    n: 27,
    label: 'Top objections this week',
    category: 'Voice of customer',
    source: 'HubSpot Conversations + manual',
    homeModule: 'Admissions',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        '“Cost” — 14 this week',
        '“Accreditation” — 11 this week',
        '“Gifted Enough” — 8 this week',
      ],
    },
  },
  {
    id: 'sms-inbox-themes',
    n: 28,
    label: 'SMS inbox themes',
    category: 'Voice of customer',
    source: 'HubSpot Conversations API',
    homeModule: 'Nurture',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'ESA application status — 44 threads',
        'Shadow Day scheduling — 29 threads',
        'Tuition / payment plans — 17 threads',
      ],
    },
  },
  {
    id: 'havent-heard-back',
    n: 29,
    label: '"Haven\'t heard back" replies',
    category: 'Voice of customer',
    source: 'HubSpot Conversations API',
    homeModule: 'Nurture',
    size: 'small',
    content: { kind: 'stat', value: '23', delta: '▲5 w/w', deltaColor: 'var(--warn)', sub: 'families awaiting a reply' },
  },
  {
    id: 'hot-families',
    n: 30,
    label: 'Hot families flagged',
    category: 'Voice of customer',
    source: 'Manual',
    homeModule: 'Admissions',
    size: 'small',
    content: { kind: 'stat', value: '14', delta: '▲4 today', deltaColor: 'var(--ok)', sub: 'high-intent · call within 24h' },
  },
  {
    id: 'family-quote',
    n: 31,
    label: 'Family quote of the week',
    category: 'Voice of customer',
    source: 'Manual',
    homeModule: 'Admissions',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        '“I came in a skeptic about an app teaching my kid. I left realizing the app is the floor and the guides build everything on top of it.” — on curriculum',
      ],
    },
  },
  {
    id: 'executive-narrative',
    n: 32,
    label: 'Executive narrative (4 fields)',
    category: 'Narrative & sprint',
    source: 'Manual',
    homeModule: 'Home',
    size: 'large',
    content: {
      kind: 'narrative',
      fields: [
        { label: 'Topline', text: '112 deposits at 62% of the 180 Fall goal; pace projects ~168 by the Aug 17 cutoff. K–2 remains the engine.' },
        { label: 'Working', text: 'X/Twitter + Ambassador channels convert 3–4× Facebook. Shadow Day invites driving 41% opens and the strongest deposit lift.' },
        { label: 'Stuck', text: '24h follow-up SLA at 78% vs 90% target (9 late). 9–12 grade band and Facebook spend are dead weight.' },
        { label: 'Decisions', text: 'Reallocate Facebook budget to X + ambassadors; approve T3 ESA-planned re-engagement; staff up to clear the SLA gap.' },
      ],
    },
  },
  {
    id: 'workstream-health',
    n: 33,
    label: 'Workstream health grid (G/Y/R)',
    category: 'Narrative & sprint',
    source: 'Manual + live KPI',
    homeModule: 'Home',
    size: 'large',
    content: {
      kind: 'list',
      items: [
        'G · Nurture — deposits on pace · owner: Priya',
        'Y · Content — pipeline thin for August · owner: Marcus',
        'G · Grassroots — ambassador conversions strong · owner: Dana',
        'R · CRM Ops — 24h SLA breached, 47 deals stuck · owner: Lee',
        'Y · Budget — 64% burned vs 58% of timeline · owner: Sam',
        'G · Website — sessions + downloads up w/w · owner: Río',
      ],
    },
  },
  {
    id: 'decision-queue-preview',
    n: 34,
    label: 'Decision queue preview',
    category: 'Narrative & sprint',
    source: 'Decision Queue',
    homeModule: 'Decision Queue',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'Approve T3 ESA-planned re-engagement send (3,142) — awaiting leader',
        'Shift $40K Facebook → X/Ambassador — awaiting leader',
        'Add 1 admissions rep to clear SLA backlog — awaiting leader',
      ],
    },
  },
  {
    id: 'sprint-phase',
    n: 35,
    label: 'Sprint phase tracker',
    category: 'Narrative & sprint',
    source: 'Config',
    homeModule: 'Home',
    size: 'small',
    content: { kind: 'progress', value: 'Phase 2 of 5', pct: 40, color: 'var(--signal)', sub: 'Convert & Close · ends Jul 11' },
  },
  {
    id: 'wins-log',
    n: 36,
    label: 'Wins log',
    category: 'Narrative & sprint',
    source: 'Manual',
    homeModule: 'Home',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'Crossed 100 deposits (Jun 24)',
        'Ambassador program hit 18 influenced enrollments',
        'Shadow Day invite best-performing email of the cycle',
      ],
    },
  },
  {
    id: 'risks-blockers',
    n: 37,
    label: 'Risks + blockers',
    category: 'Narrative & sprint',
    source: 'Manual',
    homeModule: 'Home',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        'SLA breach risks cooling 9 hot families',
        'August content pipeline thin (5 scheduled)',
        'Facebook spend underperforming — reallocation pending decision',
      ],
    },
  },
  {
    id: 'days-to-cutoff',
    n: 38,
    label: 'Days to Aug 17 cutoff',
    category: 'Calendar & budget',
    source: 'Config',
    homeModule: 'Home',
    size: 'small',
    content: { kind: 'stat', value: '51d 14h', delta: 'Fall enrollment close', deltaColor: 'var(--signal)', sub: 'Aug 17 · final deposit deadline' },
  },
  {
    id: 'upcoming-events',
    n: 39,
    label: 'Upcoming events (next 30 days)',
    category: 'Calendar & budget',
    source: 'Manual',
    homeModule: 'Field',
    size: 'large',
    content: {
      kind: 'list',
      items: [
        'Jul 9 — Austin info night (62 RSVPs)',
        'Jul 13 — Shadow Day cohort C (28 families)',
        'Jul 19 — San Antonio ESA workshop (41 RSVPs)',
        'Aug 2 — Dallas open house (RSVPs opening)',
      ],
    },
  },
  {
    id: 'budget-burn',
    n: 40,
    label: 'Budget burn vs plan ($365K)',
    category: 'Calendar & budget',
    source: 'Hub (Budget)',
    homeModule: 'Budget',
    size: 'small',
    content: { kind: 'progress', value: '$293K / $365K', pct: 80, color: 'var(--gold)', sub: '80% of the $365K plan spent' },
  },
  {
    id: 'spend-by-workstream',
    n: 41,
    label: 'Spend by workstream (pie)',
    category: 'Calendar & budget',
    source: 'Hub (Budget)',
    homeModule: 'Budget',
    size: 'medium',
    content: {
      kind: 'bars',
      rows: [
        { name: 'Grassroots', pct: '$150K', width: 100 },
        { name: 'Content', pct: '$80K', width: 53 },
        { name: 'Guerrilla', pct: '$45K', width: 30 },
        { name: 'Ops', pct: '$18K', width: 12 },
      ],
    },
  },
  {
    id: 'website-sessions',
    n: 42,
    label: 'Website sessions this week',
    category: 'Website',
    source: 'GA4',
    homeModule: 'Website',
    size: 'small',
    content: { kind: 'stat', value: '11,530', delta: 'both sites', deltaColor: 'var(--ink-3)', sub: '28,570 pageviews this week' },
  },
  {
    id: 'top-landing-pages',
    n: 43,
    label: 'Top landing pages',
    category: 'Website',
    source: 'GA4',
    homeModule: 'Website',
    size: 'medium',
    content: {
      kind: 'list',
      items: [
        '/ — 5,200 views (▲8%)',
        '/tuition — 3,100 views (▲19%)',
        '/how-it-works — 2,400 views (▼4%)',
      ],
    },
  },
  {
    id: 'pdf-downloads',
    n: 44,
    label: 'PDF downloads this week',
    category: 'Website',
    source: 'GA4',
    homeModule: 'Website',
    size: 'small',
    content: { kind: 'stat', value: '523', delta: '▲16% w/w', deltaColor: 'var(--ok)', sub: 'top: GT-School-Tuition-and-ESA-Guide.pdf' },
  },
];

export const CATEGORY_ORDER: Category[] = [
  'Volume & conversion', 'Audience & segments', 'Funnel & pipeline',
  'Content & engagement', 'Grassroots & ambassadors', 'Voice of customer',
  'Narrative & sprint', 'Calendar & budget', 'Website',
];

// The default starter pack — deliberately live-forward so a fresh Home opens onto real
// backbone data (engagement, lead score, pipeline, objections, SLA, website, budget,
// decisions are all live), with the two leadership-narrative surfaces as labelled seed.
export const STARTER_IDS: string[] = [
  'engagement-tier-mix', 'lead-score-dist', 't1t2t3-counts',
  'funnel-stages', 'top-objections', 'sla-24h',
  'website-sessions', 'budget-burn', 'decision-queue-preview',
  'executive-narrative', 'workstream-health',
];

export function widgetById(id: string): WidgetDef | undefined {
  return WIDGETS.find(w => w.id === id);
}

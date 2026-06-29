'use client';

// Summer Camp (Module 4) data layer — typed shapes for the EXTENDED dual-source
// reconciler (GET /summer/reconcile), the camp content board (GET /summer/content),
// and the leadership cross-link (POST /summer/session-change). Each resource carries
// a distinct seed fallback (live ≠ sample) so a screen never blanks with the backbone
// down. Mirrors lib/content-api.ts / lib/grassroots-api.ts conventions.

// ---- GET /summer/reconcile (app/api/summer.py) -----------------------------
export interface SummerCampusRow {
  campus: string;
  capacity: number;
  registered: number;
  paid: number;
  lead: number;
  seats_remaining: number;
  pct_sold: number;
}
export interface SummerTotals {
  capacity: number;
  registered: number;
  paid: number;
  lead: number;
}
export interface SummerSourceRow {
  source: string;
  rows: number;
}
export interface SummerDedup {
  raw_source_rows: number;
  unique_registrations: number;
  duplicates_merged: number;
  sources: SummerSourceRow[];
  conflicts: unknown[];
}
// `basis` is surfaced honestly: "synthetic_paid_times_price" today; may become
// "stripe_collected" (with revenue_by_campus / collected_count) once Stripe lands.
export interface SummerRevenue {
  paid_registrations: number;
  price_per_seat_usd: number;
  revenue_usd: number;
  target_usd: number;
  pct_to_target: number;
  basis: string;
  collected_count?: number;
  revenue_by_campus?: { campus: string; revenue_usd: number }[];
}
export interface RegistrationChannel {
  channel: string;
  count: number;
  pct: number;
}
export interface FunnelStageRow {
  stage: string;
  count: number;
  drop_off_pct: number;
  pending: boolean;
}
export interface SummerSession {
  session_id: string;
  campus: string;
  starts_on: string; // YYYY-MM-DD
  ends_on: string;
  duration: string; // "1wk" | "2wk"
  capacity: number;
  status: string;
}
export interface WaitlistRow {
  campus: string;
  capacity: number;
  registered: number;
  waitlisted: number;
}
export interface AppliedFilters {
  campus: string | null;
  grade_band: string | null;
  source: string | null;
}
export interface SummerReconcile {
  program_id: string;
  per_campus: SummerCampusRow[];
  totals: SummerTotals;
  dedup: SummerDedup;
  revenue: SummerRevenue;
  registration_channels: RegistrationChannel[];
  funnel: FunnelStageRow[];
  registrations_this_week: number;
  days_to_camp_start: number;
  sessions: SummerSession[];
  waitlist: WaitlistRow[];
  applied_filters: AppliedFilters;
}

// ---- GET /summer/content (camp-tagged subset of the live kanban) ------------
export interface CampContentRow {
  title: string;
  type: string;
  stage: string;
  owner: string;
  channel: string;
  utm: string;
  target_date: string;
}
export interface CampContentColumn {
  stage: string;
  cards: CampContentRow[];
}
export interface CampContentSync {
  mode: 'live' | 'simulate';
  synced: boolean;
  tab: string | null;
  sheet_id: string | null;
}
export interface SummerContent {
  stages: string[];
  rows: CampContentRow[];
  columns: CampContentColumn[];
  sync: CampContentSync;
}

// ---- POST /summer/session-change ↔ DecisionResponse -------------------------
export interface SessionChangeBody {
  campus: string;
  change_type: string; // "pricing" | "session_dates" | "capacity" | …
  detail?: string;
  recommendation?: string;
  budget_ask?: number | null;
  priority?: string;
}
export interface DecisionResponse {
  id: string;
  source: string;
  state: string;
  question: string;
  workstream: string;
  raised_by: string;
  priority: string;
}

// ---- Slicer option spaces (verified live against the seed) ------------------
// Grade bands that carry registrations in the seed (9-12 is empty → omitted honestly).
export const GRADE_BANDS = ['K-2', '3-5', '6-8'] as const;
export const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: 'summer_site', label: 'summer.gt.school' },
  { value: 'registration_form', label: 'Registration form' },
];
export const CHANGE_TYPES: { value: string; label: string }[] = [
  { value: 'pricing', label: 'Pricing' },
  { value: 'session_dates', label: 'Session dates' },
  { value: 'capacity', label: 'Capacity' },
];

// ---- Per-campus presentation meta (the reconciler carries only the numbers) -
// City/region only; session dates + duration come LIVE from sessions[].
export const CAMPUS_META: Record<string, { city: string }> = {
  Austin: { city: 'Mueller campus' },
  Dallas: { city: 'Knox–Henderson campus' },
  Houston: { city: 'Heights campus' },
  'San Antonio': { city: 'Pearl campus' },
};
export const campusCity = (campus: string): string => CAMPUS_META[campus]?.city ?? '—';

// ---- Seed fallbacks (rendered only when the backbone is unreachable) --------
// Distinct from live → the screen honestly flips to "○ SAMPLE". Numbers mirror the
// backend demo seed (288/350 reg, 219 paid) so a static preview still reconciles.
export const SEED_RECONCILE: SummerReconcile = {
  program_id: 'summer_camp',
  per_campus: [
    { campus: 'Austin', capacity: 100, registered: 86, paid: 66, lead: 20, seats_remaining: 14, pct_sold: 86.0 },
    { campus: 'Dallas', capacity: 100, registered: 84, paid: 63, lead: 21, seats_remaining: 16, pct_sold: 84.0 },
    { campus: 'Houston', capacity: 90, registered: 78, paid: 60, lead: 18, seats_remaining: 12, pct_sold: 86.7 },
    { campus: 'San Antonio', capacity: 60, registered: 40, paid: 30, lead: 10, seats_remaining: 20, pct_sold: 66.7 },
  ],
  totals: { capacity: 350, registered: 288, paid: 219, lead: 69 },
  dedup: {
    raw_source_rows: 385,
    unique_registrations: 288,
    duplicates_merged: 97,
    sources: [
      { source: 'registration_form', rows: 192 },
      { source: 'summer_site', rows: 193 },
    ],
    conflicts: [],
  },
  revenue: {
    paid_registrations: 219,
    price_per_seat_usd: 975,
    revenue_usd: 213_525,
    target_usd: 260_000,
    pct_to_target: 82.1,
    basis: 'synthetic_paid_times_price',
  },
  registration_channels: [
    { channel: 'word_of_mouth', count: 120, pct: 41.7 },
    { channel: 'social', count: 70, pct: 24.3 },
    { channel: 'email', count: 56, pct: 19.4 },
    { channel: 'website', count: 42, pct: 14.6 },
  ],
  funnel: [
    { stage: 'Lead', count: 288, drop_off_pct: 0.0, pending: true },
    { stage: 'Registered', count: 288, drop_off_pct: 0.0, pending: false },
    { stage: 'Paid', count: 219, drop_off_pct: 24.0, pending: false },
    { stage: 'Attended', count: 0, drop_off_pct: 0.0, pending: true },
  ],
  registrations_this_week: 30,
  days_to_camp_start: 35,
  sessions: [
    { session_id: 'seed-austin', campus: 'Austin', starts_on: '2026-08-03', ends_on: '2026-08-14', duration: '2wk', capacity: 100, status: 'scheduled' },
    { session_id: 'seed-dallas', campus: 'Dallas', starts_on: '2026-08-03', ends_on: '2026-08-14', duration: '2wk', capacity: 100, status: 'scheduled' },
    { session_id: 'seed-houston', campus: 'Houston', starts_on: '2026-08-10', ends_on: '2026-08-21', duration: '2wk', capacity: 90, status: 'scheduled' },
    { session_id: 'seed-sanantonio', campus: 'San Antonio', starts_on: '2026-08-17', ends_on: '2026-08-21', duration: '1wk', capacity: 60, status: 'scheduled' },
  ],
  waitlist: [
    { campus: 'Austin', capacity: 100, registered: 86, waitlisted: 0 },
    { campus: 'Dallas', capacity: 100, registered: 84, waitlisted: 0 },
    { campus: 'Houston', capacity: 90, registered: 78, waitlisted: 0 },
    { campus: 'San Antonio', capacity: 60, registered: 40, waitlisted: 0 },
  ],
  applied_filters: { campus: null, grade_band: null, source: null },
};

export const SEED_CONTENT: SummerContent = {
  stages: ['Backlog', 'Drafting', 'Review', 'Scheduled', 'Live'],
  rows: [
    { title: 'Camp guide interviews', type: 'article', stage: 'Backlog', owner: 'the Content Owner', channel: 'Substack', utm: 'camp_guide_interviews', target_date: 'Jul 20' },
    { title: 'Pilot outcomes recap', type: 'article', stage: 'Drafting', owner: 'Pamela Hobart', channel: 'Substack', utm: 'camp_pilot_outcomes', target_date: 'Jul 22' },
    { title: 'Welcome kit content', type: 'social', stage: 'Review', owner: 'the Content Owner', channel: 'Instagram', utm: 'camp_welcome_kit', target_date: 'Jul 25' },
    { title: 'Camp day-in-the-life', type: 'video', stage: 'Scheduled', owner: 'the Content Owner', channel: 'YouTube', utm: 'camp_day_in_the_life', target_date: 'Jul 28' },
  ],
  columns: [
    { stage: 'Backlog', cards: [{ title: 'Camp guide interviews', type: 'article', stage: 'Backlog', owner: 'the Content Owner', channel: 'Substack', utm: 'camp_guide_interviews', target_date: 'Jul 20' }] },
    { stage: 'Drafting', cards: [{ title: 'Pilot outcomes recap', type: 'article', stage: 'Drafting', owner: 'Pamela Hobart', channel: 'Substack', utm: 'camp_pilot_outcomes', target_date: 'Jul 22' }] },
    { stage: 'Review', cards: [{ title: 'Welcome kit content', type: 'social', stage: 'Review', owner: 'the Content Owner', channel: 'Instagram', utm: 'camp_welcome_kit', target_date: 'Jul 25' }] },
    { stage: 'Scheduled', cards: [{ title: 'Camp day-in-the-life', type: 'video', stage: 'Scheduled', owner: 'the Content Owner', channel: 'YouTube', utm: 'camp_day_in_the_life', target_date: 'Jul 28' }] },
    { stage: 'Live', cards: [] },
  ],
  sync: { mode: 'live', synced: true, tab: 'Sheet1', sheet_id: null },
};

// ---- Honest revenue-basis label --------------------------------------------
// The cockpit shows WHERE a revenue number comes from, never a bare dollar figure.
export function revenueBasisLabel(basis: string): { label: string; live: boolean } {
  if (basis === 'stripe_collected') return { label: 'Stripe collected · test mode', live: true };
  if (basis === 'synthetic_paid_times_price') return { label: 'synthetic · paid × price', live: false };
  return { label: basis.replace(/_/g, ' '), live: false };
}

// ---- Channel chip styling for the registration-channel mix -----------------
const REG_CHANNEL_STYLE: Record<string, { label: string; color: string }> = {
  word_of_mouth: { label: 'Word of mouth', color: 'var(--gold)' },
  social: { label: 'Social', color: 'var(--signal)' },
  email: { label: 'Email', color: 'var(--warn)' },
  website: { label: 'Website', color: 'var(--ok)' },
};
export function regChannelLabel(ch: string): string {
  return REG_CHANNEL_STYLE[ch]?.label ?? ch.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
export function regChannelColor(ch: string): string {
  return REG_CHANNEL_STYLE[ch]?.color ?? 'var(--ink-3)';
}

// ---- Session-date formatting -----------------------------------------------
export function fmtSessionDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
export function fmtSessionRange(starts: string, ends: string): string {
  const s = fmtSessionDate(starts);
  const e = fmtSessionDate(ends);
  const year = new Date(`${ends}T00:00:00`).getFullYear();
  return `${s} – ${e}, ${year}`;
}

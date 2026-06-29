// Field Marketing & Events (Module 8) view-model: typed wire shapes for the live
// FastAPI endpoints (app/api/field_events.py), the request bodies for the
// owner-gated writes (create/patch) + the open propose path, a seed fallback per
// resource (so a screen never blanks when the backbone is down → honest "○ SAMPLE"),
// and the display helpers (event-type colors/labels, status chip styling, date
// formatting + month-grid math) the module renders with. Mirrors lib/camp-api.ts
// and lib/grassroots-api.ts.

// ---- READ wire shapes (GET /field/events*) ---------------------------------
// GET /field/events/overview — the 8a rollup (every figure computed, never faked).
export interface FieldOverview {
  upcoming_count: number;
  completed_this_month: number;
  total_rsvps: number;
  total_attendance: number;
  rsvp_to_attendance_pct: number;
  consults_booked_total: number;
  event_to_consult_pct: number;
  // HONESTY: event→consult is computed from a MANUALLY entered field (consults_booked),
  // NOT auto-instrumented — surfaced so the UI never implies tracking.
  event_to_consult_manual: boolean;
  top_event_type_by_attendance: { event_type: string; attendance: number } | null;
}

// GET /field/events — the tracker list (a bare array over the wire).
export interface FieldEventRow {
  event_id: string;
  event_name: string;
  event_type: string; // shadow_day | chess_tournament | ama | community_event | festival | webinar
  venue: string;
  event_date: string; // YYYY-MM-DD
  rsvp_count: number;
  attendance_count: number;
  consults_booked: number;
  status: string; // planning | confirmed | completed | cancelled
  owner: string;
  notes: string;
  materials: string;
  budget_usd: number;
}

// GET /field/events/calendar — the blended calendar (a bare array over the wire).
// `field` items are this module's events (read_only=false); `ambassador` items come
// from Module 2 (Grassroots), surfaced READ-ONLY (read_only=true, status=null).
export interface CalendarItem {
  source: 'field' | 'ambassador';
  event_id: string;
  event_name: string;
  event_type: string;
  event_date: string; // YYYY-MM-DD
  venue: string;
  status: string | null;
  read_only: boolean;
}

// ---- WRITE request bodies (owner gated; identity stamped server-side) -------
export interface FieldEventCreateRequest {
  event_name: string;
  event_type: string;
  venue?: string;
  event_date: string;
  rsvp_count?: number;
  attendance_count?: number;
  consults_booked?: number;
  status?: string;
  notes?: string;
  materials?: string;
  budget_usd?: number;
}
// Every field optional — only provided (non-null) fields change.
export type FieldEventUpdateRequest = Partial<Omit<FieldEventCreateRequest, 'event_name' | 'event_type' | 'event_date'>> & {
  event_name?: string;
  event_type?: string;
  event_date?: string;
};

// POST /field/events/proposal — a priority recommendation (open to any seat).
export interface EventProposalRequest {
  name: string;
  recommendation?: string;
  budget_ask?: number | null;
  due_date?: string | null;
  priority?: string; // normal | urgent
}
// DecisionResponse returned by the proposal feeder (a subset we render).
export interface DecisionResponse {
  id: string;
  source: string;
  state: string;
  question: string;
  workstream: string;
  raised_by: string;
  recommendation?: string;
  budget_ask?: number | null;
  priority: string;
}

// ===========================================================================
// Display helpers — event-type color map + labels, status chips, dates.
// No invented color: tokens only (var(--…)). Mirrors ContentModule's CHANNEL_COLOR.
// ===========================================================================
export const EVENT_TYPE_COLOR: Record<string, { bg: string; fg: string }> = {
  // field-event types
  shadow_day: { bg: 'var(--gold-soft)', fg: 'var(--gold)' },
  chess_tournament: { bg: 'var(--signal-soft)', fg: 'var(--signal)' },
  ama: { bg: 'var(--ok-soft)', fg: 'var(--ok)' },
  community_event: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  festival: { bg: 'var(--warn-soft)', fg: 'var(--warn)' },
  webinar: { bg: 'var(--gold-soft)', fg: 'var(--brand)' },
  // ambassador-event types (Module 2) — rendered read-only on the overlay
  coffee_chat: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  qa: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  school_visit: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  virtual: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
};
export function eventTypeColor(t: string): { bg: string; fg: string } {
  return EVENT_TYPE_COLOR[t?.toLowerCase()] ?? { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' };
}

const EVENT_TYPE_LABEL: Record<string, string> = {
  shadow_day: 'Shadow day',
  chess_tournament: 'Chess tournament',
  ama: 'AMA',
  community_event: 'Community event',
  festival: 'Festival',
  webinar: 'Webinar',
  coffee_chat: 'Coffee chat',
  qa: 'Open house Q&A',
  school_visit: 'School visit',
  virtual: 'Virtual',
};
export function eventTypeLabel(t: string): string {
  return EVENT_TYPE_LABEL[t?.toLowerCase()] ?? (t || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// The field-event types an owner can log (the write form's options).
export const FIELD_EVENT_TYPE_OPTIONS = [
  'shadow_day', 'chess_tournament', 'ama', 'community_event', 'festival', 'webinar',
] as const;

// Status → label + color tokens (lifecycle of a field event).
const STATUS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  planning: { label: 'PLANNING', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
  confirmed: { label: 'CONFIRMED', color: 'var(--gold)', bg: 'var(--gold-soft)' },
  completed: { label: 'COMPLETED', color: 'var(--ok)', bg: 'var(--ok-soft)' },
  cancelled: { label: 'CANCELLED', color: 'var(--signal)', bg: 'var(--signal-soft)' },
};
export function statusStyle(status: string | null | undefined) {
  return STATUS_STYLE[(status ?? '').toLowerCase()] ?? { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
export const STATUS_OPTIONS = ['planning', 'confirmed', 'completed', 'cancelled'] as const;

// "YYYY-MM-DD" → "Jun 5" (compact). Empty/invalid → "—".
export function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
// "YYYY-MM-DD" → "May 26, 2026".
export function fmtLongDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ---- month-grid math -------------------------------------------------------
export interface MonthKey { year: number; month: number; } // month: 0-11
export function monthOf(iso: string): MonthKey {
  const d = new Date(`${iso}T00:00:00`);
  return { year: d.getFullYear(), month: d.getMonth() };
}
export function monthLabel(m: MonthKey): string {
  return new Date(m.year, m.month, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}
export function sameMonth(a: MonthKey, b: MonthKey): boolean {
  return a.year === b.year && a.month === b.month;
}
export function addMonth(m: MonthKey, delta: number): MonthKey {
  const d = new Date(m.year, m.month + delta, 1);
  return { year: d.getFullYear(), month: d.getMonth() };
}
// The day-number a YYYY-MM-DD falls on (1-31).
export function dayOf(iso: string): number {
  return new Date(`${iso}T00:00:00`).getDate();
}
// Build the 6×7 day matrix (Sun-first) for a month — 0 = blank pad cell.
export function monthMatrix(m: MonthKey): number[][] {
  const first = new Date(m.year, m.month, 1).getDay(); // 0=Sun
  const days = new Date(m.year, m.month + 1, 0).getDate();
  const cells: number[] = [];
  for (let i = 0; i < first; i++) cells.push(0);
  for (let d = 1; d <= days; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(0);
  const rows: number[][] = [];
  for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7));
  return rows;
}

// ===========================================================================
// Seed fallbacks (rendered only when the backbone is unreachable → "○ SAMPLE").
// Mirror the backend demo seed so a static preview still reads true.
// ===========================================================================
export const SEED_OVERVIEW: FieldOverview = {
  upcoming_count: 2,
  completed_this_month: 2,
  total_rsvps: 230,
  total_attendance: 96,
  rsvp_to_attendance_pct: 42,
  consults_booked_total: 28,
  event_to_consult_pct: 12,
  event_to_consult_manual: true,
  top_event_type_by_attendance: { event_type: 'ama', attendance: 41 },
};

export const SEED_EVENTS: FieldEventRow[] = [
  { event_id: 'fe0', event_name: 'Shadow Day at Mueller campus', event_type: 'shadow_day', venue: 'Austin metro', event_date: '2026-05-26', rsvp_count: 28, attendance_count: 22, consults_booked: 9, status: 'completed', owner: 'events', notes: 'Strong turnout; 9 consults booked on-site.', materials: 'Tour decks, lanyards', budget_usd: 1500 },
  { event_id: 'fe1', event_name: 'Fall Open Chess Tournament', event_type: 'chess_tournament', venue: 'Plano', event_date: '2026-06-03', rsvp_count: 40, attendance_count: 33, consults_booked: 7, status: 'completed', owner: 'events', notes: 'Co-hosted with the regional chess league.', materials: 'Boards, trophies, banner', budget_usd: 2200 },
  { event_id: 'fe2', event_name: 'Founder AMA (live webinar)', event_type: 'ama', venue: 'Online', event_date: '2026-06-09', rsvp_count: 55, attendance_count: 41, consults_booked: 12, status: 'completed', owner: 'events', notes: 'Highest consult yield of the quarter.', materials: 'Slides, recording', budget_usd: 300 },
  { event_id: 'fe6', event_name: 'Downtown street fair booth', event_type: 'festival', venue: 'Houston', event_date: '2026-06-20', rsvp_count: 0, attendance_count: 0, consults_booked: 0, status: 'cancelled', owner: 'events', notes: 'Cancelled — vendor permit fell through.', materials: '', budget_usd: 0 },
  { event_id: 'fe3', event_name: 'Robotics Festival booth', event_type: 'festival', venue: 'Round Rock', event_date: '2026-06-23', rsvp_count: 35, attendance_count: 0, consults_booked: 0, status: 'confirmed', owner: 'events', notes: 'Booth confirmed; volunteers assigned.', materials: 'Booth kit, flyers', budget_usd: 1800 },
  { event_id: 'fe4', event_name: 'Community open house', event_type: 'community_event', venue: 'Frisco', event_date: '2026-06-30', rsvp_count: 24, attendance_count: 0, consults_booked: 0, status: 'confirmed', owner: 'events', notes: 'Evening session for working parents.', materials: 'Welcome packets', budget_usd: 900 },
  { event_id: 'fe5', event_name: 'Admissions info webinar', event_type: 'webinar', venue: 'Online', event_date: '2026-07-07', rsvp_count: 48, attendance_count: 0, consults_booked: 0, status: 'planning', owner: 'events', notes: 'Draft agenda; date holds.', materials: 'Slides (draft)', budget_usd: 250 },
];

export const SEED_CALENDAR: CalendarItem[] = [
  ...SEED_EVENTS.map((e): CalendarItem => ({ source: 'field', event_id: e.event_id, event_name: e.event_name, event_type: e.event_type, event_date: e.event_date, venue: e.venue, status: e.status, read_only: false })),
  { source: 'ambassador', event_id: 'ae0', event_name: 'Coffee chat with prospective parents', event_type: 'coffee_chat', event_date: '2026-06-05', venue: 'Austin metro', status: null, read_only: true },
  { source: 'ambassador', event_id: 'ae1', event_name: 'Robotics open house Q&A', event_type: 'qa', event_date: '2026-06-10', venue: 'Plano', status: null, read_only: true },
  { source: 'ambassador', event_id: 'ae2', event_name: 'Campus visit morning', event_type: 'school_visit', event_date: '2026-06-21', venue: 'Round Rock', status: null, read_only: true },
  { source: 'ambassador', event_id: 'ae3', event_name: 'Virtual info session', event_type: 'virtual', event_date: '2026-06-27', venue: 'Online', status: null, read_only: true },
];

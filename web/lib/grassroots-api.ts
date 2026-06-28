// Grassroots Engine (Module 2) view-model: typed wire shapes for the live
// FastAPI endpoints (app/api/grassroots.py), the request bodies for the
// owner-gated writes + cross-module links, a seed fallback per resource (so a
// screen never blanks when the backbone is down), and the small display helpers
// (goal labels, status/health styling, date formatting) the module renders with.
// Mirrors how lib/decisions.ts / lib/api.ts structure their view models.

// ---- READ wire shapes (GET /grassroots/*) ----------------------------------
export interface GoalBar {
  key: string; // active_ambassadors | warm_intros | p2p_calls | influenced_enrollments
  value: number;
  target: number;
  pct: number;
}
export interface OverviewResponse {
  goals: GoalBar[];
  pipeline: Record<string, number>; // prospect/outreached/onboarded/active/champion
  headline: Record<string, number>; // ambassadors_total/sprints_total/.../events_upcoming
}

export type Provenance = 'both' | 'hubspot-only' | 'community-only';

export interface AmbassadorRow {
  ambassador_id: string;
  synthetic_name: string;
  synthetic_email: string;
  segment: string;
  region: string;
  status: string; // prospect|outreached|onboarded|active|champion (lowercase)
  intros: number;
  p2p_calls: number;
  last_touch: string | null; // YYYY-MM-DD
  owner: string;
  provenance?: Provenance | null; // dual-source origin where the email matched a source
}

export interface CategorySummary {
  category: string;
  total: number;
  contacted: number;
  leads: number;
  coverage_pct: number;
}
export interface MarketNodeRow {
  node_id: string;
  category: string;
  contact_label: string;
  status: string; // cold|outreach|in_conversation|active|closed
  leads_generated: number;
  last_activity: string | null;
  owner: string;
}
export interface MarketMapResponse {
  nodes: MarketNodeRow[];
  summary: CategorySummary[];
}

export interface SprintRow {
  sprint_id: string;
  name: string;
  window_start: string; // YYYY-MM-DD
  window_end: string;
  ambassadors_enlisted: number;
  families_identified: number;
  conversions: number;
  status: string; // active|...
  health: string; // on_pace | behind
}

export interface EventRow {
  event_id: string;
  event_name: string;
  host_ambassador_id: string | null;
  event_type: string; // coffee_chat|qa|school_visit|virtual|...
  date: string; // YYYY-MM-DD
  location_label: string;
  rsvp_count: number;
  attendance_count: number;
  conversions_influenced: number;
}

// The dual-source reconcile (GET /ambassadors/reconcile) — kept for the
// RECONCILED badge (matched/conflicts/freshness) + per-row conflict overlay,
// joined onto the live roster by synthetic_email.
export interface ReconcileRow {
  synthetic_name: string;
  synthetic_email: string;
  segment: string;
  region: string;
  status: string;
  intros: number;
  p2p: number;
  last_touch: string;
  provenance: Provenance;
  has_conflict: boolean;
  conflicting_fields: string[];
}
export interface ReconcileResponse {
  union: ReconcileRow[];
  conflicts: { synthetic_name: string; synthetic_email?: string; field: string; hubspot_value: string; community_value: string }[];
  counts: { union: number; matched: number; hubspot_only: number; community_only: number; conflicts: number };
  sources: { name: string; count: number; synced_minutes_ago: number; healthy: boolean }[];
  source_health: string;
  reconciled_minutes_ago: number;
}

// ---- WRITE request bodies (identity is ALWAYS stamped server-side) ----------
export interface MarketNodeRequest {
  node_id?: string | null;
  category: string;
  contact_label?: string;
  status?: string;
  leads_generated?: number;
  last_activity?: string | null;
}
export interface SprintRequest {
  name: string;
  window_start: string;
  window_end: string;
  ambassadors_enlisted?: number;
  families_identified?: number;
  conversions?: number;
  status?: string;
}
export interface EventRequest {
  event_name: string;
  host_ambassador_id?: string | null;
  event_type?: string;
  date: string;
  location_label?: string;
  rsvp_count?: number;
  attendance_count?: number;
  conversions_influenced?: number;
}
export interface HotFamilyRequest {
  family_label: string;
  reason?: string;
  recommendation?: string;
  budget_ask?: number | null;
  due_date?: string | null;
  priority?: string; // normal | urgent
}
export interface TestimonialRequest {
  title: string;
  quote: string;
  attribution_label?: string;
}

// ===========================================================================
// Display helpers — labels, styling tokens, formatting. No invented color.
// ===========================================================================
export const GOAL_LABEL: Record<string, string> = {
  active_ambassadors: 'Ambassadors active',
  warm_intros: 'Warm intros',
  p2p_calls: 'P2P calls logged',
  influenced_enrollments: 'Influenced enrollments',
};
export function goalLabel(key: string): string {
  return GOAL_LABEL[key] ?? key.replace(/_/g, ' ');
}

// The pipeline funnel order (low → high commitment).
export const PIPELINE_ORDER = ['prospect', 'outreached', 'onboarded', 'active', 'champion'];
export function pipelineLabel(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

// Ambassador status → label + color tokens (status arrives lowercase from the store).
const AMB_STATUS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  champion: { label: 'CHAMPION', color: 'var(--gold)', bg: 'var(--gold-soft)' },
  active: { label: 'ACTIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' },
  onboarded: { label: 'ONBOARDED', color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  outreached: { label: 'OUTREACHED', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
  prospect: { label: 'PROSPECT', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};
export function ambStatusStyle(status: string) {
  return AMB_STATUS_STYLE[status?.toLowerCase()] ?? { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}

// Market-map node status → chip styling.
const MAP_STATUS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  active: { label: 'ACTIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' },
  in_conversation: { label: 'IN CONVO', color: 'var(--gold)', bg: 'var(--gold-soft)' },
  outreach: { label: 'OUTREACH', color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  cold: { label: 'COLD', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
  closed: { label: 'CLOSED', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};
export function mapStatusStyle(status: string) {
  return MAP_STATUS_STYLE[status?.toLowerCase()] ?? { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
export const MAP_STATUS_OPTIONS = ['cold', 'outreach', 'in_conversation', 'active', 'closed'];

// Sprint health → chip styling (on_pace | behind).
export function sprintHealthStyle(health: string) {
  if (health === 'on_pace') return { label: 'ON PACE', color: 'var(--ok)', bg: 'var(--ok-soft)' };
  if (health === 'behind') return { label: 'BEHIND', color: 'var(--warn)', bg: 'var(--warn-soft)' };
  return { label: (health || '—').toUpperCase().replace(/_/g, ' '), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}

// Event type → human label.
const EVENT_TYPE_LABEL: Record<string, string> = {
  coffee_chat: 'Coffee chat',
  qa: 'Open house Q&A',
  school_visit: 'School visit',
  virtual: 'Virtual',
  info_night: 'Info night',
  meetup: 'Meetup',
  open_house: 'Open house',
};
export function eventTypeLabel(t: string): string {
  return EVENT_TYPE_LABEL[t] ?? (t || '—').replace(/_/g, ' ');
}
export const EVENT_TYPE_OPTIONS = ['coffee_chat', 'qa', 'school_visit', 'virtual', 'info_night', 'meetup', 'open_house'];

// "YYYY-MM-DD" → "Jun 5" (compact). Empty/invalid → "—".
export function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ===========================================================================
// Seed fallbacks (rendered when the backbone is unreachable — honest "○ SAMPLE").
// Deliberately distinct from the live seed so a live render is unmistakable.
// ===========================================================================
export const SEED_OVERVIEW: OverviewResponse = {
  goals: [
    { key: 'active_ambassadors', value: 18, target: 25, pct: 72 },
    { key: 'warm_intros', value: 150, target: 200, pct: 75 },
    { key: 'p2p_calls', value: 38, target: 50, pct: 76 },
    { key: 'influenced_enrollments', value: 22, target: 30, pct: 73 },
  ],
  pipeline: { prospect: 4, outreached: 4, onboarded: 4, active: 13, champion: 5 },
  headline: {
    ambassadors_total: 30, sprints_total: 2, sprints_active: 2,
    market_nodes_total: 7, events_total: 4, events_upcoming: 0,
  },
};

export const SEED_AMBASSADORS: AmbassadorRow[] = [
  { ambassador_id: 's0', synthetic_name: 'GR Ambassador 00', synthetic_email: 'fields.214@example.invalid', segment: 'Robotics parents', region: 'Austin metro', status: 'champion', intros: 18, p2p_calls: 6, last_touch: '2026-06-14', owner: 'grassroots', provenance: 'both' },
  { ambassador_id: 's1', synthetic_name: 'GR Ambassador 01', synthetic_email: 'bell.731@example.invalid', segment: 'Homeschool co-op', region: 'Plano', status: 'champion', intros: 16, p2p_calls: 5, last_touch: '2026-06-13', owner: 'grassroots', provenance: 'both' },
  { ambassador_id: 's2', synthetic_name: 'GR Ambassador 02', synthetic_email: 'nair.118@example.invalid', segment: 'Chess club', region: 'Round Rock', status: 'active', intros: 9, p2p_calls: 4, last_touch: '2026-06-12', owner: 'grassroots', provenance: null },
  { ambassador_id: 's3', synthetic_name: 'GR Ambassador 03', synthetic_email: 'carter.552@example.invalid', segment: 'Math circle', region: 'Frisco', status: 'onboarded', intros: 3, p2p_calls: 1, last_touch: '2026-06-08', owner: 'grassroots', provenance: 'hubspot-only' },
  { ambassador_id: 's4', synthetic_name: 'GR Ambassador 04', synthetic_email: 'rahman.903@example.invalid', segment: 'Parent group', region: 'Houston', status: 'outreached', intros: 0, p2p_calls: 0, last_touch: '2026-06-04', owner: 'grassroots', provenance: 'community-only' },
];

export const SEED_MARKET: MarketMapResponse = {
  nodes: [
    { node_id: 'm0', category: 'Parent groups', contact_label: 'Austin parent group list', status: 'active', leads_generated: 9, last_activity: '2026-06-13', owner: 'grassroots' },
    { node_id: 'm1', category: 'Robotics teams', contact_label: 'Plano robotics parents', status: 'active', leads_generated: 7, last_activity: '2026-06-14', owner: 'grassroots' },
    { node_id: 'm2', category: 'Homeschool co-ops', contact_label: 'Hill Country homeschool co-op', status: 'in_conversation', leads_generated: 5, last_activity: '2026-06-11', owner: 'grassroots' },
    { node_id: 'm3', category: 'Math circles', contact_label: 'Frisco math circle', status: 'in_conversation', leads_generated: 4, last_activity: '2026-06-12', owner: 'grassroots' },
    { node_id: 'm4', category: 'Chess clubs', contact_label: 'Round Rock chess club', status: 'outreach', leads_generated: 2, last_activity: '2026-06-09', owner: 'grassroots' },
    { node_id: 'm5', category: 'Debate leagues', contact_label: 'DFW debate league', status: 'cold', leads_generated: 0, last_activity: '2026-05-26', owner: 'grassroots' },
    { node_id: 'm6', category: 'STEM meetups', contact_label: 'Houston STEM meetup', status: 'cold', leads_generated: 0, last_activity: '2026-05-20', owner: 'grassroots' },
  ],
  summary: [
    { category: 'Parent groups', total: 1, contacted: 1, leads: 9, coverage_pct: 100 },
    { category: 'Robotics teams', total: 1, contacted: 1, leads: 7, coverage_pct: 100 },
    { category: 'Homeschool co-ops', total: 1, contacted: 1, leads: 5, coverage_pct: 100 },
    { category: 'Math circles', total: 1, contacted: 1, leads: 4, coverage_pct: 100 },
    { category: 'Chess clubs', total: 1, contacted: 1, leads: 2, coverage_pct: 100 },
    { category: 'Debate leagues', total: 1, contacted: 0, leads: 0, coverage_pct: 0 },
    { category: 'STEM meetups', total: 1, contacted: 0, leads: 0, coverage_pct: 0 },
  ],
};

export const SEED_SPRINTS: SprintRow[] = [
  { sprint_id: 'sp0', name: 'Back-to-school referral push', window_start: '2026-06-01', window_end: '2026-06-29', ambassadors_enlisted: 8, families_identified: 20, conversions: 12, status: 'active', health: 'behind' },
  { sprint_id: 'sp1', name: 'Robotics-season referral sprint', window_start: '2026-05-25', window_end: '2026-06-22', ambassadors_enlisted: 6, families_identified: 18, conversions: 10, status: 'active', health: 'behind' },
];

export const SEED_EVENTS: EventRow[] = [
  { event_id: 'e0', event_name: 'Coffee chat with prospective parents', host_ambassador_id: null, event_type: 'coffee_chat', date: '2026-06-05', location_label: 'Austin metro', rsvp_count: 14, attendance_count: 11, conversions_influenced: 3 },
  { event_id: 'e1', event_name: 'Robotics open house Q&A', host_ambassador_id: null, event_type: 'qa', date: '2026-06-10', location_label: 'Plano', rsvp_count: 22, attendance_count: 18, conversions_influenced: 4 },
  { event_id: 'e2', event_name: 'Campus visit morning', host_ambassador_id: null, event_type: 'school_visit', date: '2026-06-21', location_label: 'Round Rock', rsvp_count: 16, attendance_count: 0, conversions_influenced: 0 },
  { event_id: 'e3', event_name: 'Virtual info session', host_ambassador_id: null, event_type: 'virtual', date: '2026-06-27', location_label: 'Online', rsvp_count: 30, attendance_count: 0, conversions_influenced: 0 },
];

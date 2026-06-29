// Nurture & Lifecycle (Module 5) view-model: typed wire shapes for the live FastAPI
// endpoints (app/api/nurture.py), the owner-gated write bodies (segment builder +
// the SMS cross-links), a seed fallback per resource (so a screen never blanks when
// the backbone is down → honest "○ SAMPLE"), and the display helpers (engagement-tier
// chips, SMS status/theme styling, heatmap color ramp, source-provenance badges, date
// formatting). Mirrors lib/events-api.ts / lib/camp-api.ts conventions.
//
// HONESTY: every response carries a `source` (and per-thread `tag_mode`) string. The
// UI renders provenance from THAT field — never a hard-coded badge — so the seed
// fallback (which carries the same labels) stays truthful about where a number lives.

// ===========================================================================
// READ wire shapes (GET /nurture/*) — match backend pydantic models exactly.
// ===========================================================================
export interface TierPanel {
  tier: string; // T1 | T2 | T3
  audience_size: number;
  reachability_pct: number;
  planning_size: number;
  segment_count: number;
}
export interface TierMix {
  clicked: number;
  opened: number;
  cold: number;
  total: number;
  reachable: number;
  reachability_pct: number;
}
export interface HeatmapCell {
  engagement_tier: string; // clicked | opened | cold
  attribute_value: string; // e.g. gt_160k | 65k_160k | TX
  total: number;
  converted: number;
  conversion_pct: number;
}
export interface PipelineStage {
  stage: string; // interest | apply | enroll | tuition | closed_lost
  count: number;
  pct: number;
  stuck: number;
}
export interface Handoff {
  weekly: number;
  monthly: number;
  cumulative: number;
  total_deals: number;
  conversion_pct: number;
}

// GET /nurture/overview — the 5a rollup (every figure computed, never faked).
export interface NurtureOverview {
  tiers: TierPanel[];
  engagement_tier_mix: TierMix;
  engagement_source: string; // crm_aggregate → "LIVE HUBSPOT"
  sequences_total: number;
  sequences_healthy: number;
  top_sequence: string | null;
  sla_compliance_pct: number;
  sms_reply_count_this_week: number;
  sms_replied_total: number;
  cold_segment_count: number;
  pipeline_stage_distribution: PipelineStage[];
  handoff_this_week: number;
  engagement_attribute_crosstab: HeatmapCell[]; // income × engagement
}

// GET /nurture/segments — the 5b view.
export interface NurtureSegment {
  segment_id: string;
  tier: string;
  sub_bucket: string;
  label: string;
  attribute_filters: Record<string, unknown>;
  size: number;
  reachability_pct: number;
  owner: string;
  notes: string;
}
export interface SegmentsResponse {
  tiers: TierPanel[];
  segments: NurtureSegment[];
  heatmap: Record<string, HeatmapCell[]>; // dimension → cells (income, region)
  source: string; // "supabase_mirror+source_of_truth" → app_form note
}

// POST /nurture/segments/build — owner-gated segment builder (identity stamped server-side).
export interface SegmentBuildRequest {
  tier: string; // T1 | T2 | T3
  sub_bucket?: string;
  label?: string;
  engagement_tiers?: string[] | null;
  attribute_filters?: Record<string, string[]>;
  notes?: string;
}

// GET /nurture/pipeline — the 5c view.
export interface PipelineResponse {
  stages: PipelineStage[];
  total: number;
  stuck_total: number;
  velocity_pct: number;
  handoff: Handoff;
  source: string; // crm_aggregate → "LIVE HUBSPOT"
}

// GET /nurture/sequences — the 5d view.
export interface SequenceStep {
  step: number;
  open_pct: number;
  click_pct: number;
  conversion_pct: number;
}
export interface NurtureSequence {
  sequence_id: string;
  name: string;
  seq_type: string; // welcome | nurture | re_engagement | event | waitlist
  audience_size: number;
  step_count: number;
  steps: SequenceStep[];
  health_flag: boolean; // true ⇒ UNHEALTHY (flagged)
  status: string;
}
export interface SequencesResponse {
  sequences: NurtureSequence[];
  source: string; // synthetic_mirror → "SYNTHETIC MIRROR"
}

// GET /nurture/sms — the 5e inbox (optional ?status= filter).
export interface SmsThread {
  thread_id: string;
  contact_label: string; // synthetic token (never PII)
  last_message: string;
  theme_tags: string[];
  tag_mode: string; // "keyword" | "llm" — surfaced honestly
  status: string; // unread | no_reply | objection | hot_family | ready
  replied: boolean;
  inbound_at: string | null;
}
export interface SmsResponse {
  threads: SmsThread[];
  source: string; // synthetic_mirror → "SYNTHETIC MIRROR"
}

// POST /nurture/sms/objection-brief — content-brief DRAFT stub (→ Module 3).
export interface ObjectionBriefRequest {
  theme: string;
  title?: string;
}
export interface ObjectionBriefResponse {
  entry_id: string;
  title: string;
  channel: string;
  status: string;
}

// POST /nurture/sms/{id}/flag-hot-family → DecisionResponse (subset we render).
export interface DecisionResponse {
  id: string;
  source: string;
  state: string;
  question: string;
  workstream: string;
  raised_by: string;
  priority: string;
}

// GET /nurture/sla — the 5f view.
export interface SlaLate {
  applicant_label: string;
  owner: string;
  hours_waiting: number;
  contacted: boolean;
}
export interface SlaOwner {
  owner: string;
  total: number;
  in_window: number;
  compliance_pct: number;
}
export interface SlaResponse {
  total: number;
  applicants_today: number;
  compliance_pct: number;
  pending: number;
  late: SlaLate[];
  per_owner: SlaOwner[];
  history_30d_count: number;
  window_hours: number;
  source: string; // supabase_mirror → app_form note
}

// ===========================================================================
// Source-provenance badges. The honesty contract: map the backend `source`
// (and `tag_mode`) string → a badge tone. No invented provenance.
// ===========================================================================
export type BadgeTone = 'live' | 'synthetic' | 'truth' | 'neutral';
export interface SourceBadgeInfo {
  label: string;
  tone: BadgeTone;
}
export function sourceBadge(source: string | null | undefined): SourceBadgeInfo {
  switch ((source ?? '').toLowerCase()) {
    case 'crm_aggregate':
      return { label: 'LIVE HUBSPOT · AGGREGATE', tone: 'live' };
    case 'synthetic_mirror':
      return { label: 'SYNTHETIC MIRROR', tone: 'synthetic' };
    case 'supabase_mirror':
      return { label: 'app_form · SOURCE OF TRUTH', tone: 'truth' };
    case 'supabase_mirror+source_of_truth':
      return { label: 'app_form · SOURCE OF TRUTH', tone: 'truth' };
    default:
      return { label: (source || '—').toUpperCase(), tone: 'neutral' };
  }
}
export function badgeStyle(tone: BadgeTone): { bg: string; color: string } {
  switch (tone) {
    case 'live':
      return { bg: 'var(--ok-soft)', color: 'var(--ok)' };
    case 'synthetic':
      return { bg: 'var(--warn-soft)', color: 'var(--warn)' };
    case 'truth':
      return { bg: 'var(--signal-soft)', color: 'var(--signal)' };
    default:
      return { bg: 'var(--accent-soft)', color: 'var(--ink-3)' };
  }
}
// SMS theme tagging mode → honest label (keyword rules v1 vs LLM auto-theme).
export function tagModeLabel(mode: string | null | undefined): string {
  if ((mode ?? '').toLowerCase() === 'llm') return 'LLM auto-theme';
  return 'keyword rules v1';
}

// ===========================================================================
// Display helpers — engagement-tier chips, SMS status/theme, seq type, labels.
// No invented color: tokens only (var(--…)).
// ===========================================================================
export const ENGAGEMENT_TIER_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  clicked: { label: 'Clicked', color: 'var(--ok)', bg: 'var(--ok-soft)' },
  opened: { label: 'Opened', color: 'var(--gold)', bg: 'var(--gold-soft)' },
  cold: { label: 'Cold', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};
export function engagementTierStyle(tier: string) {
  return ENGAGEMENT_TIER_STYLE[(tier ?? '').toLowerCase()] ?? { label: tier || '—', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
export const ENGAGEMENT_TIER_ORDER = ['clicked', 'opened', 'cold'] as const;

// SMS thread status → label + chip tokens.
const SMS_STATUS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  unread: { label: 'UNREAD', color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  no_reply: { label: 'NO REPLY', color: 'var(--warn)', bg: 'var(--warn-soft)' },
  objection: { label: 'OBJECTION', color: 'var(--signal)', bg: 'var(--signal-soft)' },
  hot_family: { label: 'HOT FAMILY', color: 'var(--on-brand)', bg: 'var(--gold)' },
  ready: { label: 'READY', color: 'var(--ok)', bg: 'var(--ok-soft)' },
};
export function smsStatusStyle(status: string | null | undefined) {
  return SMS_STATUS_STYLE[(status ?? '').toLowerCase()] ?? { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
export const SMS_STATUS_FILTERS = ['unread', 'no_reply', 'objection', 'hot_family', 'ready'] as const;

// Theme tag → chip tokens (matches the params theme_keyword_rules labels).
const THEME_TAG_STYLE: Record<string, { color: string; bg: string }> = {
  tuition: { color: 'var(--signal)', bg: 'var(--signal-soft)' },
  accreditation: { color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  scheduling: { color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  no_reply: { color: 'var(--warn)', bg: 'var(--warn-soft)' },
  ready: { color: 'var(--ok)', bg: 'var(--ok-soft)' },
};
export function themeTagStyle(tag: string) {
  return THEME_TAG_STYLE[(tag ?? '').toLowerCase()] ?? { color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
export function themeLabel(tag: string): string {
  return (tag || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Sequence type → label.
const SEQ_TYPE_LABEL: Record<string, string> = {
  welcome: 'Welcome',
  nurture: 'Nurture',
  re_engagement: 'Re-engagement',
  event: 'Event',
  waitlist: 'Waitlist',
};
export function seqTypeLabel(t: string): string {
  return SEQ_TYPE_LABEL[(t ?? '').toLowerCase()] ?? (t || '—').replace(/_/g, ' ');
}

// Pipeline stage → label.
const STAGE_LABEL: Record<string, string> = {
  interest: 'Interest',
  apply: 'Apply',
  enroll: 'Enroll',
  tuition: 'Tuition',
  closed_lost: 'Closed-lost',
};
export function stageLabel(s: string): string {
  return STAGE_LABEL[(s ?? '').toLowerCase()] ?? (s || '—').replace(/_/g, ' ');
}
export const HANDOFF_STAGES = ['enroll', 'tuition'];

// Heatmap attribute-value labels per dimension (aggregate bucket labels only).
const INCOME_LABEL: Record<string, string> = {
  gt_160k: '$160K+',
  '65k_160k': '$65–160K',
  lt_65k: '<$65K',
  unknown: 'Unknown',
};
export function attrValueLabel(dimension: string, value: string): string {
  if (dimension === 'income') return INCOME_LABEL[value] ?? value;
  return (value || '—').toUpperCase(); // region = state abbrev, persona, grade
}
export function dimensionLabel(dimension: string): string {
  if (dimension === 'income') return 'Income tier';
  if (dimension === 'region') return 'Region';
  return dimension.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Heatmap conversion-% → gold opacity ramp (relative to the matrix max). The
// hottest cell is the warmest blue/gold, cold/low cells fall back to accent-soft.
export function heatCellStyle(pct: number, maxPct: number): { bg: string; color: string; opacity: number } {
  if (pct <= 0 || maxPct <= 0) return { bg: 'var(--accent-soft)', color: 'var(--ink-3)', opacity: 1 };
  const ratio = Math.min(1, pct / maxPct);
  if (ratio < 0.3) return { bg: 'var(--accent-soft)', color: 'var(--ink-3)', opacity: 1 };
  const opacity = 0.42 + 0.58 * ratio;
  return { bg: 'var(--gold)', color: opacity > 0.62 ? 'var(--on-brand)' : 'var(--ink)', opacity };
}

// "YYYY-MM-DDTHH:MM:SS" → "Jun 14, 2:00 PM" (compact). Empty/invalid → "—".
export function fmtInbound(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

// ===========================================================================
// Seed fallbacks (rendered only when the backbone is unreachable → "○ SAMPLE").
// Numbers mirror the backend demo seed (migration 0040) so a static preview still
// reads true; the `source`/`tag_mode` labels are carried verbatim so provenance
// badges stay honest even offline.
// ===========================================================================
const SEED_TIERS: TierPanel[] = [
  { tier: 'T1', audience_size: 125, reachability_pct: 89, planning_size: 40, segment_count: 2 },
  { tier: 'T2', audience_size: 3100, reachability_pct: 60, planning_size: 3100, segment_count: 2 },
  { tier: 'T3', audience_size: 1124, reachability_pct: 17, planning_size: 1124, segment_count: 2 },
];

const SEED_PIPELINE_STAGES: PipelineStage[] = [
  { stage: 'interest', count: 2100, pct: 52, stuck: 140 },
  { stage: 'apply', count: 900, pct: 22, stuck: 64 },
  { stage: 'enroll', count: 260, pct: 6, stuck: 12 },
  { stage: 'tuition', count: 180, pct: 4, stuck: 5 },
  { stage: 'closed_lost', count: 600, pct: 15, stuck: 0 },
];

const SEED_HANDOFF: Handoff = { weekly: 26, monthly: 95, cumulative: 440, total_deals: 4040, conversion_pct: 11 };

// income × engagement crosstab (the 5a widget + 5b heatmap income dimension).
const SEED_INCOME_HEATMAP: HeatmapCell[] = [
  { engagement_tier: 'clicked', attribute_value: 'gt_160k', total: 320, converted: 134, conversion_pct: 42 },
  { engagement_tier: 'clicked', attribute_value: '65k_160k', total: 880, converted: 326, conversion_pct: 37 },
  { engagement_tier: 'clicked', attribute_value: 'lt_65k', total: 410, converted: 119, conversion_pct: 29 },
  { engagement_tier: 'opened', attribute_value: 'gt_160k', total: 290, converted: 64, conversion_pct: 22 },
  { engagement_tier: 'opened', attribute_value: '65k_160k', total: 760, converted: 137, conversion_pct: 18 },
  { engagement_tier: 'opened', attribute_value: 'lt_65k', total: 380, converted: 53, conversion_pct: 14 },
  { engagement_tier: 'cold', attribute_value: 'gt_160k', total: 240, converted: 22, conversion_pct: 9 },
  { engagement_tier: 'cold', attribute_value: '65k_160k', total: 700, converted: 49, conversion_pct: 7 },
  { engagement_tier: 'cold', attribute_value: 'lt_65k', total: 350, converted: 18, conversion_pct: 5 },
];
const SEED_REGION_HEATMAP: HeatmapCell[] = [
  { engagement_tier: 'clicked', attribute_value: 'TX', total: 1100, converted: 451, conversion_pct: 41 },
  { engagement_tier: 'clicked', attribute_value: 'CA', total: 320, converted: 109, conversion_pct: 34 },
  { engagement_tier: 'clicked', attribute_value: 'FL', total: 190, converted: 59, conversion_pct: 31 },
  { engagement_tier: 'opened', attribute_value: 'TX', total: 980, converted: 206, conversion_pct: 21 },
  { engagement_tier: 'opened', attribute_value: 'CA', total: 290, converted: 52, conversion_pct: 18 },
  { engagement_tier: 'opened', attribute_value: 'FL', total: 160, converted: 26, conversion_pct: 16 },
  { engagement_tier: 'cold', attribute_value: 'TX', total: 900, converted: 72, conversion_pct: 8 },
  { engagement_tier: 'cold', attribute_value: 'CA', total: 260, converted: 16, conversion_pct: 6 },
  { engagement_tier: 'cold', attribute_value: 'FL', total: 130, converted: 5, conversion_pct: 4 },
];

export const SEED_OVERVIEW: NurtureOverview = {
  tiers: SEED_TIERS,
  engagement_tier_mix: { clicked: 1734, opened: 1733, cold: 1733, total: 5200, reachable: 3467, reachability_pct: 67 },
  engagement_source: 'crm_aggregate',
  sequences_total: 5,
  sequences_healthy: 4,
  top_sequence: 'Event — shadow-day invite',
  sla_compliance_pct: 78,
  sms_reply_count_this_week: 4,
  sms_replied_total: 4,
  cold_segment_count: 2,
  pipeline_stage_distribution: SEED_PIPELINE_STAGES,
  handoff_this_week: 26,
  engagement_attribute_crosstab: SEED_INCOME_HEATMAP,
};

export const SEED_SEGMENTS: SegmentsResponse = {
  tiers: SEED_TIERS,
  segments: [
    { segment_id: 'seg-0', tier: 'T1', sub_bucket: 'ready_high_income', label: 'T1 · Ready, >$160K', attribute_filters: { engagement_tier: 'clicked', income: 'gt_160k' }, size: 40, reachability_pct: 92, owner: 'nurture', notes: 'Hot, high-intent, fully reachable — call first.' },
    { segment_id: 'seg-1', tier: 'T1', sub_bucket: 'ready_voucher', label: 'T1 · Ready, voucher-track', attribute_filters: { engagement_tier: 'clicked', income: '65k_160k' }, size: 85, reachability_pct: 88, owner: 'nurture', notes: 'High intent, TEFA-eligible — fast-track funding.' },
    { segment_id: 'seg-2', tier: 'T2', sub_bucket: 'warm_mid_funnel', label: 'T2 · Warm mid-funnel', attribute_filters: { engagement_tier: 'opened', income: '65k_160k' }, size: 1600, reachability_pct: 61, owner: 'nurture', notes: 'Opened but not clicked — needs a nudge sequence.' },
    { segment_id: 'seg-3', tier: 'T2', sub_bucket: 'warm_southwest', label: 'T2 · Warm, Southwest region', attribute_filters: { engagement_tier: 'opened', region: 'TX' }, size: 1500, reachability_pct: 58, owner: 'nurture', notes: 'Regional warm pool — pair with field events.' },
    { segment_id: 'seg-4', tier: 'T3', sub_bucket: 'cold_longhorizon', label: 'T3 · Cold, long-horizon', attribute_filters: { engagement_tier: 'cold', grade: 'incoming_k' }, size: 700, reachability_pct: 18, owner: 'nurture', notes: 'Future-grade families — long drip, not lost.' },
    { segment_id: 'seg-5', tier: 'T3', sub_bucket: 'cold_reengage', label: 'T3 · Cold, re-engage', attribute_filters: { engagement_tier: 'cold' }, size: 424, reachability_pct: 15, owner: 'nurture', notes: 'Gone quiet — re-engagement sequence candidates.' },
  ],
  heatmap: { income: SEED_INCOME_HEATMAP, region: SEED_REGION_HEATMAP },
  source: 'supabase_mirror+source_of_truth',
};

export const SEED_PIPELINE: PipelineResponse = {
  stages: SEED_PIPELINE_STAGES,
  total: 4040,
  stuck_total: 221,
  velocity_pct: 11,
  handoff: SEED_HANDOFF,
  source: 'crm_aggregate',
};

export const SEED_SEQUENCES: SequencesResponse = {
  sequences: [
    { sequence_id: 'seq-0', name: 'Welcome — new applicant', seq_type: 'welcome', audience_size: 420, step_count: 3, steps: [{ step: 1, open_pct: 68, click_pct: 22, conversion_pct: 9 }, { step: 2, open_pct: 54, click_pct: 14, conversion_pct: 6 }, { step: 3, open_pct: 41, click_pct: 9, conversion_pct: 4 }], health_flag: false, status: 'active' },
    { sequence_id: 'seq-1', name: 'Nurture — mid-funnel drip', seq_type: 'nurture', audience_size: 1600, step_count: 4, steps: [{ step: 1, open_pct: 47, click_pct: 11, conversion_pct: 3 }, { step: 2, open_pct: 39, click_pct: 8, conversion_pct: 2 }, { step: 3, open_pct: 33, click_pct: 6, conversion_pct: 2 }, { step: 4, open_pct: 28, click_pct: 5, conversion_pct: 1 }], health_flag: false, status: 'active' },
    { sequence_id: 'seq-2', name: 'Re-engagement — gone cold', seq_type: 're_engagement', audience_size: 1100, step_count: 2, steps: [{ step: 1, open_pct: 24, click_pct: 4, conversion_pct: 1 }, { step: 2, open_pct: 18, click_pct: 3, conversion_pct: 1 }], health_flag: true, status: 'active' },
    { sequence_id: 'seq-3', name: 'Event — shadow-day invite', seq_type: 'event', audience_size: 300, step_count: 2, steps: [{ step: 1, open_pct: 72, click_pct: 31, conversion_pct: 14 }, { step: 2, open_pct: 58, click_pct: 19, conversion_pct: 8 }], health_flag: false, status: 'active' },
    { sequence_id: 'seq-4', name: 'Waitlist — hold warm', seq_type: 'waitlist', audience_size: 140, step_count: 3, steps: [{ step: 1, open_pct: 61, click_pct: 17, conversion_pct: 5 }, { step: 2, open_pct: 49, click_pct: 12, conversion_pct: 4 }, { step: 3, open_pct: 38, click_pct: 9, conversion_pct: 3 }], health_flag: false, status: 'active' },
  ],
  source: 'synthetic_mirror',
};

export const SEED_SMS: SmsResponse = {
  threads: [
    { thread_id: 'sms-0', contact_label: 'Family #A12', last_message: 'How much is tuition for two kids?', theme_tags: ['tuition'], tag_mode: 'keyword', status: 'objection', replied: false, inbound_at: '2026-06-15T09:00:00Z' },
    { thread_id: 'sms-1', contact_label: 'Family #B07', last_message: 'Is this a real accredited school?', theme_tags: ['accreditation'], tag_mode: 'keyword', status: 'objection', replied: false, inbound_at: '2026-06-15T06:00:00Z' },
    { thread_id: 'sms-2', contact_label: 'Family #C31', last_message: 'Can we reschedule the tour?', theme_tags: ['scheduling'], tag_mode: 'keyword', status: 'unread', replied: false, inbound_at: '2026-06-15T10:00:00Z' },
    { thread_id: 'sms-3', contact_label: 'Family #D44', last_message: "We're ready to enroll!", theme_tags: ['ready'], tag_mode: 'keyword', status: 'hot_family', replied: true, inbound_at: '2026-06-15T11:00:00Z' },
    { thread_id: 'sms-4', contact_label: 'Family #E18', last_message: "What's the price after the scholarship?", theme_tags: ['tuition'], tag_mode: 'keyword', status: 'hot_family', replied: false, inbound_at: '2026-06-15T08:00:00Z' },
    { thread_id: 'sms-5', contact_label: 'Family #F90', last_message: 'stop texting me please', theme_tags: ['no_reply'], tag_mode: 'keyword', status: 'no_reply', replied: false, inbound_at: '2026-06-15T03:00:00Z' },
    { thread_id: 'sms-6', contact_label: 'Family #G22', last_message: 'When does fall start?', theme_tags: ['scheduling'], tag_mode: 'keyword', status: 'unread', replied: false, inbound_at: '2026-06-15T07:00:00Z' },
    { thread_id: 'sms-7', contact_label: 'Family #H55', last_message: 'Sign us up, where do we deposit?', theme_tags: ['ready'], tag_mode: 'keyword', status: 'ready', replied: true, inbound_at: '2026-06-15T11:00:00Z' },
    { thread_id: 'sms-8', contact_label: 'Family #J03', last_message: 'Too expensive for us right now', theme_tags: ['tuition'], tag_mode: 'keyword', status: 'objection', replied: false, inbound_at: '2026-06-15T05:00:00Z' },
    { thread_id: 'sms-9', contact_label: 'Family #K61', last_message: 'Do you give real diplomas?', theme_tags: ['accreditation'], tag_mode: 'keyword', status: 'unread', replied: false, inbound_at: '2026-06-15T04:00:00Z' },
    { thread_id: 'sms-10', contact_label: 'Family #L29', last_message: 'busy, talk later', theme_tags: ['no_reply'], tag_mode: 'keyword', status: 'no_reply', replied: false, inbound_at: '2026-06-15T00:00:00Z' },
    { thread_id: 'sms-11', contact_label: 'Family #M74', last_message: 'Yes we want to start in August', theme_tags: ['ready', 'scheduling'], tag_mode: 'keyword', status: 'hot_family', replied: true, inbound_at: '2026-06-15T10:00:00Z' },
    { thread_id: 'sms-12', contact_label: 'Family #N38', last_message: 'What time is the info session?', theme_tags: ['scheduling'], tag_mode: 'keyword', status: 'unread', replied: false, inbound_at: '2026-06-15T09:00:00Z' },
    { thread_id: 'sms-13', contact_label: 'Family #P81', last_message: 'Thanks, just looking for now', theme_tags: [], tag_mode: 'keyword', status: 'unread', replied: false, inbound_at: '2026-06-15T02:00:00Z' },
  ],
  source: 'synthetic_mirror',
};

export const SEED_SLA: SlaResponse = {
  total: 30,
  applicants_today: 6,
  compliance_pct: 78,
  pending: 9,
  late: [
    { applicant_label: 'Applicant #04', owner: 'rep_morgan', hours_waiting: 31, contacted: false },
    { applicant_label: 'Applicant #07', owner: 'rep_lee', hours_waiting: 36, contacted: false },
    { applicant_label: 'Applicant #10', owner: 'rep_sasha', hours_waiting: 28, contacted: false },
    { applicant_label: 'Applicant #13', owner: 'rep_morgan', hours_waiting: 42, contacted: true },
    { applicant_label: 'Applicant #16', owner: 'rep_lee', hours_waiting: 30, contacted: true },
    { applicant_label: 'Applicant #19', owner: 'rep_sasha', hours_waiting: 33, contacted: false },
    { applicant_label: 'Applicant #22', owner: 'rep_morgan', hours_waiting: 26, contacted: false },
    { applicant_label: 'Applicant #25', owner: 'rep_lee', hours_waiting: 48, contacted: true },
    { applicant_label: 'Applicant #28', owner: 'rep_sasha', hours_waiting: 27, contacted: false },
  ],
  per_owner: [
    { owner: 'rep_morgan', total: 10, in_window: 8, compliance_pct: 80 },
    { owner: 'rep_sasha', total: 10, in_window: 8, compliance_pct: 80 },
    { owner: 'rep_lee', total: 10, in_window: 7, compliance_pct: 70 },
  ],
  history_30d_count: 30,
  window_hours: 24,
  source: 'supabase_mirror',
};

// The engagement-tier combos + attribute options the segment builder offers (the
// builder POSTs engagement_tiers + attribute_filters; the backend computes the size).
export const BUILDER_TIERS = ['T1', 'T2', 'T3'] as const;
export const BUILDER_INCOME_OPTIONS = [
  { value: 'gt_160k', label: '$160K+' },
  { value: '65k_160k', label: '$65–160K' },
  { value: 'lt_65k', label: '<$65K' },
] as const;

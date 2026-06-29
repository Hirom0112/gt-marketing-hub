// Admissions & Voice of Customer (Module 9) view-model: typed wire shapes for the live
// FastAPI endpoints (app/api/admissions.py), the owner-gated write bodies (objection →
// content brief + file feedback), the leadership PATCH (action/close), a seed fallback per
// resource (so a screen never blanks when the backbone is down → honest "○ SAMPLE"), and
// the display helpers (theme/source/trend/category chips, sentiment ratio, source badges).
// Mirrors lib/nurture-api.ts conventions.
//
// HONESTY: provenance is rendered from backend fields, never a hard-coded badge:
//   • objection `source` (bdr_call / form / event / sms) → manual vs HubSpot-Conv mirror.
//   • voice `sentiment_source_mode` ("placeholder") → "PLACEHOLDER · AGGREGATE" (not live).
// The seed fallbacks carry the same labels so provenance stays truthful even offline.

// ===========================================================================
// READ wire shapes (GET /admissions/*) — match backend pydantic models exactly.
// ===========================================================================
export type ObjectionTrend = 'up' | 'stable' | 'down';

export interface Objection {
  objection_id: string;
  theme: string;
  week_count: number;
  cumulative_count: number;
  trend: string; // up | stable | down
  source: string; // bdr_call | form | event | sms
  example_quote: string;
  persona: string;
  urgency: string; // high | normal | low
}

export interface VoiceQuote {
  quote_id: string;
  quote: string;
  sentiment: string; // positive | neutral | negative
  theme: string;
  source: string;
  is_quote_of_week: boolean;
}

export interface FeedbackItem {
  item_id: string;
  summary: string;
  category: string; // messaging_gap | persona_mismatch | objection_pattern | positive_signal | urgent
  status: string; // open | actioned | closed
  actionable: boolean;
  owner: string;
  decision_id: string | null;
  created_at: string;
  actioned_at: string | null;
}

export interface AdmissionStat {
  week_of: string; // "YYYY-MM-DD"
  applicants: number;
  shadow_days: number;
  offers: number;
  deposits: number;
}

export interface ContentBridge {
  bridge_id: string;
  objection_theme: string;
  brief_entry_id: string | null;
  produced: boolean;
  surfaced_at: string;
  published_at: string | null;
  freq_before: number;
  freq_after: number | null;
  frequency_decreased: boolean;
}

// core.bridge_hit_rate → { produced, total, hit_rate_pct, avg_resolution_days }
export interface HitRate {
  produced: number;
  total: number;
  hit_rate_pct: number;
  avg_resolution_days: number;
}

// core.sentiment_ratio → counts + per-bucket pct + total
export interface SentimentRatio {
  positive: number;
  neutral: number;
  negative: number;
  total: number;
  positive_pct: number;
  neutral_pct: number;
  negative_pct: number;
}

// core.feedback_closure_rate → { actioned, within_sla, total, open_count, closure_rate_pct }
export interface ClosureRate {
  actioned: number;
  within_sla: number;
  total: number;
  open_count: number;
  closure_rate_pct: number;
}

// GET /admissions/overview
export interface OverviewResponse {
  weekly_stats: AdmissionStat[];
  top_objections: Objection[];
  objection_trend: Record<string, string>; // theme → up|stable|down
  feedback_open_count: number;
  notable_quotes: VoiceQuote[];
  objection_to_resolution_days: number;
  bridge_hit_rate: HitRate;
}

// GET /admissions/voice
export interface VoiceResponse {
  quotes: VoiceQuote[];
  quote_of_week: VoiceQuote | null;
  quote_sentiment: SentimentRatio;
  feed_sentiment: SentimentRatio;
  sentiment_source_mode: string; // "placeholder" — surfaced honestly (never live_feed in v1)
}

// GET /admissions/feedback
export interface FeedbackResponse {
  items: FeedbackItem[];
  closure_rate: ClosureRate;
}

// GET /admissions/bridge
export interface BridgeResponse {
  bridges: ContentBridge[];
  hit_rate: HitRate;
}

// ===========================================================================
// WRITE wire shapes.
// ===========================================================================
// POST /admissions/objections/{id}/brief — owner-gated (admissions). Identity stamped server-side.
export interface BriefRequest {
  title?: string;
}
export interface BriefResponse {
  entry_id: string;
  title: string;
  channel: string;
  status: string;
  bridge_id: string;
  theme: string;
}

// POST /admissions/feedback — owner-gated. No owner/raised_by in body (server-stamped).
export interface FeedbackCreateRequest {
  summary: string;
  category: string;
  actionable: boolean;
  recommendation: string;
}

// PATCH /admissions/feedback/{id} — leader/admin only.
export interface FeedbackPatchRequest {
  action: 'action' | 'close';
}

// ===========================================================================
// Source-provenance badges. Honesty contract: map a backend field → badge tone.
// No invented provenance.
// ===========================================================================
export type BadgeTone = 'live' | 'synthetic' | 'truth' | 'neutral';
export interface SourceBadgeInfo {
  label: string;
  tone: BadgeTone;
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

// objection `source` → honest provenance badge. SMS is the HubSpot Conversations
// synthetic mirror; BDR/event are manual notes; form is a captured field.
export function objectionSourceBadge(source: string | null | undefined): SourceBadgeInfo {
  switch ((source ?? '').toLowerCase()) {
    case 'sms':
      return { label: 'HubSpot Conv. · SYNTHETIC MIRROR', tone: 'synthetic' };
    case 'bdr_call':
      return { label: 'BDR CALL · MANUAL', tone: 'neutral' };
    case 'event':
      return { label: 'EVENT NOTE · MANUAL', tone: 'neutral' };
    case 'form':
      return { label: 'FORM CAPTURE', tone: 'truth' };
    default:
      return { label: (source || '—').toUpperCase(), tone: 'neutral' };
  }
}

// sentiment `source_mode` → honest badge. v1 is "placeholder" (aggregate, never live).
export function sentimentSourceBadge(mode: string | null | undefined): SourceBadgeInfo {
  switch ((mode ?? '').toLowerCase()) {
    case 'placeholder':
      return { label: 'PLACEHOLDER · AGGREGATE', tone: 'synthetic' };
    case 'live_feed':
      return { label: 'LIVE FEED · AGGREGATE', tone: 'live' };
    default:
      return { label: (mode || '—').toUpperCase(), tone: 'neutral' };
  }
}

// ===========================================================================
// Display helpers — trend arrows, source/theme/category/urgency chips, labels.
// No invented color: tokens only (var(--…)).
// ===========================================================================
// Trend arrow + color encode direction: ↑ rising = --signal (needs attention),
// → stable = --ink-3, ↓ falling = --ok (a falling objection is a good thing).
export const TREND_META: Record<string, { glyph: string; color: string; label: string }> = {
  up: { glyph: '↑', color: 'var(--signal)', label: 'rising' },
  stable: { glyph: '→', color: 'var(--ink-3)', label: 'stable' },
  down: { glyph: '↓', color: 'var(--ok)', label: 'falling' },
};
export function trendMeta(trend: string | null | undefined) {
  return TREND_META[(trend ?? '').toLowerCase()] ?? TREND_META.stable;
}

// short source chip label for the objection-log table.
const SOURCE_LABEL: Record<string, string> = {
  bdr_call: 'BDR call',
  form: 'form',
  event: 'event',
  sms: 'SMS',
};
export function sourceLabel(source: string): string {
  return SOURCE_LABEL[(source ?? '').toLowerCase()] ?? (source || '—').replace(/_/g, ' ');
}

// human label for a snake_case token (themes, categories).
export function humanLabel(token: string): string {
  return (token || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// urgency → chip tokens.
export function urgencyStyle(urgency: string): { label: string; color: string; bg: string } {
  switch ((urgency ?? '').toLowerCase()) {
    case 'high':
      return { label: 'HIGH', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'normal':
      return { label: 'MED', color: 'var(--gold)', bg: 'var(--gold-soft)' };
    default:
      return { label: 'LOW', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}

// sentiment tone → dot/legend color.
export function toneColor(sentiment: string): string {
  switch ((sentiment ?? '').toLowerCase()) {
    case 'positive':
      return 'var(--ok)';
    case 'negative':
      return 'var(--signal)';
    default:
      return 'var(--ink-3)';
  }
}

// feedback category → chip tokens.
export const CATEGORY_STYLE: Record<string, { color: string; bg: string }> = {
  messaging_gap: { color: 'var(--signal)', bg: 'var(--signal-soft)' },
  persona_mismatch: { color: 'var(--warn)', bg: 'var(--warn-soft)' },
  objection_pattern: { color: 'var(--gold)', bg: 'var(--gold-soft)' },
  positive_signal: { color: 'var(--ok)', bg: 'var(--ok-soft)' },
  urgent: { color: 'var(--broken)', bg: 'var(--warn-soft)' },
};
export function categoryStyle(category: string): { color: string; bg: string } {
  return CATEGORY_STYLE[(category ?? '').toLowerCase()] ?? { color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}

// feedback status → chip tokens.
export function feedbackStatusStyle(status: string): { label: string; color: string; bg: string } {
  switch ((status ?? '').toLowerCase()) {
    case 'actioned':
      return { label: 'ACTIONED', color: 'var(--gold)', bg: 'var(--gold-soft)' };
    case 'closed':
      return { label: 'CLOSED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    default:
      return { label: 'OPEN', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}

// The closed list of feedback categories the file-feedback form offers (mirrors
// params.admissions.feedback_categories — the backend rejects anything else with a 422).
export const FEEDBACK_CATEGORIES = [
  'messaging_gap',
  'persona_mismatch',
  'objection_pattern',
  'positive_signal',
  'urgent',
] as const;

// "YYYY-MM-DD"[Thh:mm:ss] → "May 18" / "Jun 15" (compact). Empty/invalid → "—".
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// days between two ISO timestamps, rounded to 1 decimal (— when either is missing).
export function daysBetween(a: string | null | undefined, b: string | null | undefined): string {
  if (!a || !b) return '—';
  const da = new Date(a).getTime();
  const db = new Date(b).getTime();
  if (Number.isNaN(da) || Number.isNaN(db)) return '—';
  return `${Math.round((Math.abs(db - da) / 86400000) * 10) / 10}d`;
}

// ===========================================================================
// Seed fallbacks (rendered only when the backbone is unreachable → "○ SAMPLE").
// Numbers mirror the backend demo seed (migration 0042 / admissions_store) so a
// static preview still reads true; source/source_mode labels are carried verbatim
// so provenance badges stay honest even offline.
// ===========================================================================
const SEED_OBJECTIONS: Objection[] = [
  { objection_id: 'obj-0', theme: 'cost', week_count: 14, cumulative_count: 58, trend: 'up', source: 'bdr_call', example_quote: '$10k a year before the ESA even clears — we cannot float that.', persona: 'ESA-planned, out-of-pocket-anxious', urgency: 'high' },
  { objection_id: 'obj-1', theme: 'accreditation', week_count: 11, cumulative_count: 44, trend: 'up', source: 'form', example_quote: 'Is this an accredited school, or does my kid end up with no real diploma?', persona: 'first-time, diploma-skeptic', urgency: 'high' },
  { objection_id: 'obj-2', theme: 'gifted_enough', week_count: 8, cumulative_count: 31, trend: 'stable', source: 'sms', example_quote: 'He is bright but not a prodigy — is this only for the genius kids?', persona: 'bright-but-not-prodigy parent', urgency: 'normal' },
  { objection_id: 'obj-3', theme: 'scheduling', week_count: 6, cumulative_count: 22, trend: 'down', source: 'event', example_quote: 'When does the day actually start? I work and cannot do a 7am drop.', persona: 'working-parent, logistics-first', urgency: 'normal' },
  { objection_id: 'obj-4', theme: 'curriculum', week_count: 4, cumulative_count: 18, trend: 'stable', source: 'bdr_call', example_quote: 'What do they actually learn if an app teaches the academics?', persona: 'rigor-curious parent', urgency: 'normal' },
  { objection_id: 'obj-5', theme: 'social', week_count: 3, cumulative_count: 14, trend: 'down', source: 'sms', example_quote: 'I worry she will be isolated staring at a screen all day.', persona: 'socialization-worried parent', urgency: 'low' },
  { objection_id: 'obj-6', theme: 'tech_requirements', week_count: 1, cumulative_count: 5, trend: 'stable', source: 'form', example_quote: 'Do we have to buy the iPad, or is the device provided?', persona: 'logistics-first parent', urgency: 'low' },
];

const SEED_VOICE: VoiceQuote[] = [
  { quote_id: 'voc-0', quote: 'The 2-hour academic core gave my daughter her afternoons back.', sentiment: 'positive', theme: 'scheduling', source: 'enrolled_family', is_quote_of_week: false },
  { quote_id: 'voc-1', quote: 'Loved the tour but nobody followed up for nine days. I had already half-moved on.', sentiment: 'negative', theme: 'scheduling', source: 'tour_attendee', is_quote_of_week: false },
  { quote_id: 'voc-2', quote: 'Still not clear how the guides differ from teachers. Explain that and I am sold.', sentiment: 'neutral', theme: 'curriculum', source: 'form_inquiry', is_quote_of_week: false },
  { quote_id: 'voc-3', quote: 'My son went from hating school to asking to do extra. That alone is worth it.', sentiment: 'positive', theme: 'curriculum', source: 'enrolled_family', is_quote_of_week: false },
  { quote_id: 'voc-4', quote: 'The ESA paperwork felt heavier than enrolling itself. A checklist would have saved me.', sentiment: 'negative', theme: 'cost', source: 'esa_planned', is_quote_of_week: false },
  { quote_id: 'voc-5', quote: 'I came in a skeptic about an app teaching my kid. I left realizing the app is the floor and the guides build everything on top of it.', sentiment: 'positive', theme: 'curriculum', source: 'shadow_day_visitor', is_quote_of_week: true },
  { quote_id: 'voc-6', quote: 'What is the real difference between mastery and grade-level? I keep hearing both.', sentiment: 'neutral', theme: 'curriculum', source: 'form_inquiry', is_quote_of_week: false },
  { quote_id: 'voc-7', quote: 'Afternoons-back framing lands hard with working parents. Use it more.', sentiment: 'positive', theme: 'scheduling', source: 'enrolled_family', is_quote_of_week: false },
];

const SEED_QUOTE_OF_WEEK = SEED_VOICE[5];

const SEED_FEEDBACK: FeedbackItem[] = [
  { item_id: 'fb-0', summary: 'Families do not connect 2-hour learning to academic rigor — reads as less school.', category: 'messaging_gap', status: 'actioned', actionable: true, owner: 'admissions', decision_id: 'dec-0', created_at: '2026-06-05T12:00:00Z', actioned_at: '2026-06-09T12:00:00Z' },
  { item_id: 'fb-1', summary: 'Gifted-enough recurs from mid-tier learners — hero copy over-indexes on prodigies.', category: 'persona_mismatch', status: 'open', actionable: false, owner: 'admissions', decision_id: null, created_at: '2026-06-11T12:00:00Z', actioned_at: null },
  { item_id: 'fb-2', summary: 'Accreditation questions up since a competitor diploma campaign.', category: 'objection_pattern', status: 'actioned', actionable: true, owner: 'admissions', decision_id: 'dec-2', created_at: '2026-06-03T12:00:00Z', actioned_at: '2026-06-13T12:00:00Z' },
  { item_id: 'fb-3', summary: 'Afternoons-back framing lands hard with working parents — under-used in ads.', category: 'positive_signal', status: 'closed', actionable: false, owner: 'admissions', decision_id: null, created_at: '2026-06-07T12:00:00Z', actioned_at: '2026-06-10T12:00:00Z' },
  { item_id: 'fb-4', summary: 'High-intent families stalled on ESA paperwork confusion — churn risk this week.', category: 'urgent', status: 'open', actionable: true, owner: 'admissions', decision_id: 'dec-4', created_at: '2026-06-13T12:00:00Z', actioned_at: null },
  { item_id: 'fb-5', summary: 'Tour-to-followup gap reported again — leads cool before the first call.', category: 'messaging_gap', status: 'actioned', actionable: true, owner: 'admissions', decision_id: 'dec-5', created_at: '2026-06-10T12:00:00Z', actioned_at: '2026-06-12T12:00:00Z' },
];

const SEED_STATS: AdmissionStat[] = [
  { week_of: '2026-05-18', applicants: 31, shadow_days: 12, offers: 9, deposits: 5 },
  { week_of: '2026-05-25', applicants: 38, shadow_days: 15, offers: 12, deposits: 7 },
  { week_of: '2026-06-01', applicants: 44, shadow_days: 18, offers: 15, deposits: 9 },
  { week_of: '2026-06-08', applicants: 49, shadow_days: 20, offers: 17, deposits: 11 },
  { week_of: '2026-06-15', applicants: 47, shadow_days: 22, offers: 19, deposits: 13 },
];

const SEED_BRIDGES: ContentBridge[] = [
  { bridge_id: 'br-0', objection_theme: 'cost', brief_entry_id: 'ce-0', produced: true, surfaced_at: '2026-06-01T12:00:00Z', published_at: '2026-06-05T12:00:00Z', freq_before: 18, freq_after: 14, frequency_decreased: true },
  { bridge_id: 'br-1', objection_theme: 'accreditation', brief_entry_id: 'ce-1', produced: true, surfaced_at: '2026-06-03T12:00:00Z', published_at: '2026-06-09T12:00:00Z', freq_before: 14, freq_after: 11, frequency_decreased: true },
  { bridge_id: 'br-2', objection_theme: 'gifted_enough', brief_entry_id: 'ce-2', produced: false, surfaced_at: '2026-06-12T12:00:00Z', published_at: null, freq_before: 8, freq_after: null, frequency_decreased: false },
  { bridge_id: 'br-3', objection_theme: 'scheduling', brief_entry_id: 'ce-3', produced: false, surfaced_at: '2026-06-13T12:00:00Z', published_at: null, freq_before: 9, freq_after: null, frequency_decreased: false },
];

const SEED_HIT_RATE: HitRate = { produced: 2, total: 4, hit_rate_pct: 50, avg_resolution_days: 5.0 };
const SEED_QUOTE_SENTIMENT: SentimentRatio = { positive: 4, neutral: 2, negative: 2, total: 8, positive_pct: 50, neutral_pct: 25, negative_pct: 25 };
// feed_sentiment is the §7.5 placeholder adapter's aggregate over the recent window —
// representative numbers, carried with source_mode "placeholder" (never implied live).
const SEED_FEED_SENTIMENT: SentimentRatio = { positive: 96, neutral: 64, negative: 52, total: 212, positive_pct: 45, neutral_pct: 30, negative_pct: 25 };

export const SEED_OVERVIEW: OverviewResponse = {
  weekly_stats: SEED_STATS,
  top_objections: SEED_OBJECTIONS.slice(0, 3),
  objection_trend: {
    cost: 'up',
    accreditation: 'up',
    gifted_enough: 'stable',
    scheduling: 'down',
    curriculum: 'stable',
    social: 'down',
    tech_requirements: 'stable',
  },
  feedback_open_count: 2,
  notable_quotes: [SEED_VOICE[5], SEED_VOICE[0], SEED_VOICE[1]],
  objection_to_resolution_days: 5.0,
  bridge_hit_rate: SEED_HIT_RATE,
};

export const SEED_OBJECTIONS_RESP: Objection[] = SEED_OBJECTIONS;

export const SEED_VOICE_RESP: VoiceResponse = {
  quotes: SEED_VOICE,
  quote_of_week: SEED_QUOTE_OF_WEEK,
  quote_sentiment: SEED_QUOTE_SENTIMENT,
  feed_sentiment: SEED_FEED_SENTIMENT,
  sentiment_source_mode: 'placeholder',
};

export const SEED_FEEDBACK_RESP: FeedbackResponse = {
  items: SEED_FEEDBACK,
  closure_rate: { actioned: 4, within_sla: 3, total: 6, open_count: 2, closure_rate_pct: 75 },
};

export const SEED_BRIDGE_RESP: BridgeResponse = {
  bridges: SEED_BRIDGES,
  hit_rate: SEED_HIT_RATE,
};

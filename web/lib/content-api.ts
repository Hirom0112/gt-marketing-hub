// Content & Thought Leadership (Module 3) view-model: typed wire shapes for the
// live FastAPI endpoints (app/api/content.py), the owner-gated calendar write
// bodies, the advisory brand-voice request, a per-resource seed fallback (so a
// screen never blanks when the backbone is down), and the small display helpers
// (channel colors, source-kind provenance labels, date formatting) the module
// renders with. Mirrors lib/grassroots-api.ts / scorecard-view.ts.
//
// NOTE: the production-pipeline kanban (GET/POST /content/kanban) is a SEPARATE
// live seam wired directly inside ContentModule.tsx — it is intentionally not
// modelled here and must be left untouched.

// ---- READ wire shapes ------------------------------------------------------
// GET /content/overview
export interface ChannelStandin {
  channel: string; // lowercase: x | substack | instagram | facebook | podcast | email | youtube
  reach: number;
  source_kind: string; // manual | stood_in | live
}
export interface ContentOverview {
  productions_in_flight: number;
  on_track: number;
  on_track_pct: number;
  this_week_publish_count: number;
  top_piece_title: string;
  top_piece_conversions: number;
  x_conversion_rate_pct: number;
  channel_standins: ChannelStandin[];
  library_count: number;
  testimonial_stub_count: number;
}

// GET /content/calendar
export interface CalendarEntry {
  entry_id: string;
  title: string;
  channel: string; // lowercase
  scheduled_date: string; // YYYY-MM-DD
  status: string; // planned | scheduled | published
  piece_ref: string | null;
  owner: string;
}
export interface ContentCalendar {
  entries: CalendarEntry[];
  conflict_dates: string[]; // YYYY-MM-DD with >= conflict_threshold pieces
  conflict_threshold: number;
}

// GET /content/testimonial-stubs (Grassroots cross-link DRAFT stubs)
export interface TestimonialStub {
  asset_id: string;
  title: string;
  body: string;
  tags: string[];
  source_ref: string;
  created_at: string;
}

// GET /content/performance
export interface PerfChannel {
  channel: string; // lowercase
  reach: number;
  clicks: number;
  conversions: number;
  conversion_rate_pct: number;
  source_kind: string; // manual | stood_in | live
  is_top: boolean;
  is_bottom: boolean;
}
export interface PerfPiece {
  piece_title: string;
  channel: string;
  reach: number;
  clicks: number;
  conversions: number;
  conversion_rate_pct: number;
  utm_attributed: boolean;
}
export interface ContentPerformance {
  channels: PerfChannel[];
  top_pieces: PerfPiece[];
  bottom_pieces: PerfPiece[];
  content_to_conversion: PerfPiece[];
  unattributable_count: number;
}

// GET /content/library — kept + validated assets (params: q, tag[] repeatable)
export interface LibraryProvenance {
  generated_by: string;
  created_at: string;
  model_ref: string | null;
  prompt_id: string | null;
  recipe_ref: string | null;
  brand_memory_refs: string[];
  created_by_user: string | null;
}
export interface LibraryAsset {
  id: string;
  title: string;
  asset_type: string; // copy | blog_post | ...
  channel: string; // x | instagram | landing_page | tiktok | ...
  format: string;
  body: string;
  asset_uri: string | null;
  source_ref: string;
  tags: string[];
  search_text: string;
  validation: string;
  lifecycle: string; // kept
  provenance: LibraryProvenance;
}

// POST /content/brand-voice/suggest
export interface VoiceSuggestion {
  before: string;
  after: string;
  rule: string;
  kind: string; // hype | ...
}
export interface BrandVoiceResult {
  brand_score: number; // 0..1
  suggestions: VoiceSuggestion[];
  advisory: boolean;
  mode: 'llm' | 'heuristic' | string;
  note: string;
}

// ---- WRITE request bodies (identity stamped server-side) --------------------
export interface RescheduleRequest {
  entry_id: string;
  new_date: string; // YYYY-MM-DD
}
export interface CalendarEntryRequest {
  title: string;
  channel: string;
  scheduled_date: string;
  status?: string;
  piece_ref?: string | null;
}
export interface BrandVoiceRequest {
  text: string;
}

// ===========================================================================
// Display helpers — channel colors, provenance labels, dates. No invented color.
// ===========================================================================
// The canonical display channels (capitalized) → token pair. The API speaks
// lowercase + a couple of extra channels (landing_page, tiktok), so we normalize.
export type ChannelKey = 'Substack' | 'X' | 'Instagram' | 'Facebook' | 'Podcast' | 'Email' | 'YouTube';
export const CHANNEL_COLOR: Record<ChannelKey, { bg: string; fg: string }> = {
  Substack: { bg: 'var(--signal-soft)', fg: 'var(--signal)' },
  X: { bg: 'var(--ink)', fg: 'var(--paper)' },
  Instagram: { bg: 'var(--gold-soft)', fg: 'var(--gold)' },
  Facebook: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  Podcast: { bg: 'var(--ok-soft)', fg: 'var(--ok)' },
  Email: { bg: 'var(--warn-soft)', fg: 'var(--warn)' },
  YouTube: { bg: 'var(--signal-soft)', fg: 'var(--signal)' },
};
const CHANNEL_ALIAS: Record<string, ChannelKey> = {
  substack: 'Substack',
  x: 'X',
  'x/twitter': 'X',
  twitter: 'X',
  instagram: 'Instagram',
  facebook: 'Facebook',
  podcast: 'Podcast',
  email: 'Email',
  youtube: 'YouTube',
};
// Map any wire channel string → a display label.
export function channelLabel(ch: string): string {
  const key = CHANNEL_ALIAS[ch?.toLowerCase()];
  if (key) return key === 'X' ? 'X / Twitter' : key;
  // Unmapped (landing_page, tiktok…) → Title Case the raw token.
  return (ch || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
// Map any wire channel string → token pair (falls back to a neutral chip).
export function channelColor(ch: string): { bg: string; fg: string } {
  const key = CHANNEL_ALIAS[ch?.toLowerCase()];
  return key ? CHANNEL_COLOR[key] : { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' };
}

// source_kind → provenance chip (mirrors Module 6's STOOD-IN / MANUAL labels).
export function sourceKindStyle(kind: string): { label: string; color: string; bg: string } {
  switch ((kind || '').toLowerCase()) {
    case 'live':
    case 'our_db':
      return { label: 'LIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    case 'stood_in':
      return { label: 'STOOD-IN', color: 'var(--ink-2)', bg: 'var(--accent-soft)' };
    case 'manual':
      return { label: 'MANUAL', color: 'var(--gold)', bg: 'var(--gold-soft)' };
    default:
      return { label: (kind || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}

export function statusStyle(status: string): { label: string; color: string; bg: string } {
  switch ((status || '').toLowerCase()) {
    case 'published':
      return { label: 'PUBLISHED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    case 'scheduled':
      return { label: 'SCHEDULED', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'planned':
      return { label: 'PLANNED', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
    default:
      return { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}

// "YYYY-MM-DD" → "Jun 15". Empty/invalid → "—".
export function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// "2026-06-15T..." → "Jun 15". For testimonial created_at timestamps.
export function fmtStamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ===========================================================================
// Seed fallbacks (rendered when the backbone is unreachable — honest "○ SAMPLE").
// Deliberately distinct from the live seed so a live render is unmistakable
// (e.g. productions 11 not 16, x 38% not 42%).
// ===========================================================================
export const SEED_OVERVIEW: ContentOverview = {
  productions_in_flight: 11,
  on_track: 6,
  on_track_pct: 55,
  this_week_publish_count: 4,
  top_piece_title: 'Sample — backbone unreachable',
  top_piece_conversions: 120,
  x_conversion_rate_pct: 38,
  channel_standins: [
    { channel: 'x', reach: 12000, source_kind: 'stood_in' },
    { channel: 'substack', reach: 6180, source_kind: 'manual' },
    { channel: 'instagram', reach: 8000, source_kind: 'stood_in' },
    { channel: 'email', reach: 4800, source_kind: 'manual' },
  ],
  library_count: 0,
  testimonial_stub_count: 0,
};

export const SEED_CALENDAR: ContentCalendar = {
  entries: [
    { entry_id: 'seed-0', title: 'Sample — founder thread', channel: 'x', scheduled_date: '2026-06-15', status: 'scheduled', piece_ref: null, owner: 'content' },
    { entry_id: 'seed-1', title: 'Sample — Substack essay', channel: 'substack', scheduled_date: '2026-06-15', status: 'planned', piece_ref: null, owner: 'content' },
    { entry_id: 'seed-2', title: 'Sample — newsletter', channel: 'email', scheduled_date: '2026-06-21', status: 'scheduled', piece_ref: null, owner: 'content' },
  ],
  conflict_dates: ['2026-06-15'],
  conflict_threshold: 4,
};

export const SEED_TESTIMONIALS: TestimonialStub[] = [
  {
    asset_id: 'seed-testimonial',
    title: 'Sample testimonial — backbone unreachable',
    body: 'Seed stub shown only when the backbone is down.',
    tags: ['testimonial'],
    source_ref: 'grassroots_testimonial',
    created_at: '2026-06-01T00:00:00+00:00',
  },
];

export const SEED_PERFORMANCE: ContentPerformance = {
  channels: [
    { channel: 'x', reach: 12000, clicks: 480, conversions: 180, conversion_rate_pct: 38, source_kind: 'stood_in', is_top: true, is_bottom: false },
    { channel: 'substack', reach: 6180, clicks: 410, conversions: 60, conversion_rate_pct: 14, source_kind: 'manual', is_top: false, is_bottom: false },
    { channel: 'facebook', reach: 5000, clicks: 220, conversions: 6, conversion_rate_pct: 3, source_kind: 'stood_in', is_top: false, is_bottom: true },
  ],
  top_pieces: [
    { piece_title: 'Sample top piece', channel: 'x', reach: 12000, clicks: 480, conversions: 180, conversion_rate_pct: 38, utm_attributed: true },
  ],
  bottom_pieces: [
    { piece_title: 'Sample bottom piece', channel: 'facebook', reach: 5000, clicks: 220, conversions: 6, conversion_rate_pct: 3, utm_attributed: false },
  ],
  content_to_conversion: [
    { piece_title: 'Sample top piece', channel: 'x', reach: 12000, clicks: 480, conversions: 180, conversion_rate_pct: 38, utm_attributed: true },
  ],
  unattributable_count: 0,
};

export const SEED_LIBRARY: LibraryAsset[] = [
  {
    id: 'seed-lib-0',
    title: 'Sample library asset — backbone unreachable',
    asset_type: 'copy',
    channel: 'x',
    format: 'copy',
    body: 'Seed asset shown only when the backbone is down.',
    asset_uri: null,
    source_ref: 'seed',
    tags: ['sample'],
    search_text: 'sample',
    validation: 'seed',
    lifecycle: 'kept',
    provenance: { generated_by: 'seed', created_at: '2026-06-01T00:00:00+00:00', model_ref: null, prompt_id: null, recipe_ref: null, brand_memory_refs: [], created_by_user: null },
  },
];

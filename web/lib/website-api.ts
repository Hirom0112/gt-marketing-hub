// Website & Digital Analytics (Module 13) view-model: typed wire shapes for the live
// FastAPI endpoints (app/api/website.py), the leadership-gated write bodies (flag a page →
// content refresh brief + decision; request analysis → decision), a seed fallback per
// resource (so a screen never blanks → honest "○ SAMPLE"), and the display helpers.
//
// HONESTY: the GA4 metrics come from a STOOD-IN simulated adapter (no live GA4 credential
// in this portal). `source_mode` is rendered as a badge ("GA4 STOOD-IN · SIMULATED") —
// never implied live. The traffic view's UTM validation runs the SAME rule set CRM Ops
// uses, so a broken campaign is flagged at the ORIGIN of the tags (the website).

// ===========================================================================
// READ wire shapes (GET /website/*) — match backend pydantic models exactly.
// ===========================================================================
export interface SiteMetric {
  site: string;
  sessions: number;
  users: number;
  new_users: number;
  returning_users: number;
  bounce_rate: number;
  avg_session_duration_s: number;
  pageviews: number;
}

export interface Subpage {
  page_path: string;
  site: string;
  page_type: string;
  pageviews: number;
  prev_pageviews: number;
  unique_visitors: number;
  avg_time_on_page_s: number;
  bounce_rate: number;
  exit_rate: number;
  conversions: number;
  trend_pct: number;
  refresh_candidate: boolean;
}

export interface TopPage {
  page_path: string;
  site: string;
  page_type: string;
  pageviews: number;
  trend_pct: number;
}

export interface Download {
  file_name: string;
  weekly_count: number;
  cumulative_count: number;
  prev_weekly_count: number;
  referring_page: string;
  source: string;
}

export interface SiteRollup {
  total_sessions: number;
  total_pageviews: number;
  total_new: number;
  total_returning: number;
  new_pct: number;
  returning_pct: number;
  avg_bounce_rate: number;
  avg_session_duration_s: number;
}

export interface DownloadSummary {
  total_weekly: number;
  total_cumulative: number;
  prev_weekly: number;
  wow_delta_pct: number;
}

export interface ChannelRow {
  channel: string;
  sessions: number;
  conversions: number;
  share_pct: number;
  conversion_rate: number;
}

export interface PlatformRow {
  platform: string;
  sessions: number;
  conversions: number;
  conversion_rate: number;
}

export interface TrafficBreakdown {
  total_sessions: number;
  total_conversions: number;
  channels: ChannelRow[];
  social_platforms: PlatformRow[];
}

export interface BrokenCampaign {
  utm_source: string;
  utm_medium: string;
  utm_campaign: string;
  sessions: number;
  landing_page: string;
  offending_keys: string[];
  reasons: string[];
}

export interface UtmValidation {
  total: number;
  healthy: number;
  broken: number;
  broken_count: number;
  health_pct: number;
  broken_campaigns: BrokenCampaign[];
}

export interface SourcePageCell {
  channel: string;
  page_path: string;
  sessions: number;
}

export interface FunnelStage {
  stage: string;
  sessions: number;
  of_top_pct: number;
  drop_from_prev_pct: number;
}

export interface KeyConversionPage {
  page_path: string;
  site: string;
  sessions: number;
  form_submissions: number;
  submission_rate: number;
}

export interface PathFlow {
  from_page: string;
  to_page: string;
  sessions: number;
}

export interface CrossSiteFlow {
  from_site: string;
  to_site: string;
  sessions: number;
}

export interface PageFlag {
  flag_id: string;
  page_path: string;
  site: string;
  reason: string;
  status: string;
  brief_entry_id: string | null;
  decision_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface AnalysisRequest {
  request_id: string;
  target: string;
  target_kind: string;
  question: string;
  status: string;
  decision_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface OverviewResponse {
  source_mode: string;
  site_rollup: SiteRollup;
  sites: SiteMetric[];
  download_summary: DownloadSummary;
  top_downloads: Download[];
  top_landing_pages: TopPage[];
  refresh_candidate_count: number;
  open_flag_count: number;
  open_request_count: number;
}

export interface SubpagesResponse {
  source_mode: string;
  pages: Subpage[];
  sites: string[];
  page_types: string[];
  bounce_warn_pct: number;
}

export interface TrafficResponse {
  source_mode: string;
  breakdown: TrafficBreakdown;
  source_pages: SourcePageCell[];
  utm_validation: UtmValidation;
}

export interface DownloadsResponse {
  source_mode: string;
  downloads: Download[];
  summary: DownloadSummary;
}

export interface PathsResponse {
  source_mode: string;
  funnel: FunnelStage[];
  key_conversion_pages: KeyConversionPage[];
  path_flows: PathFlow[];
  cross_site_flows: CrossSiteFlow[];
}

export interface InputsResponse {
  page_flags: PageFlag[];
  analysis_requests: AnalysisRequest[];
  open_flag_count: number;
  open_request_count: number;
}

// ===========================================================================
// WRITE wire shapes (leadership-gated; identity stamped server-side).
// ===========================================================================
export interface FlagPageRequest {
  page_path: string;
  site: string;
  reason: string;
  create_brief?: boolean;
  raise_decision?: boolean;
  recommendation?: string;
}
export interface FlagPageResponse {
  flag: PageFlag;
  brief_entry_id: string | null;
  brief_title: string | null;
  decision_id: string | null;
}
export interface AnalysisCreateRequest {
  target: string;
  target_kind: string;
  question: string;
  recommendation?: string;
}

// ===========================================================================
// Display helpers — tokens only (var(--…)); no invented color.
// ===========================================================================
// GA4 source_mode → honest badge. v1 is "simulated" (stood-in, never live).
export function sourceModeBadge(mode: string | null | undefined): { label: string; bg: string; color: string } {
  switch ((mode ?? '').toLowerCase()) {
    case 'simulated':
      return { label: 'GA4 STOOD-IN · SIMULATED', bg: 'var(--warn-soft)', color: 'var(--warn)' };
    case 'ga4_live':
      return { label: 'GA4 · LIVE', bg: 'var(--ok-soft)', color: 'var(--ok)' };
    default:
      return { label: (mode || '—').toUpperCase(), bg: 'var(--accent-soft)', color: 'var(--ink-3)' };
  }
}

export function siteLabel(site: string): string {
  return site;
}

// short label for a page path (keep the leading slash; collapse long blog paths).
export function pageLabel(path: string): string {
  return path || '/';
}

export function humanLabel(token: string): string {
  return (token || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// channel → chip color.
export const CHANNEL_COLOR: Record<string, string> = {
  organic: 'var(--ok)',
  direct: 'var(--ink-2)',
  social: 'var(--signal)',
  email: 'var(--gold)',
  referral: 'var(--accent-strong, var(--ink-3))',
};
export function channelColor(channel: string): string {
  return CHANNEL_COLOR[(channel ?? '').toLowerCase()] ?? 'var(--ink-3)';
}

// signed trend → arrow + color (↑ up = ok for traffic, ↓ down = signal).
export function trendArrow(pct: number): { glyph: string; color: string } {
  if (pct > 0) return { glyph: '↑', color: 'var(--ok)' };
  if (pct < 0) return { glyph: '↓', color: 'var(--signal)' };
  return { glyph: '→', color: 'var(--ink-3)' };
}

export function fmtPctRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}
export function fmtPct01(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}
export function fmtNum(n: number): string {
  return n.toLocaleString('en-US');
}
// seconds → "m:ss".
export function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// The closed list of target kinds the analysis-request form offers (backend rejects else).
export const TARGET_KINDS = ['page', 'campaign'] as const;

// ===========================================================================
// Seed fallbacks (rendered only when the backbone is unreachable → "○ SAMPLE").
// Numbers mirror the simulated GA4 adapter (app/adapters/analytics/simulated.py) +
// the pure core, so a static preview reads true; source_mode carried verbatim.
// ===========================================================================
const SEED_SITES: SiteMetric[] = [
  { site: 'gt.school', sessions: 8420, users: 6100, new_users: 4270, returning_users: 1830, bounce_rate: 0.42, avg_session_duration_s: 138.0, pageviews: 21850 },
  { site: 'anywhere.gt.school', sessions: 3110, users: 2480, new_users: 1990, returning_users: 490, bounce_rate: 0.55, avg_session_duration_s: 96.0, pageviews: 6720 },
];

const SEED_PAGES: Subpage[] = [
  { page_path: '/', site: 'gt.school', page_type: 'landing', pageviews: 5200, prev_pageviews: 4800, unique_visitors: 4100, avg_time_on_page_s: 64, bounce_rate: 0.38, exit_rate: 0.30, conversions: 120, trend_pct: 8, refresh_candidate: false },
  { page_path: '/tuition', site: 'gt.school', page_type: 'landing', pageviews: 3100, prev_pageviews: 2600, unique_visitors: 2500, avg_time_on_page_s: 142, bounce_rate: 0.34, exit_rate: 0.22, conversions: 240, trend_pct: 19, refresh_candidate: false },
  { page_path: '/how-it-works', site: 'gt.school', page_type: 'landing', pageviews: 2400, prev_pageviews: 2500, unique_visitors: 1900, avg_time_on_page_s: 175, bounce_rate: 0.36, exit_rate: 0.25, conversions: 95, trend_pct: -4, refresh_candidate: false },
  { page_path: '/accreditation', site: 'gt.school', page_type: 'resource', pageviews: 1450, prev_pageviews: 1100, unique_visitors: 1200, avg_time_on_page_s: 150, bounce_rate: 0.40, exit_rate: 0.33, conversions: 60, trend_pct: 32, refresh_candidate: false },
  { page_path: '/apply', site: 'gt.school', page_type: 'form', pageviews: 1280, prev_pageviews: 1150, unique_visitors: 1050, avg_time_on_page_s: 210, bounce_rate: 0.20, exit_rate: 0.18, conversions: 330, trend_pct: 11, refresh_candidate: false },
  { page_path: '/blog/2-hour-learning', site: 'gt.school', page_type: 'blog', pageviews: 1850, prev_pageviews: 2100, unique_visitors: 1600, avg_time_on_page_s: 220, bounce_rate: 0.62, exit_rate: 0.55, conversions: 18, trend_pct: -12, refresh_candidate: true },
  { page_path: '/blog/is-my-kid-gifted', site: 'gt.school', page_type: 'blog', pageviews: 1320, prev_pageviews: 980, unique_visitors: 1180, avg_time_on_page_s: 190, bounce_rate: 0.58, exit_rate: 0.50, conversions: 22, trend_pct: 35, refresh_candidate: false },
  { page_path: '/summer-camp', site: 'gt.school', page_type: 'landing', pageviews: 1600, prev_pageviews: 900, unique_visitors: 1350, avg_time_on_page_s: 120, bounce_rate: 0.45, exit_rate: 0.38, conversions: 70, trend_pct: 78, refresh_candidate: false },
  { page_path: '/esa-guide', site: 'gt.school', page_type: 'resource', pageviews: 980, prev_pageviews: 720, unique_visitors: 820, avg_time_on_page_s: 240, bounce_rate: 0.30, exit_rate: 0.28, conversions: 140, trend_pct: 36, refresh_candidate: false },
  { page_path: '/about', site: 'gt.school', page_type: 'about', pageviews: 760, prev_pageviews: 800, unique_visitors: 640, avg_time_on_page_s: 88, bounce_rate: 0.55, exit_rate: 0.48, conversions: 8, trend_pct: -5, refresh_candidate: false },
  { page_path: '/', site: 'anywhere.gt.school', page_type: 'landing', pageviews: 2100, prev_pageviews: 1700, unique_visitors: 1700, avg_time_on_page_s: 70, bounce_rate: 0.52, exit_rate: 0.40, conversions: 55, trend_pct: 24, refresh_candidate: false },
  { page_path: '/online-program', site: 'anywhere.gt.school', page_type: 'landing', pageviews: 1400, prev_pageviews: 1500, unique_visitors: 1150, avg_time_on_page_s: 130, bounce_rate: 0.66, exit_rate: 0.58, conversions: 30, trend_pct: -7, refresh_candidate: true },
  { page_path: '/pricing', site: 'anywhere.gt.school', page_type: 'landing', pageviews: 1180, prev_pageviews: 980, unique_visitors: 980, avg_time_on_page_s: 120, bounce_rate: 0.40, exit_rate: 0.30, conversions: 90, trend_pct: 20, refresh_candidate: false },
  { page_path: '/apply', site: 'anywhere.gt.school', page_type: 'form', pageviews: 540, prev_pageviews: 480, unique_visitors: 470, avg_time_on_page_s: 200, bounce_rate: 0.22, exit_rate: 0.20, conversions: 130, trend_pct: 13, refresh_candidate: false },
  { page_path: '/faq', site: 'anywhere.gt.school', page_type: 'resource', pageviews: 700, prev_pageviews: 760, unique_visitors: 600, avg_time_on_page_s: 160, bounce_rate: 0.50, exit_rate: 0.45, conversions: 12, trend_pct: -8, refresh_candidate: false },
];

const SEED_DOWNLOADS: Download[] = [
  { file_name: 'GT-School-Tuition-and-ESA-Guide.pdf', weekly_count: 142, cumulative_count: 1880, prev_weekly_count: 120, referring_page: '/esa-guide', source: 'organic' },
  { file_name: 'Summer-Camp-2026-Brochure.pdf', weekly_count: 120, cumulative_count: 610, prev_weekly_count: 70, referring_page: '/summer-camp', source: 'social' },
  { file_name: '2-Hour-Learning-Whitepaper.pdf', weekly_count: 96, cumulative_count: 1240, prev_weekly_count: 110, referring_page: '/blog/2-hour-learning', source: 'organic' },
  { file_name: 'Accreditation-FAQ.pdf', weekly_count: 78, cumulative_count: 940, prev_weekly_count: 60, referring_page: '/accreditation', source: 'referral' },
  { file_name: 'Sample-Daily-Schedule.pdf', weekly_count: 54, cumulative_count: 720, prev_weekly_count: 58, referring_page: '/how-it-works', source: 'direct' },
  { file_name: 'Parent-Handbook.pdf', weekly_count: 33, cumulative_count: 410, prev_weekly_count: 31, referring_page: '/about', source: 'email' },
];

const SEED_ROLLUP: SiteRollup = {
  total_sessions: 11530, total_pageviews: 28570, total_new: 6260, total_returning: 2320,
  new_pct: 73, returning_pct: 27, avg_bounce_rate: 0.4551, avg_session_duration_s: 126.7,
};
const SEED_DL_SUMMARY: DownloadSummary = { total_weekly: 523, total_cumulative: 5800, prev_weekly: 449, wow_delta_pct: 16 };

const SEED_BREAKDOWN: TrafficBreakdown = {
  total_sessions: 11530,
  total_conversions: 879,
  channels: [
    { channel: 'organic', sessions: 4900, conversions: 360, share_pct: 42, conversion_rate: 0.0735 },
    { channel: 'direct', sessions: 2600, conversions: 210, share_pct: 23, conversion_rate: 0.0808 },
    { channel: 'social', sessions: 2180, conversions: 104, share_pct: 19, conversion_rate: 0.0477 },
    { channel: 'email', sessions: 1100, conversions: 150, share_pct: 10, conversion_rate: 0.1364 },
    { channel: 'referral', sessions: 750, conversions: 55, share_pct: 7, conversion_rate: 0.0733 },
  ],
  social_platforms: [
    { platform: 'x', sessions: 820, conversions: 40, conversion_rate: 0.0488 },
    { platform: 'instagram', sessions: 720, conversions: 36, conversion_rate: 0.05 },
    { platform: 'facebook', sessions: 640, conversions: 28, conversion_rate: 0.0438 },
  ],
};

const SEED_UTM: UtmValidation = {
  total: 6, healthy: 3, broken: 3, broken_count: 3, health_pct: 50,
  broken_campaigns: [
    { utm_source: 'instagram', utm_medium: 'social', utm_campaign: '', sessions: 230, landing_page: '/online-program', offending_keys: ['utm_campaign'], reasons: ["required key 'utm_campaign' is missing or blank"] },
    { utm_source: 'qr_flyer', utm_medium: 'qr_code', utm_campaign: 'field_event_q2', sessions: 180, landing_page: '/', offending_keys: ['utm_medium'], reasons: ["utm_medium 'qr_code' not in allowed mediums"] },
    { utm_source: 'Partner', utm_medium: 'Referral', utm_campaign: 'partner_blast', sessions: 90, landing_page: '/accreditation', offending_keys: ['utm_source', 'utm_medium'], reasons: ["'utm_source' must be lowercase: 'Partner'", "utm_medium 'Referral' not in allowed mediums"] },
  ],
};

const SEED_SOURCE_PAGES: SourcePageCell[] = [
  { channel: 'organic', page_path: '/', sessions: 1800 },
  { channel: 'direct', page_path: '/', sessions: 1200 },
  { channel: 'organic', page_path: '/tuition', sessions: 1100 },
  { channel: 'organic', page_path: '/blog/2-hour-learning', sessions: 900 },
  { channel: 'social', page_path: '/summer-camp', sessions: 700 },
  { channel: 'email', page_path: '/tuition', sessions: 520 },
  { channel: 'organic', page_path: '/esa-guide', sessions: 480 },
  { channel: 'direct', page_path: '/apply', sessions: 400 },
  { channel: 'referral', page_path: '/accreditation', sessions: 300 },
  { channel: 'email', page_path: '/apply', sessions: 280 },
];

const SEED_FUNNEL: FunnelStage[] = [
  { stage: 'landing', sessions: 11530, of_top_pct: 100, drop_from_prev_pct: 0 },
  { stage: 'program_page', sessions: 4200, of_top_pct: 36, drop_from_prev_pct: 64 },
  { stage: 'tuition_pricing', sessions: 2300, of_top_pct: 20, drop_from_prev_pct: 45 },
  { stage: 'apply_start', sessions: 1180, of_top_pct: 10, drop_from_prev_pct: 49 },
  { stage: 'apply_submit', sessions: 460, of_top_pct: 4, drop_from_prev_pct: 61 },
];

const SEED_KEY_PAGES: KeyConversionPage[] = [
  { page_path: '/apply', site: 'gt.school', sessions: 1050, form_submissions: 330, submission_rate: 0.3143 },
  { page_path: '/apply', site: 'anywhere.gt.school', sessions: 470, form_submissions: 130, submission_rate: 0.2766 },
  { page_path: '/tuition', site: 'gt.school', sessions: 2500, form_submissions: 240, submission_rate: 0.096 },
  { page_path: '/pricing', site: 'anywhere.gt.school', sessions: 980, form_submissions: 90, submission_rate: 0.0918 },
  { page_path: '/summer-camp', site: 'gt.school', sessions: 1350, form_submissions: 70, submission_rate: 0.0519 },
];

const SEED_FLOWS: PathFlow[] = [
  { from_page: '/', to_page: '/tuition', sessions: 1400 },
  { from_page: '/', to_page: '/how-it-works', sessions: 1100 },
  { from_page: '/', to_page: '/summer-camp', sessions: 720 },
  { from_page: '/tuition', to_page: '/apply', sessions: 560 },
  { from_page: '/how-it-works', to_page: '/tuition', sessions: 480 },
  { from_page: '/tuition', to_page: '/esa-guide', sessions: 340 },
];

const SEED_CROSS: CrossSiteFlow[] = [
  { from_site: 'gt.school', to_site: 'anywhere.gt.school', sessions: 410 },
  { from_site: 'anywhere.gt.school', to_site: 'gt.school', sessions: 230 },
];

const SEED_FLAGS: PageFlag[] = [
  { flag_id: 'pf-0', page_path: '/blog/2-hour-learning', site: 'gt.school', reason: '62% bounce, traffic down 12% WoW — top-of-funnel explainer reads as thin.', status: 'open', brief_entry_id: 'ce-0', decision_id: 'dec-0', created_at: '2026-06-11T12:00:00Z', resolved_at: null },
  { flag_id: 'pf-1', page_path: '/online-program', site: 'anywhere.gt.school', reason: '66% bounce on a key landing page — refreshed hero shipped, monitoring.', status: 'resolved', brief_entry_id: 'ce-1', decision_id: null, created_at: '2026-06-03T12:00:00Z', resolved_at: '2026-06-12T12:00:00Z' },
];

const SEED_REQUESTS: AnalysisRequest[] = [
  { request_id: 'ar-0', target: '/tuition', target_kind: 'page', question: 'Why did /tuition pageviews jump 19% WoW — which source drove it, and does it convert?', status: 'open', decision_id: 'dec-1', created_at: '2026-06-13T12:00:00Z', resolved_at: null },
  { request_id: 'ar-1', target: 'spring_open_house', target_kind: 'campaign', question: 'Did the spring_open_house campaign actually move summer-camp registrations?', status: 'resolved', decision_id: null, created_at: '2026-06-05T12:00:00Z', resolved_at: '2026-06-10T12:00:00Z' },
];

export const SEED_OVERVIEW: OverviewResponse = {
  source_mode: 'simulated',
  site_rollup: SEED_ROLLUP,
  sites: SEED_SITES,
  download_summary: SEED_DL_SUMMARY,
  top_downloads: SEED_DOWNLOADS.slice(0, 5),
  top_landing_pages: [
    { page_path: '/', site: 'gt.school', page_type: 'landing', pageviews: 5200, trend_pct: 8 },
    { page_path: '/tuition', site: 'gt.school', page_type: 'landing', pageviews: 3100, trend_pct: 19 },
    { page_path: '/how-it-works', site: 'gt.school', page_type: 'landing', pageviews: 2400, trend_pct: -4 },
    { page_path: '/', site: 'anywhere.gt.school', page_type: 'landing', pageviews: 2100, trend_pct: 24 },
    { page_path: '/blog/2-hour-learning', site: 'gt.school', page_type: 'blog', pageviews: 1850, trend_pct: -12 },
  ],
  refresh_candidate_count: 2,
  open_flag_count: 1,
  open_request_count: 1,
};

export const SEED_SUBPAGES: SubpagesResponse = {
  source_mode: 'simulated',
  pages: [...SEED_PAGES].sort((a, b) => b.pageviews - a.pageviews),
  sites: ['gt.school', 'anywhere.gt.school'],
  page_types: ['landing', 'blog', 'resource', 'form', 'about'],
  bounce_warn_pct: 0.6,
};

export const SEED_TRAFFIC: TrafficResponse = {
  source_mode: 'simulated',
  breakdown: SEED_BREAKDOWN,
  source_pages: SEED_SOURCE_PAGES,
  utm_validation: SEED_UTM,
};

export const SEED_DOWNLOADS_RESP: DownloadsResponse = {
  source_mode: 'simulated',
  downloads: SEED_DOWNLOADS,
  summary: SEED_DL_SUMMARY,
};

export const SEED_PATHS: PathsResponse = {
  source_mode: 'simulated',
  funnel: SEED_FUNNEL,
  key_conversion_pages: SEED_KEY_PAGES,
  path_flows: SEED_FLOWS,
  cross_site_flows: SEED_CROSS,
};

export const SEED_INPUTS: InputsResponse = {
  page_flags: SEED_FLAGS,
  analysis_requests: SEED_REQUESTS,
  open_flag_count: 1,
  open_request_count: 1,
};

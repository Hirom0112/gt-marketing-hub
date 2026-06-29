// CRM / Marketing Operations (Module 7) view-model: typed wire shapes for the live
// FastAPI backbone (app/api/crm_ops.py), the owner-gated write bodies (file issue +
// triage PATCH), a seed fallback per resource (so a screen never blanks when the
// backbone is down → honest "○ SAMPLE"), and the display helpers (source-provenance
// badges, severity/status/category chips, date formatting). Mirrors lib/nurture-api.ts.
//
// HONESTY: every response carries a `source` (or `correlation_source`) string. The UI
// renders provenance from THAT field — never a hard-coded badge — so the seed fallback
// (which carries the same labels verbatim) stays truthful about where a number lives:
//   crm_aggregate          → LIVE HUBSPOT · AGGREGATE   (the lead-score histogram/tiers)
//   derived_synthetic      → DERIVED (not live)         (the score→conversion table)
//   supabase⇄hubspot       → SUPABASE ⇄ HUBSPOT         (A4 sync parity)
//   supabase_attribution_utm → SUPABASE · attribution_utm (per-param resolution)
//   synthetic              → SYNTHETIC                  (synthesized last-sync stamps)
// UTM attribution reads as a PERMANENT RED flag — it is broken upstream until rebuilt.

// ===========================================================================
// READ wire shapes (GET /crm/ops/*) — match backend pydantic models exactly.
// ===========================================================================
export interface FieldFlag {
  field: string;
  status: string; // "reliable" | "unreliable"
  reason: string | null;
}
export interface UtmEntity {
  entity_id: string;
  offending_keys: string[];
  reasons: string[];
}
export interface LeadScoreBand {
  label: string;
  low: number;
  high: number;
  count: number;
}
export interface LeadScoreTiers {
  cold: number;
  warm: number;
  hot: number;
}
export interface LeadScoreDistribution {
  bands: LeadScoreBand[];
  total: number;
  mean: number;
  threshold: number;
  tiers: LeadScoreTiers;
  source: string; // crm_aggregate → LIVE HUBSPOT · AGGREGATE
}
export interface ConnectorSync {
  connector: string;
  last_sync: string; // ISO
  source: string; // synthetic → SYNTHETIC
}

// GET /crm/ops/overview — the 5a rollup.
export interface OverviewResponse {
  parity_overall: number; // fraction [0,1]
  data_confidence_banner: boolean;
  utm_ok: number;
  utm_broken: number;
  lead_score_distribution: LeadScoreDistribution;
  open_dq_count: number;
  last_sync: ConnectorSync[];
  field_flags: FieldFlag[];
}

// GET /crm/ops/source-tracking — the 5b view.
export interface UtmParamResolution {
  param: string;
  resolved: number;
  total: number;
  resolved_pct: number;
}
export interface AttributionChainStep {
  step: number;
  label: string;
  status: string; // "ok" | ...
}
export interface FixLogEntry {
  fix_id: string;
  kind: string; // utm_fix | scoring_change
  summary: string;
  actor: string;
  applied_at: string; // ISO
}
export interface SourceTrackingResponse {
  params: UtmParamResolution[];
  broken_utm: UtmEntity[];
  attribution_chain: AttributionChainStep[];
  fix_log: FixLogEntry[];
  source: string; // supabase_attribution_utm
}

// GET /crm/ops/lead-scoring — the 5c view.
export interface CorrelationRow {
  band: string;
  conversion_pct: number;
}
export interface LeadScoringResponse {
  distribution: LeadScoreDistribution;
  correlation: CorrelationRow[];
  correlation_source: string; // derived_synthetic → DERIVED (not live)
  model_description: string;
  threshold: number;
  change_log: FixLogEntry[];
}

// GET /crm/ops/sync-parity — the 5d view.
export interface DriftAlert {
  field: string;
  parity: number;
  floor: number;
}
export interface SyncParityResponse {
  parity_overall: number;
  parity_by_field: Record<string, number>;
  field_flags: FieldFlag[];
  drift_alerts: DriftAlert[];
  rule_of_truth: string;
  source: string; // supabase⇄hubspot
}

// GET /crm/ops/data-quality — the 5e view + the write results.
export interface Issue {
  issue_id: string;
  signature: string;
  category: string; // utm | sync | scoring | tracking | other
  kind: string;
  severity: string; // high | medium | low
  description: string;
  owner: string;
  status: string; // open | acknowledged | resolved
  source: string; // auto | manual
  entity_ref: string;
  priority: string; // urgent | normal
  created_at: string; // ISO
  resolved_at: string | null;
  resolution: string;
  resolved_by: string;
}
export interface DataQualityResponse {
  open_issues: Issue[];
  resolution_log: Issue[];
}
export interface ScanResult {
  scanned: number;
  detected: number;
  open_dq_count: number;
}

// POST /crm/ops/data-quality — file a MANUAL issue (owner stamped server-side).
export interface FileIssueRequest {
  category: string;
  kind: string;
  severity?: string;
  description?: string;
  entity_ref?: string;
  priority?: string;
}
// PATCH /crm/ops/data-quality/{id} — acknowledge / prioritize / resolve.
export interface UpdateIssueRequest {
  status?: string;
  priority?: string;
  resolution?: string;
}

// Closed value sets (mirror backend crm_ops_store + decisions_store; 422 otherwise).
export const CATEGORIES = ['utm', 'sync', 'scoring', 'tracking', 'other'] as const;
export const SEVERITIES = ['high', 'medium', 'low'] as const;
export const PRIORITIES = ['urgent', 'normal'] as const;
export const ISSUE_STATUSES = ['open', 'acknowledged', 'resolved'] as const;

// ===========================================================================
// Source-provenance badges. The honesty contract: map the backend `source`
// string → a badge tone + label. No invented provenance.
// ===========================================================================
export type BadgeTone = 'live' | 'derived' | 'parity' | 'synthetic' | 'neutral';
export interface SourceBadgeInfo {
  label: string;
  tone: BadgeTone;
}
export function sourceBadge(source: string | null | undefined): SourceBadgeInfo {
  switch ((source ?? '').toLowerCase()) {
    case 'crm_aggregate':
      return { label: 'LIVE HUBSPOT · AGGREGATE', tone: 'live' };
    case 'derived_synthetic':
      return { label: 'DERIVED · NOT LIVE', tone: 'derived' };
    case 'supabase⇄hubspot':
      return { label: 'SUPABASE ⇄ HUBSPOT', tone: 'parity' };
    case 'supabase_attribution_utm':
      return { label: 'SUPABASE · attribution_utm', tone: 'parity' };
    case 'synthetic':
      return { label: 'SYNTHETIC', tone: 'synthetic' };
    case 'auto':
      return { label: 'AUTO-DETECTED', tone: 'derived' };
    case 'manual':
      return { label: 'FILED', tone: 'neutral' };
    default:
      return { label: (source || '—').toUpperCase(), tone: 'neutral' };
  }
}
export function badgeStyle(tone: BadgeTone): { bg: string; color: string } {
  switch (tone) {
    case 'live':
      return { bg: 'var(--ok-soft)', color: 'var(--ok)' };
    case 'derived':
      return { bg: 'var(--warn-soft)', color: 'var(--warn)' };
    case 'parity':
      return { bg: 'var(--signal-soft)', color: 'var(--signal)' };
    case 'synthetic':
      return { bg: 'var(--accent-soft)', color: 'var(--ink-2)' };
    default:
      return { bg: 'var(--accent-soft)', color: 'var(--ink-3)' };
  }
}

// ===========================================================================
// Display helpers — severity / status / category chips, labels, dates.
// No invented color: tokens only (var(--…)).
// ===========================================================================
export function severityStyle(sev: string): { label: string; color: string; bg: string } {
  switch ((sev ?? '').toLowerCase()) {
    case 'high':
      return { label: 'HIGH', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'medium':
      return { label: 'MEDIUM', color: 'var(--warn)', bg: 'var(--warn-soft)' };
    case 'low':
      return { label: 'LOW', color: 'var(--ink-2)', bg: 'var(--accent-soft)' };
    default:
      return { label: (sev || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}
export function statusStyle(status: string): { label: string; color: string; bg: string } {
  switch ((status ?? '').toLowerCase()) {
    case 'open':
      return { label: 'OPEN', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'acknowledged':
      return { label: 'ACKNOWLEDGED', color: 'var(--warn)', bg: 'var(--warn-soft)' };
    case 'resolved':
      return { label: 'RESOLVED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    default:
      return { label: (status || '—').toUpperCase(), color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
  }
}
const CATEGORY_LABEL: Record<string, string> = {
  utm: 'UTM / attribution',
  sync: 'Sync parity',
  scoring: 'Lead scoring',
  tracking: 'Tracking',
  other: 'Other',
};
export function categoryLabel(c: string): string {
  return CATEGORY_LABEL[(c ?? '').toLowerCase()] ?? (c || '—');
}
export function priorityStyle(p: string): { color: string; bg: string } {
  return (p ?? '').toLowerCase() === 'urgent'
    ? { color: 'var(--signal)', bg: 'var(--signal-soft)' }
    : { color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
}
// A tracked field's human label (parity by_field / drift alerts / chain steps).
const FIELD_LABEL: Record<string, string> = {
  stage: 'Pipeline stage',
  funding_state: 'Funding state',
  owner: 'Record owner',
  tefa_amount: 'TEFA amount',
  attribution_source: 'Attribution source',
  income_tier: 'Income tier',
  app_form_submit: 'Form submit',
  supabase_app_form: 'Supabase app_form',
  hubspot_contact: 'HubSpot contact',
};
export function fieldLabel(f: string): string {
  return FIELD_LABEL[(f ?? '').toLowerCase()] ?? (f || '—').replace(/_/g, ' ');
}
export function fmtPct(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}
// "YYYY-MM-DDTHH:MM:SS" → "Jun 14, 2:00 PM" (compact). Empty/invalid → "—".
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ', ' +
    d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  );
}
// "…" → "3d ago" relative age (UTC-safe, coarse). Empty → "—".
export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  return `${Math.round(hrs / 24)}d`;
}
export function connectorLabel(c: string): string {
  return (c ?? '').replace(/_/g, ' ').replace(/\bhubspot\b/i, 'HubSpot').replace(/\bsupabase\b/i, 'Supabase');
}

// ===========================================================================
// Seed fallbacks (rendered only when the backbone is unreachable → "○ SAMPLE").
// Numbers mirror the backend defaults (params/params.yaml crm_ops + migration 0041
// seed); the `source` labels are carried verbatim so provenance badges stay honest
// even offline. UTM attribution is BROKEN in the seed exactly as it is live.
// ===========================================================================
const SEED_FIELD_FLAGS: FieldFlag[] = [
  { field: 'tefa_amount', status: 'unreliable', reason: "'tefa_amount' is a known low-trust field — value flagged unreliable." },
  { field: 'attribution_source', status: 'unreliable', reason: "'attribution_source' is a known low-trust field — value flagged unreliable." },
  { field: 'income_tier', status: 'unreliable', reason: "'income_tier' is a known low-trust field — value flagged unreliable." },
];

const SEED_DISTRIBUTION: LeadScoreDistribution = {
  bands: [
    { label: '0–20', low: 0, high: 20, count: 520 },
    { label: '20–40', low: 20, high: 40, count: 980 },
    { label: '40–60', low: 40, high: 60, count: 1140 },
    { label: '60–80', low: 60, high: 80, count: 900 },
    { label: '80–100', low: 80, high: 100, count: 500 },
  ],
  total: 4040,
  mean: 47.8,
  threshold: 60,
  tiers: { cold: 1500, warm: 2040, hot: 500 },
  source: 'crm_aggregate',
};

const SEED_BROKEN_UTM: UtmEntity[] = [
  { entity_id: 'Family-0007', offending_keys: ['utm_medium'], reasons: ["utm_medium 'e-mail' not in allowed mediums ['email', 'social', 'cpc', 'organic', 'referral', 'event']"] },
  { entity_id: 'Family-0019', offending_keys: ['utm_campaign'], reasons: ['utm_campaign missing or blank'] },
  { entity_id: 'Family-0033', offending_keys: ['utm_source', 'utm_campaign'], reasons: ['utm_source missing or blank', 'utm_campaign missing or blank'] },
];

const SEED_FIX_LOG: FixLogEntry[] = [
  { fix_id: 'fix-0', kind: 'utm_fix', summary: "Normalized utm_medium 'e-mail' → 'email' on the email nurture campaign.", actor: 'crm', applied_at: '2026-06-15T06:00:00Z' },
  { fix_id: 'fix-1', kind: 'utm_fix', summary: 'Backfilled missing utm_campaign on the apply landing page.', actor: 'leader', applied_at: '2026-06-13T12:00:00Z' },
];
const SEED_SCORING_LOG: FixLogEntry[] = [
  { fix_id: 'fix-2', kind: 'scoring_change', summary: 'Raised the lead-score qualification threshold 55 → 60 for the fall cohort.', actor: 'leader', applied_at: '2026-06-14T12:00:00Z' },
];

export const SEED_OVERVIEW: OverviewResponse = {
  parity_overall: 0.962,
  data_confidence_banner: false,
  utm_ok: 41,
  utm_broken: 9,
  lead_score_distribution: SEED_DISTRIBUTION,
  open_dq_count: 3,
  last_sync: [
    { connector: 'hubspot_contacts', last_sync: '2026-06-15T11:55:00Z', source: 'synthetic' },
    { connector: 'hubspot_deals', last_sync: '2026-06-15T11:50:00Z', source: 'synthetic' },
    { connector: 'supabase_app_form', last_sync: '2026-06-15T11:45:00Z', source: 'synthetic' },
  ],
  field_flags: SEED_FIELD_FLAGS,
};

export const SEED_SOURCE_TRACKING: SourceTrackingResponse = {
  params: [
    { param: 'utm_source', resolved: 44, total: 50, resolved_pct: 88.0 },
    { param: 'utm_medium', resolved: 41, total: 50, resolved_pct: 82.0 },
    { param: 'utm_campaign', resolved: 33, total: 50, resolved_pct: 66.0 },
    { param: 'utm_content', resolved: 12, total: 50, resolved_pct: 24.0 },
  ],
  broken_utm: SEED_BROKEN_UTM,
  attribution_chain: [
    { step: 1, label: 'app_form_submit', status: 'ok' },
    { step: 2, label: 'supabase_app_form', status: 'ok' },
    { step: 3, label: 'hubspot_contact', status: 'ok' },
  ],
  fix_log: SEED_FIX_LOG,
  source: 'supabase_attribution_utm',
};

export const SEED_LEAD_SCORING: LeadScoringResponse = {
  distribution: SEED_DISTRIBUTION,
  correlation: [
    { band: '0–20', conversion_pct: 0.0 },
    { band: '20–40', conversion_pct: 20.0 },
    { band: '40–60', conversion_pct: 40.0 },
    { band: '60–80', conversion_pct: 60.0 },
    { band: '80–100', conversion_pct: 80.0 },
  ],
  correlation_source: 'derived_synthetic',
  model_description:
    "Lead score is HubSpot's gt_lead_score (0–100), read aggregate-only and DISPLAY-only. A lead qualifies at/above the configured threshold; the cockpit never edits the score.",
  threshold: 60,
  change_log: SEED_SCORING_LOG,
};

export const SEED_SYNC_PARITY: SyncParityResponse = {
  parity_overall: 0.962,
  parity_by_field: { stage: 0.98, funding_state: 0.97, owner: 0.88 },
  field_flags: SEED_FIELD_FLAGS,
  drift_alerts: [{ field: 'owner', parity: 0.88, floor: 0.9 }],
  rule_of_truth: 'Supabase app_form is the source of truth for funnel/TEFA/income',
  source: 'supabase⇄hubspot',
};

export const SEED_DATA_QUALITY: DataQualityResponse = {
  open_issues: [
    { issue_id: 'iss-0', signature: 'Family-0007:utm_broken', category: 'utm', kind: 'utm_broken', severity: 'high', description: "Broken UTM: utm_medium 'e-mail' not in the allowed-medium set.", owner: 'crm', status: 'open', source: 'auto', entity_ref: 'Family-0007', priority: 'urgent', created_at: '2026-06-15T12:00:00Z', resolved_at: null, resolution: '', resolved_by: '' },
    { issue_id: 'iss-1', signature: 'Family-0012:conflict', category: 'sync', kind: 'conflict', severity: 'high', description: 'Supabase and HubSpot diverge on stage — needs a reconcile decision.', owner: 'crm', status: 'acknowledged', source: 'auto', entity_ref: 'Family-0012', priority: 'normal', created_at: '2026-06-15T12:00:00Z', resolved_at: null, resolution: '', resolved_by: '' },
    { issue_id: 'iss-2', signature: 'manual:scoring-review-01', category: 'scoring', kind: 'scoring_review', severity: 'medium', description: 'Lead-score model flagged for review: threshold may be too low for fall cohort.', owner: 'crm', status: 'open', source: 'manual', entity_ref: '', priority: 'normal', created_at: '2026-06-15T12:00:00Z', resolved_at: null, resolution: '', resolved_by: '' },
    { issue_id: 'iss-3', signature: 'Family-0021:unreliable_field', category: 'other', kind: 'unreliable_field', severity: 'low', description: "Low-trust field 'income_tier' present — value is self-reported, unreliable.", owner: 'crm', status: 'open', source: 'auto', entity_ref: 'Family-0021', priority: 'normal', created_at: '2026-06-15T12:00:00Z', resolved_at: null, resolution: '', resolved_by: '' },
  ],
  resolution_log: [
    { issue_id: 'iss-4', signature: 'manual:tracking-fix-01', category: 'tracking', kind: 'missing_field', severity: 'medium', description: 'Form submissions missing utm_campaign — landing-page tag was dropped.', owner: 'crm', status: 'resolved', source: 'manual', entity_ref: '', priority: 'normal', created_at: '2026-06-13T12:00:00Z', resolved_at: '2026-06-13T12:00:00Z', resolution: 'Re-added the campaign tag to the apply landing page; backfilled the gap.', resolved_by: 'leader' },
  ],
};

// The kind options the file-issue form offers per category (manual issues; the
// backend stores the kind free-form, these are sensible defaults per category).
export const FILE_ISSUE_KINDS = [
  'utm_broken',
  'conflict',
  'unreliable_field',
  'missing_field',
  'mojibake',
  'scoring_review',
  'other',
] as const;

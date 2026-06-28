// Shared view model for Module 6 (Dashboard / KPI Tracking). JSX-free so both the
// scorecard table and the sub-view tabs (Trends / SLA / Goal pacing / HubSpot mirror)
// import the SAME types, kind vocabulary, and formatters — no duplication, no cycle.

// ---- Provenance: where a number comes from (backend MetricProvenance) --------
// kind ∈ live | our_db | derived | stood_in | uninstrumented — the trust class.
export interface ProvenanceView {
  system: string; // "Supabase" | "HubSpot" | "Stripe" | "Grassroots" | "Derived" | "—"
  locator: string; // exact table.column / function
  kind: string;
  compute: string; // one-line human formula
  lastSync: string | null;
}

// kind → how the SOURCE dot + panel badge reads. One home for the vocabulary.
export const KIND: Record<string, { label: string; dot: string; tip: string }> = {
  live: { label: 'LIVE', dot: 'var(--ok)', tip: 'Live API call to a real external service.' },
  our_db: { label: 'OUR DB', dot: 'var(--brand)', tip: "The Hub's own database (Supabase) — our source of record." },
  derived: { label: 'DERIVED', dot: 'var(--warn)', tip: 'Computed in core logic from other sources — never stored twice.' },
  stood_in: { label: 'STOOD-IN', dot: 'var(--ink-3)', tip: 'Source not reachable yet — seeded behind the real interface, labeled.' },
  uninstrumented: { label: 'NOT INSTRUMENTED', dot: 'var(--signal)', tip: 'Genuinely not measured yet — shown as a gap, never faked green.' },
};

export function kindOf(kind: string) {
  return KIND[kind] ?? { label: kind.toUpperCase() || '—', dot: 'var(--ink-3)', tip: 'Source kind unknown.' };
}

// One scorecard row — display strings (table) + raw numbers (charts/pacing).
export interface KpiRow {
  key: string;
  name: string;
  note: string;
  now: string;
  last: string;
  delta: string;
  deltaColor: string;
  target: string;
  status: string;
  statusBg: string;
  statusColor: string;
  prov: ProvenanceView;
  // raw numerics for the Trends / Goal-pacing tabs:
  nowNum: number;
  targetNum: number;
  projection: number;
  sparkline: number[];
  rate: boolean;
  statusKey: string; // green | yellow | red | uninstrumented
}

// ---- Live /scorecard/weekly shape (from backend/app/api/scorecard.py) -------
export interface MetricProvenanceApi {
  system: string;
  locator: string;
  kind: string;
  compute: string;
  last_sync: string | null;
}
export interface ScorecardMetricApi {
  key: string;
  label: string;
  this_week: number;
  last_week: number;
  delta: number;
  sparkline: number[];
  target: number;
  status: string; // 'green' | 'yellow' | 'red'
  projection: number;
  provenance?: MetricProvenanceApi;
}
export interface WeeklyScorecardApi {
  metrics?: ScorecardMetricApi[];
  as_of?: string;
  goal_date?: string;
}

// Map the backend green/yellow/red band onto the scorecard's status pills/tokens.
export function statusPresentation(status: string): { label: string; statusBg: string; statusColor: string } {
  if (status === 'green') return { label: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' };
  if (status === 'yellow') return { label: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)' };
  if (status === 'red') return { label: 'AT RISK', statusBg: 'var(--signal-soft)', statusColor: 'var(--signal)' };
  return { label: status.toUpperCase() || '—', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' };
}

// A weekly count may arrive as 5.0 — show whole numbers cleanly, else 1 decimal.
export function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// A target in (0,1] means the metric is a RATE → render this-week/target as %.
export function isRate(target: number): boolean {
  return target > 0 && target <= 1;
}
export function fmtValue(n: number, target: number): string {
  return isRate(target) ? `${Math.round(n * 100)}%` : fmtNum(n);
}

// Reshape one live metric row into the table's KpiRow shape, carrying provenance.
export function toKpiRow(m: ScorecardMetricApi): KpiRow {
  const p = m.provenance;
  const uninstrumented = p?.kind === 'uninstrumented';
  const pres = uninstrumented
    ? { label: '⃠ NOT INSTRUMENTED', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' }
    : statusPresentation(m.status);
  const rate = isRate(m.target);
  const delta = m.delta;
  // No real prior week (a single-point series) ⇒ a week-over-week Δ is undefined.
  const noPriorWeek = (m.sparkline?.length ?? 0) < 2;
  const deltaUnit = rate ? `${Math.round(Math.abs(delta) * 100)}pt` : fmtNum(Math.abs(delta));
  const deltaLabel = uninstrumented
    ? 'n/a'
    : noPriorWeek
      ? '·'
      : delta > 0
        ? `▲${deltaUnit}`
        : delta < 0
          ? `▼${deltaUnit}`
          : '—';
  const deltaColor = noPriorWeek ? 'var(--ink-3)' : delta > 0 ? 'var(--ok)' : delta < 0 ? 'var(--signal)' : 'var(--ink-3)';
  return {
    key: m.key,
    name: m.label,
    note: p?.compute ?? `proj ~${fmtNum(Math.round(m.projection))}`,
    now: uninstrumented ? '——' : fmtValue(m.this_week, m.target),
    last: uninstrumented || noPriorWeek ? '——' : fmtValue(m.last_week, m.target),
    delta: deltaLabel,
    deltaColor,
    target: m.target ? fmtValue(m.target, m.target) : '—',
    status: pres.label,
    statusBg: pres.statusBg,
    statusColor: pres.statusColor,
    prov: p
      ? { system: p.system, locator: p.locator, kind: p.kind, compute: p.compute, lastSync: p.last_sync }
      : { system: '—', locator: '—', kind: 'derived', compute: '—', lastSync: null },
    nowNum: m.this_week,
    targetNum: m.target,
    projection: m.projection,
    sparkline: m.sparkline ?? [m.this_week],
    rate,
    statusKey: uninstrumented ? 'uninstrumented' : m.status,
  };
}

// 9-row canonical scorecard SEED — shown when the backbone is unreachable. Carries
// the same provenance + numeric shape so every tab works offline too.
function seed(
  key: string,
  name: string,
  note: string,
  nowNum: number,
  targetNum: number,
  spark: number[],
  status: string,
  prov: ProvenanceView,
): KpiRow {
  const rate = isRate(targetNum);
  const pres =
    prov.kind === 'uninstrumented'
      ? { label: '⃠ NOT INSTRUMENTED', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' }
      : statusPresentation(status);
  const last = spark.length >= 2 ? spark[spark.length - 2] : 0;
  const d = nowNum - last;
  const dUnit = rate ? `${Math.round(Math.abs(d) * 100)}pt` : fmtNum(Math.abs(d));
  return {
    key,
    name,
    note,
    now: prov.kind === 'uninstrumented' ? '——' : fmtValue(nowNum, targetNum),
    last: prov.kind === 'uninstrumented' ? '——' : fmtValue(last, targetNum),
    delta: prov.kind === 'uninstrumented' ? 'n/a' : d > 0 ? `▲${dUnit}` : d < 0 ? `▼${dUnit}` : '—',
    deltaColor: d > 0 ? 'var(--ok)' : d < 0 ? 'var(--signal)' : 'var(--ink-3)',
    target: targetNum ? fmtValue(targetNum, targetNum) : '—',
    status: pres.label,
    statusBg: pres.statusBg,
    statusColor: pres.statusColor,
    prov,
    nowNum,
    targetNum,
    projection: nowNum + d * 4,
    sparkline: spark,
    rate,
    statusKey: prov.kind === 'uninstrumented' ? 'uninstrumented' : status,
  };
}

export const KPI_ROWS: KpiRow[] = [
  seed('applicants', 'Applicants (total)', 'count of families across all funnel stages', 1284, 0, [1180, 1221, 1252, 1284], 'green', { system: 'Supabase', locator: 'family_record.current_stage (app_form funnel)', kind: 'our_db', compute: 'count of families across all funnel stages', lastSync: null }),
  seed('deposits', 'Deposits vs Fall goal', 'count of recorded deposit payments', 112, 180, [95, 101, 107, 112], 'yellow', { system: 'Stripe', locator: 'payment (Stripe webhook → ledger)', kind: 'our_db', compute: 'count of recorded deposit payments', lastSync: null }),
  seed('conversion_top_channel', 'Conversion · top channel', 'enrolled / total for the top attribution source', 0.42, 0.4, [0.38, 0.39, 0.4, 0.42], 'green', { system: 'Supabase', locator: 'family_record.attribution_source', kind: 'derived', compute: 'enrolled / total for the top attribution source', lastSync: null }),
  seed('engagement_clicked', 'Engagement-tier mix (clicked)', 'share of contacts in the clicked engagement tier', 0.31, 0.35, [0.27, 0.29, 0.3, 0.31], 'green', { system: 'HubSpot', locator: 'community_profile.engagement_signals', kind: 'stood_in', compute: 'share of contacts in the clicked engagement tier', lastSync: null }),
  seed('followup_sla', '24-hr follow-up SLA', 'not-breached / total assigned within 24h', 0.78, 0.9, [0.85, 0.83, 0.82, 0.78], 'red', { system: 'HubSpot', locator: 'core.lead_routing.is_sla_breached + core.contact_log.last_contact_at', kind: 'derived', compute: 'not-breached / total assigned within 24h', lastSync: null }),
  seed('objections', 'Objections logged', 'count of logged objections', 47, 0, [33, 39, 42, 47], 'yellow', { system: 'HubSpot', locator: '— (HubSpot conversations)', kind: 'stood_in', compute: 'count of logged objections', lastSync: null }),
  seed('ambassador_enrollments', 'Ambassador-influenced enroll.', 'enrollments attributed to an ambassador', 18, 30, [12, 15, 16, 18], 'green', { system: 'Grassroots', locator: 'core.ambassador_reconcile.reconcile_ambassadors', kind: 'stood_in', compute: 'enrollments attributed to an ambassador', lastSync: null }),
  seed('handoffs', 'Marketing → onboarding handoffs', 'families crossing into the enroll/onboarding stage', 26, 0, [19, 22, 24, 26], 'green', { system: 'HubSpot', locator: 'family_record.current_stage (enroll/tuition boundary)', kind: 'derived', compute: 'families crossing into the enroll/onboarding stage', lastSync: null }),
  seed('event_to_consult', 'Event-to-consult conversion', 'no event tracking yet — manual entry in v1', 0, 0, [0], 'green', { system: '—', locator: '—', kind: 'uninstrumented', compute: 'no event tracking yet — manual entry in v1', lastSync: null }),
];

// Known measurement holes — the SLA & ops-health "tracking gaps register". These are
// surfaced honestly (the brief: show what's broken, don't fake green).
export const TRACKING_GAPS: { title: string; detail: string; severity: 'broken' | 'untracked' }[] = [
  { title: 'UTM attribution broken', detail: 'Inbound UTMs are dropped before the CRM write — channel attribution is unreliable.', severity: 'broken' },
  { title: 'Event-to-consult uninstrumented', detail: 'No tracking from event attendance to a booked consult; manual entry in v1.', severity: 'untracked' },
  { title: 'SMS send-rate unmeasurable', detail: 'Reconnect/text vendor exposes no send-rate API — volume is not observable.', severity: 'untracked' },
];

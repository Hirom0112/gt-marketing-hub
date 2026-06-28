'use client';

// Dashboard / KPI Tracking (Module 6) — the canonical weekly scorecard.
//   • One row per KPI: this-week / last-week / Δ / target / status.
//   • Status pills colored by token: ON TRACK = --ok, WATCH = --warn,
//     AT RISK = --signal. The Event-to-consult row is honestly
//     NOT INSTRUMENTED (gray) — never faked green (single source of truth).
//   • "Reads all · owns nothing" — identical for every user/role.
//   • PROVENANCE: every number carries where it comes from. The SOURCE cell
//     shows the system + a kind dot; click any row to open a panel with the
//     exact table.column, how it's computed, the trust kind, and last sync —
//     so you can look at a figure and know exactly where it's from.

import { useEffect, useState } from 'react';
import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet } from '@/lib/api';

const MONO = 'JetBrains Mono';

// ---- Provenance: where a number comes from (backend MetricProvenance) --------
// kind ∈ live | our_db | derived | stood_in | uninstrumented — the trust class.
interface ProvenanceView {
  system: string; // "Supabase" | "HubSpot" | "Stripe" | "Grassroots" | "Derived" | "—"
  locator: string; // exact table.column / function, e.g. "family_record.attribution_source"
  kind: string;
  compute: string; // one-line human formula
  lastSync: string | null;
}

// kind → how the SOURCE dot + panel badge reads. One home for the vocabulary.
const KIND: Record<string, { label: string; dot: string; tip: string }> = {
  live: { label: 'LIVE', dot: 'var(--ok)', tip: 'Live API call to a real external service.' },
  our_db: { label: 'OUR DB', dot: 'var(--brand)', tip: "The Hub's own database (Supabase) — our source of record." },
  derived: { label: 'DERIVED', dot: 'var(--warn)', tip: 'Computed in core logic from other sources — never stored twice.' },
  stood_in: { label: 'STOOD-IN', dot: 'var(--ink-3)', tip: 'Source not reachable yet — seeded behind the real interface, labeled.' },
  uninstrumented: { label: 'NOT INSTRUMENTED', dot: 'var(--signal)', tip: 'Genuinely not measured yet — shown as a gap, never faked green.' },
};

function kindOf(kind: string) {
  return KIND[kind] ?? { label: kind.toUpperCase() || '—', dot: 'var(--ink-3)', tip: 'Source kind unknown.' };
}

interface KpiRow {
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
}

// 9-row canonical scorecard SEED — shown when the backbone is unreachable. Each row
// carries the SAME provenance shape the live endpoint returns, so the source panel
// works offline too. (When live, these are replaced by the backend's rows.)
const KPI_ROWS: KpiRow[] = [
  { key: 'applicants', name: 'Applicants (total)', note: 'sum of funnel stages', now: '1,284', last: '1,221', delta: '▲5%', deltaColor: 'var(--ok)', target: '—', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)', prov: { system: 'Supabase', locator: 'family_record.current_stage (app_form funnel)', kind: 'our_db', compute: 'count of families across all funnel stages', lastSync: null } },
  { key: 'deposits', name: 'Deposits vs Fall goal', note: 'goal 180', now: '112', last: '101', delta: '▲11%', deltaColor: 'var(--ok)', target: '180', status: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)', prov: { system: 'Stripe', locator: 'payment (Stripe webhook → ledger)', kind: 'our_db', compute: 'count of recorded deposit payments', lastSync: null } },
  { key: 'conversion_top_channel', name: 'Conversion · top channel', note: 'best attribution source', now: '42%', last: '40%', delta: '▲2pt', deltaColor: 'var(--ok)', target: '40%', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)', prov: { system: 'Supabase', locator: 'family_record.attribution_source', kind: 'derived', compute: 'enrolled / total for the top attribution source', lastSync: null } },
  { key: 'engagement_clicked', name: 'Engagement-tier mix (clicked)', note: 'top conversion predictor', now: '31%', last: '29%', delta: '▲2pt', deltaColor: 'var(--ok)', target: '35%', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)', prov: { system: 'HubSpot', locator: 'community_profile.engagement_signals', kind: 'stood_in', compute: 'share of contacts in the clicked engagement tier', lastSync: null } },
  { key: 'followup_sla', name: '24-hr follow-up SLA', note: 'owner-attributable', now: '78%', last: '82%', delta: '▼4pt', deltaColor: 'var(--signal)', target: '90%', status: 'AT RISK', statusBg: 'var(--signal-soft)', statusColor: 'var(--signal)', prov: { system: 'HubSpot', locator: 'core.lead_routing.is_sla_breached + core.contact_log.last_contact_at', kind: 'derived', compute: 'not-breached / total assigned within 24h', lastSync: null } },
  { key: 'objections', name: 'Objections logged', note: 'BDR + SMS + admissions', now: '47', last: '39', delta: '▲21%', deltaColor: 'var(--warn)', target: '—', status: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)', prov: { system: 'HubSpot', locator: '— (HubSpot conversations)', kind: 'stood_in', compute: 'count of logged objections', lastSync: null } },
  { key: 'ambassador_enrollments', name: 'Ambassador-influenced enroll.', note: 'attribution chain', now: '18', last: '15', delta: '▲3', deltaColor: 'var(--ok)', target: '30', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)', prov: { system: 'Grassroots', locator: 'core.ambassador_reconcile.reconcile_ambassadors', kind: 'stood_in', compute: 'enrollments attributed to an ambassador', lastSync: null } },
  { key: 'handoffs', name: 'Marketing → onboarding handoffs', note: 'deal-stage transitions', now: '26', last: '22', delta: '▲18%', deltaColor: 'var(--ok)', target: '—', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)', prov: { system: 'HubSpot', locator: 'family_record.current_stage (enroll/tuition boundary)', kind: 'derived', compute: 'families crossing into the enroll/onboarding stage', lastSync: null } },
  { key: 'event_to_consult', name: 'Event-to-consult conversion', note: 'uninstrumented · manual v1', now: '——', last: '——', delta: 'n/a', deltaColor: 'var(--ink-3)', target: '—', status: '⃠ NOT INSTRUMENTED', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)', prov: { system: '—', locator: '—', kind: 'uninstrumented', compute: 'no event tracking yet — manual entry in v1', lastSync: null } },
];

const GRID = '2.2fr 1.3fr .9fr .9fr .7fr .8fr 1.3fr';

// ---- Live /scorecard/weekly shape (from backend/app/api/scorecard.py) -------
interface MetricProvenanceApi {
  system: string;
  locator: string;
  kind: string;
  compute: string;
  last_sync: string | null;
}
interface ScorecardMetricApi {
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
interface WeeklyScorecardApi {
  metrics?: ScorecardMetricApi[];
  as_of?: string;
}

// Map the backend green/yellow/red band onto the scorecard's status pills/tokens.
function statusPresentation(status: string): { label: string; statusBg: string; statusColor: string } {
  if (status === 'green') return { label: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' };
  if (status === 'yellow') return { label: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)' };
  if (status === 'red') return { label: 'AT RISK', statusBg: 'var(--signal-soft)', statusColor: 'var(--signal)' };
  return { label: status.toUpperCase() || '—', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' };
}

// A weekly count may arrive as 5.0 — show whole numbers cleanly, else 1 decimal.
function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// A target in (0,1] means the metric is a RATE → render this-week/target as %.
function isRate(target: number): boolean {
  return target > 0 && target <= 1;
}
function fmtValue(n: number, target: number): string {
  return isRate(target) ? `${Math.round(n * 100)}%` : fmtNum(n);
}

// Reshape one live metric row into the table's KpiRow shape, carrying provenance.
function toKpiRow(m: ScorecardMetricApi): KpiRow {
  const p = m.provenance;
  const uninstrumented = p?.kind === 'uninstrumented';
  const pres = uninstrumented
    ? { label: '⃠ NOT INSTRUMENTED', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' }
    : statusPresentation(m.status);
  const rate = isRate(m.target);
  const delta = m.delta;
  // No real prior week (a single-point series) ⇒ a week-over-week Δ is undefined.
  // Show "·" rather than a misleading ▲ off a zero baseline (honest, not faked).
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
  };
}

export function DashboardModule() {
  const def = moduleById('dashboard')!;
  const { session } = useSession();
  const [live, setLive] = useState<WeeklyScorecardApi | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiGet<WeeklyScorecardApi>('/scorecard/weekly', session.role).then((data) => {
      if (active && data && Array.isArray(data.metrics) && data.metrics.length > 0) {
        setLive(data);
      }
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const isLive = live !== null;
  // Render real rows when the live scorecard loaded, else the canonical seed rows.
  const rows: KpiRow[] = isLive ? (live!.metrics ?? []).map(toKpiRow) : KPI_ROWS;
  const selectedRow = rows.find((r) => r.key === selected) ?? null;

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 16 }}>
          {/* Inverted header band */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '11px 16px',
              borderBottom: '2px solid var(--ink)',
              background: 'var(--ink)',
              color: 'var(--paper)',
            }}
          >
            <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 15, letterSpacing: '.3px' }}>
              CANONICAL WEEKLY SCORECARD
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '.4px', opacity: 0.85 }}>
                {isLive && live!.as_of ? `WEEK OF ${live!.as_of}` : 'WEEK OF JUN 22'} · READS ALL · OWNS NOTHING
              </span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 9,
                  fontWeight: 600,
                  letterSpacing: '.4px',
                  padding: '3px 8px',
                  borderRadius: 2,
                  whiteSpace: 'nowrap',
                  color: isLive ? 'var(--ok)' : 'var(--ink-3)',
                  background: isLive ? 'var(--ok-soft)' : 'var(--accent-soft)',
                }}
              >
                {isLive ? '● LIVE' : '○ SAMPLE'}
              </span>
            </div>
          </div>

          {/* Column header row */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: GRID,
              fontFamily: MONO,
              fontSize: 9,
              letterSpacing: '.4px',
              color: 'var(--ink-3)',
              padding: '8px 16px',
              borderBottom: '1px solid var(--line-2)',
              fontWeight: 600,
            }}
          >
            <div>METRIC</div>
            <div>SOURCE</div>
            <div style={{ textAlign: 'right' }}>THIS WK</div>
            <div style={{ textAlign: 'right' }}>LAST WK</div>
            <div style={{ textAlign: 'right' }}>Δ</div>
            <div style={{ textAlign: 'right' }}>TARGET</div>
            <div style={{ textAlign: 'center' }}>STATUS</div>
          </div>

          {/* KPI rows — each row is a button that opens its source panel */}
          {rows.map((k, i) => {
            const kind = kindOf(k.prov.kind);
            const on = k.key === selected;
            return (
              <button
                key={k.key}
                onClick={() => setSelected(on ? null : k.key)}
                aria-pressed={on}
                title="Click to see where this number comes from"
                style={{
                  width: '100%',
                  textAlign: 'left',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'grid',
                  gridTemplateColumns: GRID,
                  alignItems: 'center',
                  padding: '11px 16px',
                  borderBottom: '1px solid var(--line)',
                  borderLeft: `3px solid ${on ? 'var(--brand)' : 'transparent'}`,
                  background: on ? 'var(--accent-soft)' : i % 2 ? 'var(--card-2)' : 'transparent',
                  font: 'inherit',
                  color: 'inherit',
                }}
              >
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{k.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{k.note}</span>
                </div>
                {/* SOURCE — readable system + a kind dot */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <span
                    aria-hidden
                    style={{ width: 7, height: 7, borderRadius: '50%', background: kind.dot, flexShrink: 0 }}
                  />
                  <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                    <span style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                      {k.prov.system}
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 8, letterSpacing: '.3px', color: 'var(--ink-3)' }}>
                      {kind.label}
                    </span>
                  </span>
                </div>
                <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>
                  {k.now}
                </div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>{k.last}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: k.deltaColor }}>
                  {k.delta}
                </div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{k.target}</div>
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <span
                    style={{
                      fontFamily: MONO,
                      fontSize: 9,
                      fontWeight: 600,
                      letterSpacing: '.4px',
                      padding: '3px 8px',
                      borderRadius: 2,
                      background: k.statusBg,
                      color: k.statusColor,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {k.status}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* SOURCE PANEL — opens when a row is clicked: exactly where the number is from */}
        {selectedRow && <SourcePanel row={selectedRow} live={isLive} onClose={() => setSelected(null)} />}

        {/* Footnote / honesty band */}
        <div style={{ display: 'flex', gap: 14, fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', flexWrap: 'wrap', marginTop: 14 }}>
          <span>◆ Click any row to see its single source of record.</span>
          <span style={{ color: 'var(--signal)' }}>⃠ Hatched = uninstrumented / broken — never shown as on-track.</span>
          <span>Referenced live in the Monday meeting (agenda item 2 · the Marketing Lead).</span>
        </div>
      </section>
    </>
  );
}

// The source panel — the "oh, I know where this is coming from" surface. Shows the
// system, the exact table.column / function, the trust kind, how it's computed, and
// last sync, for the clicked metric.
function SourcePanel({ row, live, onClose }: { row: KpiRow; live: boolean; onClose: () => void }) {
  const kind = kindOf(row.prov.kind);
  const trust = live
    ? row.prov.kind === 'uninstrumented'
      ? { label: 'NOT INSTRUMENTED', color: 'var(--ink-2)', bg: 'var(--accent-soft)' }
      : row.prov.kind === 'stood_in'
        ? { label: 'STOOD-IN', color: 'var(--ink-2)', bg: 'var(--accent-soft)' }
        : { label: 'LIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' }
    : { label: 'SAMPLE', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };

  return (
    <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 4 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 16px',
          borderBottom: '1px solid var(--line-2)',
          background: 'var(--card-2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: kind.dot }} />
          <span style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 13.5, color: 'var(--ink)' }}>
            {row.name}
          </span>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '2px 7px', borderRadius: 2, background: trust.bg, color: trust.color }}>
            {trust.label}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close source panel"
          style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 13, color: 'var(--ink-3)', padding: 2 }}
        >
          ✕
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: 10, columnGap: 14, padding: '14px 16px' }}>
        <Field label="SYSTEM" value={row.prov.system} />
        <Field label="LOCATOR" value={row.prov.locator} mono />
        <Field label="KIND" value={`${kind.label} — ${kind.tip}`} />
        <Field label="HOW IT'S COMPUTED" value={row.prov.compute} />
        <Field label="LAST SYNC" value={row.prov.lastSync ?? (live ? 'point-in-time (no weekly history yet)' : '—')} mono />
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, paddingTop: 2 }}>
        {label}
      </div>
      <div style={{ fontFamily: mono ? MONO : 'Geist', fontSize: mono ? 11 : 12.5, color: 'var(--ink)', lineHeight: 1.45 }}>
        {value}
      </div>
    </>
  );
}

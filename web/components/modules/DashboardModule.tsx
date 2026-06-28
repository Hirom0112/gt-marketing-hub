'use client';

// Dashboard / KPI Tracking (Module 6) — the canonical weekly scorecard.
//   • One row per KPI: this-week / last-week / Δ / target / status.
//   • Status pills colored by token: ON TRACK = --ok, WATCH = --warn,
//     AT RISK = --signal. The Event-to-consult row is honestly
//     NOT INSTRUMENTED (gray) — never faked green (single source of truth).
//   • "Reads all · owns nothing" — identical for every user/role.

import { useEffect, useState } from 'react';
import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet } from '@/lib/api';

const MONO = 'JetBrains Mono';

interface KpiRow {
  name: string;
  note: string;
  source: string;
  now: string;
  last: string;
  delta: string;
  deltaColor: string;
  target: string;
  status: string;
  statusBg: string;
  statusColor: string;
}

// 9-row canonical scorecard. Colors are CSS-var token strings.
const KPI_ROWS: KpiRow[] = [
  { name: 'Applicants (total)', note: 'fall push · cumulative', source: 'SUPA', now: '1,284', last: '1,221', delta: '▲5%', deltaColor: 'var(--ok)', target: '—', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' },
  { name: 'Deposits vs Fall goal', note: 'goal 180', source: 'SUPA', now: '112', last: '101', delta: '▲11%', deltaColor: 'var(--ok)', target: '180', status: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)' },
  { name: 'Conversion · top channel (X)', note: 'pre-sold engine', source: 'SUPA', now: '42%', last: '40%', delta: '▲2pt', deltaColor: 'var(--ok)', target: '40%', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' },
  { name: 'Engagement-tier mix (clicked)', note: 'top conversion predictor', source: 'HUBS', now: '31%', last: '29%', delta: '▲2pt', deltaColor: 'var(--ok)', target: '35%', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' },
  { name: '24-hr follow-up SLA', note: 'owner-attributable', source: 'HUBS', now: '78%', last: '82%', delta: '▼4pt', deltaColor: 'var(--signal)', target: '90%', status: 'AT RISK', statusBg: 'var(--signal-soft)', statusColor: 'var(--signal)' },
  { name: 'Objections logged', note: 'BDR + SMS + admissions', source: 'HUBS+M', now: '47', last: '39', delta: '▲21%', deltaColor: 'var(--warn)', target: '—', status: 'WATCH', statusBg: 'var(--warn-soft)', statusColor: 'var(--warn)' },
  { name: 'Ambassador-influenced enroll.', note: 'attribution chain', source: 'GRASS', now: '18', last: '15', delta: '▲3', deltaColor: 'var(--ok)', target: '30', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' },
  { name: 'Marketing → onboarding handoffs', note: 'deal-stage transitions', source: 'HUBS', now: '26', last: '22', delta: '▲18%', deltaColor: 'var(--ok)', target: '—', status: 'ON TRACK', statusBg: 'var(--ok-soft)', statusColor: 'var(--ok)' },
  { name: 'Event-to-consult conversion', note: 'uninstrumented · manual v1', source: '—', now: '——', last: '——', delta: 'n/a', deltaColor: 'var(--ink-3)', target: '—', status: '⃠ NOT INSTRUMENTED', statusBg: 'var(--accent-soft)', statusColor: 'var(--ink-2)' },
];

const GRID = '2.2fr .9fr .9fr .9fr .7fr .9fr 1.4fr';

// ---- Live /scorecard/weekly shape (from backend/app/api/scorecard.py) -------
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

// Reshape one live metric row into the table's KpiRow shape.
function toKpiRow(m: ScorecardMetricApi): KpiRow {
  const pres = statusPresentation(m.status);
  const delta = m.delta;
  const deltaLabel = delta > 0 ? `▲${fmtNum(delta)}` : delta < 0 ? `▼${fmtNum(Math.abs(delta))}` : '—';
  const deltaColor = delta > 0 ? 'var(--ok)' : delta < 0 ? 'var(--signal)' : 'var(--ink-3)';
  return {
    name: m.label,
    note: `audit-spine count · proj ~${fmtNum(Math.round(m.projection))}`,
    source: 'SPINE',
    now: fmtNum(m.this_week),
    last: fmtNum(m.last_week),
    delta: deltaLabel,
    deltaColor,
    target: m.target ? fmtNum(m.target) : '—',
    status: pres.label,
    statusBg: pres.statusBg,
    statusColor: pres.statusColor,
  };
}

export function DashboardModule() {
  const def = moduleById('dashboard')!;
  const { session } = useSession();
  const [live, setLive] = useState<WeeklyScorecardApi | null>(null);

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

          {/* KPI rows */}
          {rows.map((k, i) => (
            <div
              key={k.name}
              style={{
                display: 'grid',
                gridTemplateColumns: GRID,
                alignItems: 'center',
                padding: '11px 16px',
                borderBottom: '1px solid var(--line)',
                background: i % 2 ? 'var(--card-2)' : 'transparent',
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{k.name}</span>
                <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{k.note}</span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>
                <span style={{ borderBottom: '1px dotted var(--ink-3)' }}>{k.source}</span>
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
            </div>
          ))}
        </div>

        {/* Footnote / honesty band */}
        <div style={{ display: 'flex', gap: 14, fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}>
          <span>◆ Every figure cites exactly one source of record.</span>
          <span style={{ color: 'var(--signal)' }}>⃠ Hatched = uninstrumented / broken — never shown as on-track.</span>
          <span>Referenced live in the Monday meeting (agenda item 2 · the Marketing Lead).</span>
        </div>
      </section>
    </>
  );
}

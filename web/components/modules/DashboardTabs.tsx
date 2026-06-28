'use client';

// Module 6 sub-view tabs (Trends / SLA & ops health / Goal pacing / HubSpot mirror).
// Each reads the SAME KpiRow[] the Scorecard renders — "reads all, owns nothing" —
// and shows where its numbers come from (provenance carried on every row).

import { useState } from 'react';
import { kindOf, fmtValue, fmtNum, type KpiRow, TRACKING_GAPS } from '@/lib/scorecard-view';

const MONO = 'JetBrains Mono';

// ---- a tiny dependency-free SVG line chart (normalized per series) -----------
function LineChart({
  series,
  height = 160,
}: {
  series: { label: string; color: string; points: number[] }[];
  height?: number;
}) {
  const width = 720;
  const pad = 8;
  const maxLen = Math.max(...series.map((s) => s.points.length), 2);
  const path = (points: number[]) => {
    if (points.length === 0) return '';
    const lo = Math.min(...points);
    const hi = Math.max(...points);
    const span = hi - lo || 1; // flat series ⇒ centre the line
    const stepX = (width - pad * 2) / (maxLen - 1);
    return points
      .map((v, i) => {
        const x = pad + i * stepX;
        const y = pad + (height - pad * 2) * (1 - (v - lo) / span);
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  };
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto' }} role="img" aria-label="trend line chart">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="var(--line-2)" strokeWidth={1} />
      {series.map((s) => (
        <g key={s.label}>
          <path d={path(s.points)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {s.points.length === 1 && (
            <circle cx={pad} cy={height / 2} r={3.5} fill={s.color} />
          )}
        </g>
      ))}
    </svg>
  );
}

const SECTION: React.CSSProperties = { border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 16 };
const HEAD: React.CSSProperties = {
  padding: '11px 16px',
  borderBottom: '2px solid var(--ink)',
  background: 'var(--ink)',
  color: 'var(--paper)',
  fontFamily: 'Fraunces',
  fontWeight: 700,
  fontSize: 14,
  letterSpacing: '.3px',
};

function SourceTag({ row }: { row: KpiRow }) {
  const k = kindOf(row.prov.kind);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
      <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: k.dot }} />
      {row.prov.system} · {k.label}
    </span>
  );
}

// ============================ TRENDS ========================================
export function TrendsTab({ rows }: { rows: KpiRow[] }) {
  const charted = rows.filter((r) => r.statusKey !== 'uninstrumented');
  const [windowWeeks, setWindowWeeks] = useState(12);
  const [a, setA] = useState(charted[0]?.key ?? '');
  const [b, setB] = useState('');
  const rowA = charted.find((r) => r.key === a);
  const rowB = charted.find((r) => r.key === b);
  const slice = (pts: number[]) => pts.slice(-windowWeeks);
  const series = [
    rowA && { label: rowA.name, color: 'var(--brand)', points: slice(rowA.sparkline) },
    rowB && { label: rowB.name, color: 'var(--signal)', points: slice(rowB.sparkline) },
  ].filter(Boolean) as { label: string; color: string; points: number[] }[];
  const thin = series.some((s) => s.points.length < 2);

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={SECTION}>
        <div style={{ ...HEAD, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>KPI TRENDS</span>
          <span style={{ display: 'flex', gap: 4 }}>
            {[4, 8, 12].map((w) => (
              <button
                key={w}
                onClick={() => setWindowWeeks(w)}
                style={{
                  cursor: 'pointer',
                  fontFamily: MONO,
                  fontSize: 9.5,
                  fontWeight: 600,
                  padding: '3px 9px',
                  borderRadius: 2,
                  border: '1px solid var(--paper)',
                  background: windowWeeks === w ? 'var(--paper)' : 'transparent',
                  color: windowWeeks === w ? 'var(--ink)' : 'var(--paper)',
                }}
              >
                {w}W
              </button>
            ))}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 16, padding: '12px 16px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
          <Picker label="METRIC" color="var(--brand)" value={a} onChange={setA} rows={charted} />
          <Picker label="COMPARE" color="var(--signal)" value={b} onChange={setB} rows={charted} allowNone />
        </div>
        <div style={{ padding: '16px' }}>
          <LineChart series={series} />
          <div style={{ display: 'flex', gap: 18, marginTop: 10, flexWrap: 'wrap' }}>
            {series.map((s) => (
              <span key={s.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--ink-2)' }}>
                <span style={{ width: 14, height: 2, background: s.color, display: 'inline-block' }} /> {s.label}
              </span>
            ))}
          </div>
          {thin && (
            <p style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 12 }}>
              ◆ Limited history — synthetic data is point-in-time, so the series is a single real sample
              (no fabricated multi-week trend). Weekly history accrues as the backbone runs.
            </p>
          )}
        </div>
      </div>
      <p style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)' }}>Event annotations deferred to v2 (per spec 6b).</p>
    </section>
  );
}

function Picker({
  label,
  color,
  value,
  onChange,
  rows,
  allowNone,
}: {
  label: string;
  color: string;
  value: string;
  onChange: (v: string) => void;
  rows: KpiRow[];
  allowNone?: boolean;
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', fontWeight: 600 }}>
        <span style={{ width: 14, height: 2, background: color, display: 'inline-block' }} /> {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ fontFamily: 'Geist', fontSize: 12, padding: '4px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 }}
      >
        {allowNone && <option value="">— none —</option>}
        {rows.map((r) => (
          <option key={r.key} value={r.key}>
            {r.name}
          </option>
        ))}
      </select>
    </label>
  );
}

// ====================== SLA & OPS HEALTH ====================================
export function SlaOpsTab({ rows }: { rows: KpiRow[] }) {
  const sla = rows.find((r) => r.key === 'followup_sla');
  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={SECTION}>
        <div style={HEAD}>24-HR FOLLOW-UP SLA</div>
        <div style={{ display: 'flex', gap: 28, alignItems: 'center', padding: '20px 16px', borderBottom: '1px solid var(--line)' }}>
          <div>
            <div style={{ fontFamily: 'Fraunces', fontSize: 44, fontWeight: 700, color: sla?.statusKey === 'red' ? 'var(--signal)' : 'var(--ink)', lineHeight: 1 }}>
              {sla ? sla.now : '—'}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 6 }}>
              compliance · target {sla?.target ?? '90%'}
            </div>
            {sla && (
              <div style={{ marginTop: 6 }}>
                <SourceTag row={sla} />
              </div>
            )}
          </div>
          <div style={{ flex: 1 }}>
            {sla && <LineChart series={[{ label: '30-day SLA', color: 'var(--signal)', points: sla.sparkline }]} height={120} />}
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>
              owner-attributable · 30-day window
            </div>
          </div>
        </div>
        <div style={{ padding: '10px 16px', fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)' }}>
          ◆ {sla?.prov.compute ?? '—'} — source {sla?.prov.system} ({sla?.prov.locator}).
        </div>
      </div>

      {/* Tracking-gaps register — the known measurement holes, surfaced honestly */}
      <div style={SECTION}>
        <div style={HEAD}>TRACKING GAPS REGISTER</div>
        {TRACKING_GAPS.map((g) => (
          <div key={g.title} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
            <span
              style={{
                fontFamily: MONO,
                fontSize: 8.5,
                fontWeight: 600,
                letterSpacing: '.4px',
                padding: '3px 8px',
                borderRadius: 2,
                whiteSpace: 'nowrap',
                marginTop: 1,
                background: g.severity === 'broken' ? 'var(--signal-soft)' : 'var(--accent-soft)',
                color: g.severity === 'broken' ? 'var(--signal)' : 'var(--ink-2)',
              }}
            >
              {g.severity === 'broken' ? '⃠ BROKEN' : '○ UNTRACKED'}
            </span>
            <div>
              <div style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{g.title}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{g.detail}</div>
            </div>
          </div>
        ))}
        <div style={{ padding: '10px 16px', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          Surfaced, not hidden — a known hole is never shown as on-track.
        </div>
      </div>
    </section>
  );
}

// ========================= GOAL PACING ======================================
export function GoalPacingTab({ rows, goalDate }: { rows: KpiRow[]; goalDate: string | null }) {
  const paced = rows.filter((r) => r.statusKey !== 'uninstrumented' && r.targetNum > 0);
  const by = goalDate ? new Date(goalDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'the goal date';
  const GRID = '2fr 1fr 1fr 1.4fr 1.1fr';
  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={SECTION}>
        <div style={{ ...HEAD, display: 'flex', justifyContent: 'space-between' }}>
          <span>GOAL PACING</span>
          <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 400, opacity: 0.85 }}>HORIZON · {by}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: GRID, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
          <div>METRIC</div>
          <div style={{ textAlign: 'right' }}>NOW</div>
          <div style={{ textAlign: 'right' }}>TARGET</div>
          <div style={{ textAlign: 'right' }}>AT THIS PACE →</div>
          <div style={{ textAlign: 'center' }}>PACE</div>
        </div>
        {paced.map((r, i) => {
          const onPace = r.projection >= r.targetNum;
          return (
            <div key={r.key} style={{ display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '11px 16px', borderBottom: '1px solid var(--line)', background: i % 2 ? 'var(--card-2)' : 'transparent' }}>
              <div>
                <div style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{r.name}</div>
                <SourceTag row={r} />
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, color: 'var(--ink-2)' }}>{r.now}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, color: 'var(--ink-2)' }}>{r.target}</div>
              <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 15, fontWeight: 600, color: onPace ? 'var(--ok)' : 'var(--signal)' }}>
                {fmtValue(r.projection, r.targetNum)} <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>by {by}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: onPace ? 'var(--ok-soft)' : 'var(--signal-soft)', color: onPace ? 'var(--ok)' : 'var(--signal)' }}>
                  {onPace ? 'ON PACE' : 'BEHIND'}
                </span>
              </div>
            </div>
          );
        })}
        <div style={{ padding: '10px 16px', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          Projection = current + recent weekly run-rate × weeks to horizon (deterministic, from the backbone).
        </div>
      </div>
    </section>
  );
}

// ====================== HUBSPOT MIRROR ======================================
export function HubSpotMirrorTab({ rows }: { rows: KpiRow[] }) {
  const hs = rows.filter((r) => r.prov.system === 'HubSpot');
  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={SECTION}>
        <div style={{ ...HEAD, display: 'flex', justifyContent: 'space-between' }}>
          <span>HUBSPOT DASHBOARD MIRROR</span>
          <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 400, opacity: 0.85 }}>RECONSTRUCTED FROM CRM READS</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 1, background: 'var(--line)' }}>
          {hs.map((r) => (
            <div key={r.key} style={{ background: 'var(--card)', padding: '14px 16px' }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginBottom: 6 }}>{r.name}</div>
              <div style={{ fontFamily: 'Fraunces', fontSize: 26, fontWeight: 700, color: 'var(--ink)' }}>{r.now}</div>
              <div style={{ marginTop: 6 }}>
                <SourceTag row={r} />
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding: '12px 16px', fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', borderTop: '1px solid var(--line)' }}>
          ◆ HubSpot's API exposes no saved-report endpoint, so these widgets are <b>reconstructed</b> from
          live CRM reads inside the Hub — leadership doesn&apos;t need to log into HubSpot separately. Each
          tile cites the HubSpot field it reads.
        </div>
      </div>
    </section>
  );
}

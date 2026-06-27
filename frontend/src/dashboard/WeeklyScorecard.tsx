import { useEffect, useState } from 'react';
import { apiFetch } from '../config';
import { Card, Chip } from '../ui';
import type { Tone } from '../ui';

// The canonical weekly KPI scorecard (TODO_v2 §B5). Reads the deterministic
// `GET /scorecard/weekly` rollup and renders ONE row per metric: this-week /
// last-week / a signed week-over-week delta / a 4-week sparkline / the target /
// a green/yellow/red status pill / the pace projection. The scorecard is
// identical for everyone — no role gate (any authenticated seat). Read-only
// (INV-2): a GET, nothing written, nothing logged.
//
// This is the real component behind the B3 `kpi_scorecard` Home widget id (it
// renders a `compact` variant there) and is safe to mount standalone too.

// --- the backend contract (mirrors WeeklyScorecard/ScorecardMetric) ---------
interface ScorecardMetric {
  key: string;
  label: string;
  this_week: number;
  last_week: number;
  delta: number; // == this_week − last_week
  sparkline: number[]; // up to 4 trailing weekly values
  target: number;
  status: 'green' | 'yellow' | 'red';
  projection: number; // deterministic pace estimate to the goal date
}

interface ScorecardResponse {
  metrics: ScorecardMetric[];
  as_of: string;
}

// status band → the app's existing semantic tones (no invented palette):
//   green → flow (healthy) · yellow → gate (watch) · red → signal (off-baseline).
const STATUS_TONE: Record<string, Tone> = {
  green: 'flow',
  yellow: 'gate',
  red: 'signal',
};

type LoadState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; data: ScorecardResponse };

// Compact whole-or-1dp number — weekly counts are integers but typed float, so a
// genuine 3.5 still reads honestly while 5.0 renders as "5".
function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return '–';
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// A lightweight inline-SVG sparkline of the trailing weekly values — NO charting
// dependency. A flat (single-value or all-equal) series draws a centered baseline
// rather than dividing by a zero span.
function Sparkline({ values }: { values: number[] }): JSX.Element | null {
  if (values.length === 0) return null;
  const w = 60;
  const h = 18;
  const pad = 2;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const innerW = w - pad * 2;
  const innerH = h - pad * 2;
  const points = values
    .map((v, i) => {
      const x =
        values.length > 1 ? pad + (i / (values.length - 1)) * innerW : w / 2;
      const y = pad + innerH - ((v - min) / span) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg
      data-testid="sparkline"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="4-week trend"
      style={{ display: 'block', overflow: 'visible' }}
    >
      <polyline
        data-testid="sparkline-line"
        points={points}
        fill="none"
        stroke="var(--flow)"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// The signed week-over-week delta. Up = good (flow/green), down = bad
// (signal/red), flat = quiet. The sign is always explicit.
function DeltaCell({ delta }: { delta: number }): JSX.Element {
  const arrow = delta > 0 ? '▲' : delta < 0 ? '▼' : '–';
  const color =
    delta > 0
      ? 'var(--flow)'
      : delta < 0
        ? 'var(--signal)'
        : 'var(--muted)';
  const signed = delta > 0 ? `+${fmtNum(delta)}` : fmtNum(delta); // negatives keep their −
  return (
    <span
      data-testid="delta"
      className="mono"
      style={{ color, whiteSpace: 'nowrap' }}
    >
      {arrow} {signed}
    </span>
  );
}

export interface WeeklyScorecardProps {
  /** Trim columns for the Home tile (drops last-week / target / projection). */
  compact?: boolean;
}

export default function WeeklyScorecard({
  compact = false,
}: WeeklyScorecardProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    apiFetch('/scorecard/weekly')
      .then((res) => {
        if (!res.ok) throw new Error(`scorecard failed: ${res.status}`);
        return res.json() as Promise<ScorecardResponse>;
      })
      .then((data) => {
        if (!cancelled)
          setState({
            status: 'ready',
            data: { metrics: data.metrics ?? [], as_of: data.as_of ?? '' },
          });
      })
      .catch(() => {
        // Fail-safe: a quiet notice, never a crash (brief: trustworthy when slow).
        if (!cancelled) setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === 'loading') {
    return (
      <div data-testid="weekly-scorecard" style={{ height: '100%' }}>
        <Card style={{ height: '100%' }}>
          <p className="lab" style={{ color: 'var(--muted)', margin: 0 }}>
            Loading scorecard…
          </p>
        </Card>
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div data-testid="weekly-scorecard" style={{ height: '100%' }}>
        <Card style={{ height: '100%' }}>
          <p className="lab" style={{ color: 'var(--muted)', margin: 0 }}>
            Scorecard unavailable
          </p>
        </Card>
      </div>
    );
  }

  const { metrics, as_of } = state.data;
  const th: React.CSSProperties = {
    textAlign: 'left',
    padding: '4px 10px',
    borderBottom: '1px solid var(--line)',
    color: 'var(--muted)',
    whiteSpace: 'nowrap',
  };
  const td: React.CSSProperties = {
    padding: '6px 10px',
    borderBottom: '1px solid var(--line)',
    verticalAlign: 'middle',
  };
  const numTd: React.CSSProperties = { ...td, textAlign: 'right' };

  return (
    <div data-testid="weekly-scorecard" style={{ height: '100%' }}>
    <Card
      style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 'var(--s-2)' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--s-2)' }}>
        <span style={{ fontWeight: 600 }}>KPI Scorecard</span>
        <span className="lab" data-testid="scorecard-asof" style={{ color: 'var(--muted)' }}>
          {as_of ? `week of ${as_of}` : 'this week'}
        </span>
      </div>

      {metrics.length === 0 ? (
        <p className="lab" style={{ color: 'var(--muted)', margin: 0 }}>
          No metrics yet
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table
            className="mono"
            style={{ borderCollapse: 'collapse', width: '100%', fontSize: 'var(--fs-chip)' }}
          >
            <thead>
              <tr>
                <th style={th}>Metric</th>
                <th style={{ ...th, textAlign: 'right' }}>This wk</th>
                {!compact && <th style={{ ...th, textAlign: 'right' }}>Last wk</th>}
                <th style={{ ...th, textAlign: 'right' }}>Δ</th>
                <th style={th}>Trend</th>
                {!compact && <th style={{ ...th, textAlign: 'right' }}>Target</th>}
                <th style={th}>Status</th>
                {!compact && <th style={th}>Pace</th>}
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.key} data-testid="scorecard-row">
                  <td style={td}>{m.label}</td>
                  <td style={numTd}>{fmtNum(m.this_week)}</td>
                  {!compact && <td style={numTd}>{fmtNum(m.last_week)}</td>}
                  <td style={numTd}>
                    <DeltaCell delta={m.delta} />
                  </td>
                  <td style={td}>
                    <Sparkline values={m.sparkline} />
                  </td>
                  {!compact && <td style={numTd}>{fmtNum(m.target)}</td>}
                  <td style={td}>
                    <Chip tone={STATUS_TONE[m.status] ?? 'neutral'}>
                      {m.status.toUpperCase()}
                    </Chip>
                  </td>
                  {!compact && (
                    <td style={td} data-testid="projection">
                      at this pace → {fmtNum(m.projection)}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
    </div>
  );
}

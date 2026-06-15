import type { ReactNode } from 'react';
import { type Tone, toneVars } from './tokens';
import { Card } from './Card';

// A single metric: mono micro-label, a big mono value, and an optional sub-note.
// `tone` tints the VALUE (e.g. signal for an off-baseline / needs-attention KPI);
// the default neutral tone renders ink. `Stat` is the bare metric; `KpiCard`
// wraps it in a Card for the dashboard KPI strips.
export interface StatProps {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: Tone;
  // Optional funnel-share: renders a 4px mini-bar (track --line-2, fill --flow)
  // at `barPct`% width — the leadership KPI's share-of-funnel rail. Clamped 0–100.
  barPct?: number;
}

export function Stat({
  label,
  value,
  note,
  tone = 'neutral',
  barPct,
}: StatProps): JSX.Element {
  const color = tone === 'neutral' ? 'var(--ink)' : toneVars(tone).solid;
  const clampedPct =
    barPct === undefined ? undefined : Math.max(0, Math.min(100, barPct));
  return (
    <div>
      <div className="lab">{label}</div>
      <div
        className="mono"
        style={{
          fontSize: 'var(--fs-stat)',
          fontWeight: 600,
          lineHeight: 1.1,
          marginTop: 'var(--s-1)',
          color,
        }}
      >
        {value}
      </div>
      {clampedPct !== undefined ? (
        <div
          data-testid="stat-bar"
          style={{
            height: 4,
            borderRadius: 'var(--r-pill)',
            background: 'var(--line-2)',
            marginTop: 'var(--s-3)',
            overflow: 'hidden',
          }}
        >
          <div
            data-testid="stat-bar-fill"
            style={{
              height: '100%',
              width: `${clampedPct}%`,
              background: 'var(--flow)',
            }}
          />
        </div>
      ) : null}
      {note ? (
        <div
          style={{
            fontSize: '11.5px',
            color: 'var(--muted)',
            marginTop: 2,
          }}
        >
          {note}
        </div>
      ) : null}
    </div>
  );
}

export type KpiCardProps = StatProps;

export function KpiCard(props: KpiCardProps): JSX.Element {
  return (
    <Card>
      <Stat {...props} />
    </Card>
  );
}

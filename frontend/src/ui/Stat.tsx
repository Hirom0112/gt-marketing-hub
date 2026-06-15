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
}

export function Stat({
  label,
  value,
  note,
  tone = 'neutral',
}: StatProps): JSX.Element {
  const color = tone === 'neutral' ? 'var(--ink)' : toneVars(tone).solid;
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

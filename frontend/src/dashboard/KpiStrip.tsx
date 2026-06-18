import { Stat } from '../ui';
import type { Tone } from '../ui';

// One metric in the strip. The shells decide WHICH metrics (admin: 3 —
// ACTIVE STALLS/OVERDUE/$ AT RISK; agent: 4 — BOOKED/CONTACTED/OVERDUE/ACTIVE)
// and pass them in. KpiStrip knows nothing about the data, only how to lay them
// out full-width across the top of the dashboard.
export interface KpiMetric {
  label: string;
  value: React.ReactNode;
  tone?: Tone;
}

export interface KpiStripProps {
  metrics: KpiMetric[];
}

export function KpiStrip({ metrics }: KpiStripProps): JSX.Element {
  return (
    <>
      {metrics.map((m) => (
        <div className="admin-kpi-cell" data-testid="kpi-metric" key={m.label}>
          <Stat label={m.label} value={m.value} tone={m.tone} />
        </div>
      ))}
    </>
  );
}

export default KpiStrip;

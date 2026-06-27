import { useEffect, useState } from 'react';
import { FlaskConical } from 'lucide-react';
import { apiFetch } from '../config';
import { Card, PlaceholderBadge } from '../ui';
import { KpiStrip } from '../dashboard/KpiStrip';
import SituationBar from '../enrollment/SituationBar';
import {
  summarizeRecovery,
  type RecoverableRow,
} from '../enrollment/recency';
import { fmtUSD } from '../enrollment/format';

// Small self-fetching widgets for the composable Home (TODO_v2 §B3 / U4). Each of
// the eight STARTER-pack ids resolves to a real cockpit surface; the three that
// have no drop-in component (kpi_strip, work_queue, crm_status) get these thin,
// self-contained readers here. The remaining 28 catalog ids render the honest
// `WidgetPlaceholder` tile below — a labeled "surface coming soon" frame, NOT a
// fake dashboard (the point of B3 is the composable FRAME + id registry, with
// real widgets where they exist and honest placeholders otherwise).

// ---------------------------------------------------------------------------
// Shared /work-queue read — the same DB read AdminDashboard's KPI strip derives
// from. Fail-safe: a failed read leaves an empty set, so the widget degrades to
// zeros rather than crashing the Home it sits on (brief: trustworthy when slow).
function useWorkQueueRows(): readonly RecoverableRow[] {
  const [rows, setRows] = useState<readonly RecoverableRow[]>([]);
  useEffect(() => {
    let cancelled = false;
    apiFetch('/work-queue')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<RecoverableRow[]>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        /* keep last-known rows; the widget stays usable */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return rows;
}

/** kpi_strip → the 3-metric enrollment KPI strip, derived client-side from the
 *  ONE /work-queue read (reuses the AdminDashboard derivation; never hardcoded). */
export function KpiStripWidget(): JSX.Element {
  const sum = summarizeRecovery(useWorkQueueRows());
  return (
    <KpiStrip
      metrics={[
        { label: 'ACTIVE STALLS', value: sum.stalled },
        {
          label: 'OVERDUE',
          value: sum.overdue,
          tone: sum.overdue > 0 ? ('signal' as const) : undefined,
        },
        { label: '$ AT RISK', value: fmtUSD(sum.recoverableValue) },
      ]}
    />
  );
}

/** work_queue → the team recovery situation headline, reading the same
 *  /work-queue rows (reuses the shared SituationBar in its team variant). */
export function WorkQueueWidget(): JSX.Element {
  return <SituationBar rows={useWorkQueueRows()} variant="team" />;
}

// GET /crm/status (subset of app/api/crm_status.py — see DataConfidenceBanner for
// the full shape). crm_status reads the live CRM mode + sync parity.
interface CrmStatusShape {
  effective_mode: string;
  kill_switch: boolean;
  parity_overall: number;
}

/** crm_status → the live CRM connector mode + sync-parity readout. Fail-safe:
 *  a failed status read renders a quiet "unavailable" line, never a crash. */
export function CrmStatusWidget(): JSX.Element {
  const [status, setStatus] = useState<CrmStatusShape | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    apiFetch('/crm/status')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<CrmStatusShape>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <p className="lab" style={{ color: 'var(--muted)', margin: 0 }}>
        CRM status unavailable
      </p>
    );
  }
  if (status === null) {
    return (
      <p className="lab" style={{ color: 'var(--muted)', margin: 0 }}>
        Loading CRM status…
      </p>
    );
  }
  const parityPct = Math.round(status.parity_overall * 1000) / 10;
  return (
    <div style={{ display: 'grid', gap: 'var(--s-2)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span className="lab" style={{ color: 'var(--muted)' }}>
          MODE
        </span>
        <span className="mono" data-testid="crm-status-mode">
          {status.effective_mode}
          {status.kill_switch ? ' (kill-switch)' : ''}
        </span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span className="lab" style={{ color: 'var(--muted)' }}>
          SYNC PARITY
        </span>
        <span className="mono" data-testid="crm-status-parity">
          {parityPct}%
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
/** The honest placeholder tile for catalog ids without a dedicated component
 *  yet. Carries the INV-9 PlaceholderBadge so the operator knows it is not a
 *  live surface — a labeled frame, never a faked dashboard. */
export function WidgetPlaceholder({ label }: { label: string }): JSX.Element {
  return (
    <Card
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--s-2)',
        textAlign: 'center',
      }}
    >
      <FlaskConical size={20} aria-hidden style={{ color: 'var(--muted)' }} />
      <span style={{ fontWeight: 600 }}>{label}</span>
      <PlaceholderBadge label="SURFACE COMING SOON" />
    </Card>
  );
}

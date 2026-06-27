import { useEffect, useState } from 'react';
import { apiFetch } from '../config';
import { Card, Stat } from '../ui';
import { fmtPct } from '../enrollment/format';

// AgentKpiTab (R6 / D-14) — the sales agent's personal performance dashboard
// (sales-agent brief Tab 5). A Day/Week/Month/All-time window control scopes a
// single read: GET /enrollment/agent-kpis?window=… (owner-scoped by the verified
// agent_id in the bearer token that apiFetch attaches, so the agent only
// ever sees their own numbers — assigned-only, INV-1). The seven KPIs render in
// a clean scannable grid (Stat primitive inside .dash-kpi-grid); no charts per
// the brief. Read-only GET (INV-2). Changing the window refetches.

type Window = 'day' | 'week' | 'month' | 'all';

const WINDOWS: ReadonlyArray<{ key: Window; label: string }> = [
  { key: 'day', label: 'Day' },
  { key: 'week', label: 'Week' },
  { key: 'month', label: 'Month' },
  { key: 'all', label: 'All time' },
];

// The contract being built in parallel by the backend agent (D-14). Local to
// this component — NOT added to dashboard/types.ts.
interface AgentKpis {
  leads_assigned: number;
  contacts_made: number;
  follow_ups_completed: number;
  appointments_booked: number;
  applications_started: number;
  applications_completed: number;
  conversion_rate: number; // [0,1] fraction (funded ÷ assigned)
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: AgentKpis };

interface AgentKpiTabProps {
  // Optional explicit agent scope. When omitted the endpoint scopes to the
  // signed-in agent via the principal header (the common case).
  agentId?: string;
}

const METRICS: ReadonlyArray<{
  key: keyof AgentKpis;
  label: string;
  pct?: boolean;
}> = [
  { key: 'leads_assigned', label: 'Leads Assigned' },
  { key: 'contacts_made', label: 'Contacts Made' },
  { key: 'follow_ups_completed', label: 'Follow-Ups Completed' },
  { key: 'appointments_booked', label: 'Appointments Booked' },
  { key: 'applications_started', label: 'Applications Started' },
  { key: 'applications_completed', label: 'Applications Completed' },
  { key: 'conversion_rate', label: 'Conversion Rate', pct: true },
];

export default function AgentKpiTab({
  agentId,
}: AgentKpiTabProps): JSX.Element {
  const [window, setWindow] = useState<Window>('all');
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    const params = new URLSearchParams({ window });
    if (agentId) params.set('owner', agentId);
    apiFetch(`/enrollment/agent-kpis?${params.toString()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`agent-kpis request failed: ${res.status}`);
        return res.json() as Promise<AgentKpis>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [window, agentId]);

  return (
    <section aria-label="Personal KPIs" data-testid="agent-kpi-tab">
      <div
        className="dash-window-tabs"
        role="group"
        aria-label="Time window"
        style={{ marginBottom: 'var(--s-4)' }}
      >
        {WINDOWS.map((w) => {
          const active = w.key === window;
          return (
            <button
              key={w.key}
              type="button"
              data-testid={`kpi-window-${w.key}`}
              aria-pressed={active}
              onClick={() => setWindow(w.key)}
              style={{
                fontFamily: 'var(--mono)',
                fontSize: '11.5px',
                padding: '8px 12px',
                border: 'none',
                borderRadius: 'var(--r-md)',
                background: active ? 'var(--ink)' : 'transparent',
                color: active ? 'var(--on-ink)' : 'var(--muted)',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {w.label}
            </button>
          );
        })}
      </div>

      {state.status === 'loading' ? (
        <p data-testid="kpi-loading" className="lab">
          Loading your KPIs…
        </p>
      ) : state.status === 'error' ? (
        <p
          data-testid="kpi-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load your KPIs: {state.message}
        </p>
      ) : (
        <Card>
          <div className="dash-kpi-grid" data-testid="kpi-grid">
            {METRICS.map((m) => {
              const raw = state.data[m.key];
              const value = m.pct ? fmtPct(raw) : raw;
              return (
                <Stat
                  key={m.key}
                  label={m.label}
                  value={value}
                />
              );
            })}
          </div>
        </Card>
      )}
    </section>
  );
}

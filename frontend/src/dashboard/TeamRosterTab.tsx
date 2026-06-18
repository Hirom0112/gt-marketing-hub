import { useEffect, useState } from 'react';
import { Users } from 'lucide-react';
import { apiFetch } from '../config';
import { Card, Chip } from '../ui';
import { fmtPct } from '../enrollment/format';

// The Team Roster tab (admin-dashboard redesign, R5). Per-agent KPIs and nothing
// more (admin brief Tab 4). It reads the same shape as AgentRoster
// (GET /enrollment/agents → { agents: [{ agent_id, synthetic_name, tier,
// queue_size, stall_rate, close_rate, load }], unowned }) but drives a
// Day/Week/Month/All window filter (D-15) that passes ?window= to the endpoint
// (default 'all'). Changing the window refetches. Read-only GET (INV-2),
// synthetic only (INV-1), through apiFetch so the principal header scopes it.

type Window = 'day' | 'week' | 'month' | 'all';

const WINDOWS: ReadonlyArray<{ key: Window; label: string }> = [
  { key: 'day', label: 'Day' },
  { key: 'week', label: 'Week' },
  { key: 'month', label: 'Month' },
  { key: 'all', label: 'All' },
];

interface AgentRollup {
  agent_id: string | null;
  name?: string | null;
  synthetic_name?: string | null;
  tier?: string | null;
  queue_size?: number;
  stall_rate?: number; // [0,1]
  close_rate?: number; // [0,1]
  load?: number; // [0,1]
}

interface AgentsResponse {
  agents: AgentRollup[];
  unowned?: AgentRollup;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: AgentsResponse };

function agentName(a: AgentRollup): string {
  return a.name ?? a.synthetic_name ?? a.agent_id ?? 'Unassigned';
}

export default function TeamRosterTab(): JSX.Element {
  const [window, setWindow] = useState<Window>('all');
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/enrollment/agents?window=${window}`)
      .then((res) => {
        if (!res.ok) throw new Error(`agents request failed: ${res.status}`);
        return res.json() as Promise<AgentsResponse>;
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
  }, [window]);

  const raw =
    state.status === 'ready'
      ? (state.data as AgentsResponse | AgentRollup[])
      : undefined;
  const agents: AgentRollup[] = Array.isArray(raw)
    ? raw
    : raw && Array.isArray(raw.agents)
      ? raw.agents
      : [];
  const unowned = Array.isArray(raw) ? undefined : raw?.unowned;

  return (
    <section aria-label="Team roster" data-testid="admin-tab-roster">
      <div className="admin-toolbar" style={{ marginBottom: 'var(--s-3)' }}>
        <div
          className="dash-window-tabs"
          role="group"
          aria-label="Time window"
        >
          {WINDOWS.map((w) => {
            const active = w.key === window;
            return (
              <button
                key={w.key}
                type="button"
                data-testid={`roster-window-${w.key}`}
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
      </div>

      {state.status === 'loading' ? (
        <p data-testid="roster-loading" className="lab">
          Loading the team roster…
        </p>
      ) : state.status === 'error' ? (
        <p
          data-testid="roster-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load the roster: {state.message}
        </p>
      ) : agents.length === 0 ? (
        <div className="admin-empty" data-testid="roster-empty">
          <div className="admin-empty-title">No agents in this window</div>
          <div className="admin-empty-body">
            No per-agent activity for the selected time window.
          </div>
        </div>
      ) : (
        <Card pad={false}>
          <div
            className="lab"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-1)',
              padding: 'var(--s-3) var(--s-4)',
              borderBottom: '1px solid var(--line-2)',
              color: 'var(--muted)',
            }}
          >
            <Users size={12} aria-hidden /> Team roster · queue / stall% / close% /
            load
          </div>

          {agents.map((a) => (
            <div
              key={a.agent_id ?? agentName(a)}
              data-testid="roster-row"
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr repeat(4, auto)',
                alignItems: 'center',
                gap: 'var(--s-3)',
                padding: 'var(--s-2) var(--s-4)',
                borderBottom: '1px solid var(--line-2)',
                color: 'var(--ink)',
              }}
            >
              <span
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--s-2)',
                  minWidth: 0,
                }}
              >
                <span
                  data-testid="roster-name"
                  style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}
                >
                  {agentName(a)}
                </span>
                {a.tier ? (
                  <Chip tone="neutral" title="Tier">
                    {a.tier}
                  </Chip>
                ) : null}
              </span>
              <span className="mono" data-testid="roster-queue" title="Queue size">
                {a.queue_size ?? 0}
              </span>
              <span
                className="mono"
                data-testid="roster-stall"
                title="Stall rate"
                style={{ color: 'var(--signal-ink)' }}
              >
                {fmtPct(a.stall_rate ?? 0)}
              </span>
              <span
                className="mono"
                data-testid="roster-close"
                title="Close rate"
                style={{ color: 'var(--flow-ink)' }}
              >
                {fmtPct(a.close_rate ?? 0)}
              </span>
              <span className="mono" data-testid="roster-load" title="Load">
                {fmtPct(a.load ?? 0)}
              </span>
            </div>
          ))}

          {unowned !== undefined &&
          typeof unowned.queue_size === 'number' ? (
            <div
              data-testid="roster-unowned"
              className="lab"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: 'var(--s-2) var(--s-4)',
                color: 'var(--muted)',
              }}
            >
              <span>Unowned bucket</span>
              <span className="mono" style={{ color: 'var(--signal-ink)' }}>
                {unowned.queue_size}
              </span>
            </div>
          ) : null}
        </Card>
      )}
    </section>
  );
}

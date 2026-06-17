import { useEffect, useState } from 'react';
import { Users } from 'lucide-react';
import { apiFetch } from '../config';
import { Card, Chip } from '../ui';
import { fmtPct } from './format';

// AgentRoster (M3) — the admin's per-agent roll-up panel
// (MULTI_AGENT_COCKPIT.md §5). Reads GET /enrollment/agents: one row per agent
// (name + tier + queue_size / stall% / close% / load) plus the unowned bucket
// count. The admin reads team load at a glance before slicing the working list
// or routing the intake desk.
//
// Read-only GET (INV-2). Synthetic only (INV-1); reads through apiFetch (INV-5).
// The field names follow MULTI_AGENT_COCKPIT §5 (queue_size, stall_rate,
// close_rate, load, agent identity name+tier); the director reconciles with the
// backend's final shape if it differs.

interface AgentRollup {
  agent_id: string;
  // The backend may use either name or synthetic_name; we read both.
  name?: string;
  synthetic_name?: string;
  tier?: string;
  rank?: number;
  queue_size?: number;
  stall_rate?: number; // [0,1]
  close_rate?: number; // [0,1]
  load?: number; // [0,1] capacity utilization
}

interface AgentsResponse {
  agents: AgentRollup[];
  // The unowned bucket — families with no assigned_rep (the intake pool). The
  // backend (GET /enrollment/agents) returns this as a full AgentRollup with a
  // null identity, NOT a bare count: read its queue_size for the pool size.
  unowned?: AgentRollup;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: AgentsResponse };

interface AgentRosterProps {
  // The currently filtered agent (the owner-filter chip) — highlighted in the
  // roster. Clicking a roster row drives the same filter.
  activeAgentId?: string | null;
  onFilterAgent?: (agentId: string | null) => void;
  refreshKey?: number;
}

function agentName(a: AgentRollup): string {
  return a.name ?? a.synthetic_name ?? a.agent_id;
}

export default function AgentRoster({
  activeAgentId,
  onFilterAgent,
  refreshKey = 0,
}: AgentRosterProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/enrollment/agents`)
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
  }, [refreshKey]);

  if (state.status === 'loading') {
    return (
      <p data-testid="roster-loading" className="lab">
        Loading the team roster…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="roster-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the roster: {state.message}
      </p>
    );
  }

  // Tolerate a backend that returns a bare array or omits `agents` (mock/early
  // shapes) — never crash on a missing roster.
  const raw = state.data as AgentsResponse | AgentRollup[];
  const agents: AgentRollup[] = Array.isArray(raw)
    ? raw
    : Array.isArray(raw.agents)
      ? raw.agents
      : [];
  const unowned = Array.isArray(raw) ? undefined : raw.unowned;

  return (
    <section aria-label="Agent roster" data-testid="agent-roster">
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

        {agents.map((a) => {
          const active = a.agent_id === activeAgentId;
          return (
            <button
              key={a.agent_id}
              type="button"
              data-testid="roster-row"
              aria-pressed={active}
              onClick={() => onFilterAgent?.(active ? null : a.agent_id)}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr repeat(4, auto)',
                alignItems: 'center',
                gap: 'var(--s-3)',
                width: '100%',
                textAlign: 'left',
                padding: 'var(--s-2) var(--s-4)',
                borderBottom: '1px solid var(--line-2)',
                background: active ? 'var(--flow-wash)' : 'var(--surface)',
                border: 'none',
                borderLeft: active
                  ? '2px solid var(--flow)'
                  : '2px solid transparent',
                cursor: 'pointer',
                fontFamily: 'inherit',
                color: 'var(--ink)',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)', minWidth: 0 }}>
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
            </button>
          );
        })}

        {unowned !== undefined && typeof unowned.queue_size === 'number' && (
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
        )}
      </Card>
    </section>
  );
}

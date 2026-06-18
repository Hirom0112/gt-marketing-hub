import { useState } from 'react';
import AgentRoster from '../enrollment/AgentRoster';

// The Team Roster tab (admin-dashboard redesign). Per-agent KPIs and nothing more
// — it reuses the existing AgentRoster (GET /enrollment/agents: queue_size /
// stall% / close% / load per agent + the unowned pool). A Day/Week/Month/All time
// filter renders as the active-window control; the roster endpoint is a
// point-in-time snapshot (not time-scoped today), so we keep it HONEST — the
// control selects the labelled window and we say so, rather than faking scoped
// numbers. Read-only (INV-2).

type Scope = 'day' | 'week' | 'month' | 'all';

const SCOPES: ReadonlyArray<{ key: Scope; label: string }> = [
  { key: 'day', label: 'Day' },
  { key: 'week', label: 'Week' },
  { key: 'month', label: 'Month' },
  { key: 'all', label: 'All' },
];

export default function TeamRosterTab(): JSX.Element {
  const [scope, setScope] = useState<Scope>('all');

  return (
    <section aria-label="Team roster" data-testid="admin-tab-roster">
      <div className="admin-toolbar" style={{ marginBottom: 'var(--s-3)' }}>
        <div className="scope-dial" role="group" aria-label="Time window">
          {SCOPES.map((s) => (
            <button
              key={s.key}
              type="button"
              className="scope-dial-seg"
              data-testid={`roster-scope-${s.key}`}
              aria-pressed={scope === s.key}
              onClick={() => setScope(s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <span className="admin-toolbar-spacer" />
        <span className="lab" data-testid="roster-scope-note">
          {scope === 'all'
            ? 'All-time snapshot'
            : `${SCOPES.find((s) => s.key === scope)?.label} window — current roster snapshot`}
        </span>
      </div>

      <AgentRoster />
    </section>
  );
}

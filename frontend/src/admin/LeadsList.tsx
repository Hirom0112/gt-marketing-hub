import { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { apiFetch } from '../config';
import { DEMO_AGENTS } from '../LoginPage';
import { fmtUSD, fundingLabel } from '../enrollment/format';
import RecencyChip from '../enrollment/RecencyChip';
import type { WorkQueueRow } from './types';

// The Leads LIST view (admin-dashboard redesign). Rows come from GET /work-queue.
// Controls: filter by agent (DEMO_AGENTS + an Unassigned option), a Day/Week/All
// time scope over stall_date, a status filter (Overdue/Fresh/Working/Contacted),
// a household-name search (D-4), and a Triage filter that surfaces families
// "falling through the cracks". Clicking a row populates the right detail panel.
// Read-only GET (INV-2); every filter is applied client-side over the fetched rows.

const UNASSIGNED = '__unassigned__';

export type TimeScope = 'day' | 'week' | 'all';
export type StatusFilter = 'all' | 'overdue' | 'fresh' | 'working' | 'contacted';

// The (month, day) the Day/Week scope windows around — set when the operator
// arrives from a calendar agent chip; null ⇒ anchor on the latest stall in view.
export interface DayAnchor {
  month: string; // YYYY-MM
  day: number;
}

interface LeadsListProps {
  selectedFamilyId: string | null;
  onSelectFamily: (familyId: string) => void;
  // Controlled by the parent so a calendar agent chip can pre-filter the list.
  agentFilter: string | null; // null = all; UNASSIGNED for the unowned pool.
  onAgentFilter: (value: string | null) => void;
  scope: TimeScope;
  onScope: (scope: TimeScope) => void;
  dayAnchor: DayAnchor | null;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: WorkQueueRow[] };

const AGENT_NAME = new Map(DEMO_AGENTS.map((a) => [a.id, a.name]));

function utcDate(iso: string): { y: number; m: number; d: number } | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const dt = new Date(ms);
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

// Whole-day difference between two ISO instants (UTC day-bucketed).
function dayDiff(aIso: string, anchor: { y: number; m: number; d: number }): number {
  const a = utcDate(aIso);
  if (a === null) return Number.POSITIVE_INFINITY;
  const ams = Date.UTC(a.y, a.m - 1, a.d);
  const bms = Date.UTC(anchor.y, anchor.m - 1, anchor.d);
  return Math.round((ams - bms) / 86_400_000);
}

export default function LeadsList({
  selectedFamilyId,
  onSelectFamily,
  agentFilter,
  onAgentFilter,
  scope,
  onScope,
  dayAnchor,
}: LeadsListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [status, setStatus] = useState<StatusFilter>('all');
  const [query, setQuery] = useState('');
  const [triage, setTriage] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/work-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue failed: ${res.status}`);
        return res.json() as Promise<WorkQueueRow[]>;
      })
      .then((rows) => {
        if (!cancelled)
          setState({ status: 'ready', rows: Array.isArray(rows) ? rows : [] });
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
  }, []);

  const rows = state.status === 'ready' ? state.rows : [];

  // The Day/Week anchor: the explicit calendar day if present, else the latest
  // stall_date among the agent-scoped rows (so a manual Day/Week is non-empty).
  const anchor = useMemo<{ y: number; m: number; d: number } | null>(() => {
    if (dayAnchor !== null) {
      const [yStr, mStr] = dayAnchor.month.split('-');
      return { y: Number(yStr), m: Number(mStr), d: dayAnchor.day };
    }
    let latest: number | null = null;
    for (const r of rows) {
      const ms = Date.parse(r.stall_date);
      if (!Number.isNaN(ms) && (latest === null || ms > latest)) latest = ms;
    }
    if (latest === null) return null;
    const dt = new Date(latest);
    return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
  }, [dayAnchor, rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      // Agent filter.
      if (agentFilter === UNASSIGNED) {
        if (r.assigned_rep_id !== null) return false;
      } else if (agentFilter !== null) {
        if (r.assigned_rep_id !== agentFilter) return false;
      }
      // Time scope over stall_date (anchored).
      if (scope !== 'all' && anchor !== null) {
        const diff = dayDiff(r.stall_date, anchor);
        if (scope === 'day' && diff !== 0) return false;
        if (scope === 'week' && Math.abs(diff) > 3) return false;
      }
      // Status filter.
      if (status === 'overdue' && r.contact_status !== 'overdue') return false;
      if (status === 'fresh' && r.contact_status !== 'fresh') return false;
      if (status === 'contacted' && r.contact_status !== 'followed_up')
        return false;
      if (status === 'working' && r.recovery_state !== 'working') return false;
      // Search over the household display name (D-4).
      if (q !== '' && !r.display_name.toLowerCase().includes(q)) return false;
      // Triage: families falling through the cracks — not yet followed up/closed
      // (contact_status fresh/overdue) AND a cold/stalled recovery state.
      if (triage) {
        const notFollowed =
          r.contact_status === 'fresh' || r.contact_status === 'overdue';
        const coldOrStalled =
          r.recovery_state === 'stalled' || r.recovery_state === 'cold';
        if (!notFollowed || !coldOrStalled) return false;
      }
      return true;
    });
  }, [rows, agentFilter, scope, anchor, status, query, triage]);

  const scopeBtn = (key: TimeScope, label: string): JSX.Element => (
    <button
      type="button"
      className="scope-dial-seg"
      data-testid={`leads-scope-${key}`}
      aria-pressed={scope === key}
      onClick={() => onScope(key)}
    >
      {label}
    </button>
  );

  return (
    <section aria-label="Leads list" data-testid="leads-list">
      <div className="admin-toolbar" style={{ marginBottom: 'var(--s-3)' }}>
        <select
          className="history-sort"
          data-testid="leads-filter-agent"
          aria-label="Filter by agent"
          value={agentFilter ?? ''}
          onChange={(e) => onAgentFilter(e.target.value === '' ? null : e.target.value)}
        >
          <option value="">All agents</option>
          {DEMO_AGENTS.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
          <option value={UNASSIGNED}>Unassigned</option>
        </select>

        <div className="scope-dial" role="group" aria-label="Time scope">
          {scopeBtn('day', 'Day')}
          {scopeBtn('week', 'Week')}
          {scopeBtn('all', 'All')}
        </div>

        <select
          className="history-sort"
          data-testid="leads-filter-status"
          aria-label="Filter by status"
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
        >
          <option value="all">Any status</option>
          <option value="overdue">Overdue</option>
          <option value="fresh">Fresh</option>
          <option value="working">Working</option>
          <option value="contacted">Contacted</option>
        </select>

        <button
          type="button"
          className="facet-pill"
          data-testid="leads-triage"
          aria-pressed={triage}
          onClick={() => setTriage((t) => !t)}
          title="Families falling through the cracks"
        >
          Triage
        </button>

        <label
          className="history-tools"
          style={{ flex: 1, minWidth: 160, display: 'flex', gap: 'var(--s-1)', alignItems: 'center' }}
        >
          <Search size={13} aria-hidden style={{ color: 'var(--muted)' }} />
          <input
            className="history-search"
            data-testid="leads-search"
            aria-label="Search households"
            placeholder="Search household name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>

      {state.status === 'loading' && (
        <p data-testid="leads-list-loading" className="lab">
          Loading the work queue…
        </p>
      )}
      {state.status === 'error' && (
        <p
          data-testid="leads-list-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load the work queue: {state.message}
        </p>
      )}

      {state.status === 'ready' &&
        (filtered.length === 0 ? (
          <div className="admin-empty" data-testid="leads-list-empty">
            <span className="admin-empty-title">No leads match</span>
            <span className="admin-empty-body">
              Widen the time scope, clear the agent or status filter, or turn off
              Triage to see more of the queue.
            </span>
          </div>
        ) : (
          <div data-testid="leads-list-rows">
            {filtered.map((r) => {
              const agent =
                r.assigned_rep_id === null
                  ? 'Unassigned'
                  : (AGENT_NAME.get(r.assigned_rep_id) ?? 'Assigned');
              return (
                <button
                  key={r.family_id}
                  type="button"
                  data-testid="lead-row"
                  data-family={r.family_id}
                  className={`admin-row${selectedFamilyId === r.family_id ? ' is-active' : ''}`}
                  onClick={() => onSelectFamily(r.family_id)}
                >
                  <span style={{ minWidth: 0 }}>
                    <span className="admin-row-name">{r.display_name}</span>
                    <span className="admin-row-sub">
                      {agent} · {fundingLabel(r.funding_type)}
                    </span>
                  </span>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 'var(--s-2)',
                      flexShrink: 0,
                    }}
                  >
                    <RecencyChip status={r.contact_status} testId="lead-recency" />
                    <span className="admin-row-value">{fmtUSD(r.value)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        ))}
    </section>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { apiFetch } from '../config';
import { DEMO_AGENTS } from '../LoginPage';
import { fmtUSD, fmtDay, fundingLabel } from '../enrollment/format';
import RecencyChip from '../enrollment/RecencyChip';
import type { WorkQueueRow } from './types';

// The shared Leads LIST view (redesign R2). Rows come from GET /work-queue
// (owner-scoped server-side). Filters: a Day/Week/All time scope over stall_date,
// a status filter (Overdue/Fresh/Working/Contacted), and a search box over the
// household display name AND student first names (D-17 — a one-shot GET /students
// at mount builds a family_id → [first names] map). When `showTriageFilter` is on
// (admin), a Triage facet surfaces families "falling through the cracks" — no
// logged contact OR an overdue follow-up — reusing the recency helpers. Each row
// shows family name, student name(s), a RecencyChip status, last activity, and the
// next action date. A row click lifts the family id to the detail panel. Read-only
// GET (INV-2); every filter is applied client-side over the fetched rows.

export type TimeScope = 'day' | 'week' | 'all';
export type StatusFilter = 'all' | 'overdue' | 'fresh' | 'working' | 'contacted';

interface LeadsListProps {
  onSelectFamily: (familyId: string) => void;
  selectedFamilyId?: string | null;
  // Pre-filter arriving from a calendar day/chip click: a day (1-31) and an
  // optional owning agent. A day pins DAY scope; an agent pins the agent select.
  initialFilter?: { day?: number; agentId?: string };
  // Admin shells surface the Triage facet; agent shells get their own Triage tab.
  showTriageFilter?: boolean;
}

// A work-queue row plus the optional recency instant the list reads for the
// "last activity" column (types.WorkQueueRow omits it; we read it defensively).
interface QueueRowWithContact extends WorkQueueRow {
  last_contact_at?: string | null;
}

// One household's student first names (D-17), keyed by family_id off GET /students.
interface StudentBoardRow {
  family_id: string;
  synthetic_first_name: string;
}
interface StudentHousehold {
  family_id: string;
  students: StudentBoardRow[];
}
interface StudentBoardResponse {
  households: StudentHousehold[];
}

function isStudentBoard(value: unknown): value is StudentBoardResponse {
  if (typeof value !== 'object' || value === null) return false;
  return Array.isArray((value as Record<string, unknown>).households);
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: QueueRowWithContact[] };

const AGENT_NAME = new Map(DEMO_AGENTS.map((a) => [a.id, a.name]));

function utcDate(iso: string): { y: number; m: number; d: number } | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const dt = new Date(ms);
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

// Whole-day difference between an ISO instant and a (y,m,d) anchor (UTC-bucketed).
function dayDiff(aIso: string, anchor: { y: number; m: number; d: number }): number {
  const a = utcDate(aIso);
  if (a === null) return Number.POSITIVE_INFINITY;
  const ams = Date.UTC(a.y, a.m - 1, a.d);
  const bms = Date.UTC(anchor.y, anchor.m - 1, anchor.d);
  return Math.round((ams - bms) / 86_400_000);
}

// True if a family is "falling through the cracks": no logged contact (no activity
// or follow-up) OR an overdue follow-up. The same predicate the agent Triage tab
// uses (D-12), reusing the recency-status signals.
function isTriageCrack(r: QueueRowWithContact): boolean {
  const noContact = r.last_contact_at == null;
  const overdue = r.contact_status === 'overdue';
  return noContact || overdue;
}

export default function LeadsList({
  onSelectFamily,
  selectedFamilyId = null,
  initialFilter,
  showTriageFilter = false,
}: LeadsListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [studentNames, setStudentNames] = useState<Map<string, string[]>>(
    () => new Map(),
  );
  const [agentFilter, setAgentFilter] = useState<string | null>(
    initialFilter?.agentId ?? null,
  );
  const [scope, setScope] = useState<TimeScope>(
    initialFilter?.day != null ? 'day' : 'all',
  );
  const [status, setStatus] = useState<StatusFilter>('all');
  const [query, setQuery] = useState('');
  const [triage, setTriage] = useState(false);

  // The work queue.
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/work-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue failed: ${res.status}`);
        return res.json() as Promise<QueueRowWithContact[]>;
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

  // D-17 — a one-shot /students fetch builds the family_id → [first names] index
  // so search can match a student's name without a per-row deal-view fetch.
  useEffect(() => {
    let cancelled = false;
    apiFetch(`/students?scope=all`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (cancelled || !isStudentBoard(data)) return;
        const map = new Map<string, string[]>();
        for (const h of data.households) {
          map.set(
            h.family_id,
            h.students.map((s) => s.synthetic_first_name).filter(Boolean),
          );
        }
        setStudentNames(map);
      })
      .catch(() => {
        /* search degrades to household-name-only if /students is unavailable */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo<QueueRowWithContact[]>(
    () => (state.status === 'ready' ? state.rows : []),
    [state],
  );

  // The Day/Week anchor: the calendar-supplied day (within the latest stall month
  // in view) if present, else the latest stall_date so a manual Day/Week is
  // non-empty. We resolve the (y,m) from the rows' newest stall_date.
  const anchor = useMemo<{ y: number; m: number; d: number } | null>(() => {
    let latest: number | null = null;
    for (const r of rows) {
      const ms = Date.parse(r.stall_date);
      if (!Number.isNaN(ms) && (latest === null || ms > latest)) latest = ms;
    }
    if (latest === null) return null;
    const dt = new Date(latest);
    const base = {
      y: dt.getUTCFullYear(),
      m: dt.getUTCMonth() + 1,
      d: dt.getUTCDate(),
    };
    if (initialFilter?.day != null) return { ...base, d: initialFilter.day };
    return base;
  }, [rows, initialFilter?.day]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      // Agent filter.
      if (agentFilter !== null && r.assigned_rep_id !== agentFilter) return false;
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
      // Search over household name + student first names (D-17).
      if (q !== '') {
        const names = studentNames.get(r.family_id) ?? [];
        const haystack = [r.display_name, ...names].join(' ').toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      // Triage: families falling through the cracks (admin facet, D-12).
      if (triage && !isTriageCrack(r)) return false;
      return true;
    });
  }, [rows, agentFilter, scope, anchor, status, query, triage, studentNames]);

  const scopeBtn = (key: TimeScope, label: string): JSX.Element => (
    <button
      type="button"
      className="scope-dial-seg"
      data-testid={`leads-scope-${key}`}
      aria-pressed={scope === key}
      onClick={() => setScope(key)}
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
          onChange={(e) =>
            setAgentFilter(e.target.value === '' ? null : e.target.value)
          }
        >
          <option value="">All agents</option>
          {DEMO_AGENTS.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
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

        {showTriageFilter && (
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
        )}

        <label
          className="history-tools"
          style={{
            flex: 1,
            minWidth: 160,
            display: 'flex',
            gap: 'var(--s-1)',
            alignItems: 'center',
          }}
        >
          <Search size={13} aria-hidden style={{ color: 'var(--muted)' }} />
          <input
            className="history-search"
            data-testid="leads-search"
            aria-label="Search families and students"
            placeholder="Search family or student name…"
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
              const names = studentNames.get(r.family_id) ?? [];
              const kids = names.length > 0 ? names.join(', ') : '—';
              const lastActivity =
                r.last_contact_at != null ? fmtDay(r.last_contact_at) : '—';
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
                    <span className="admin-row-sub" data-testid="lead-row-students">
                      {kids} · {agent} · {fundingLabel(r.funding_type)}
                    </span>
                    <span className="admin-row-sub" data-testid="lead-row-dates">
                      Last activity {lastActivity} · Next action{' '}
                      {fmtDay(r.stall_date)}
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

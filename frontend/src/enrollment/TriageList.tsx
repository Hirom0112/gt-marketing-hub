import { useEffect, useMemo, useState } from 'react';
import { apiBaseUrl } from '../config';
import { Card } from '../ui';
import DrillRow, { DrillRowHead } from './DrillRow';
import BulkBar from './BulkBar';
import {
  DrillToolbar,
  ROW_CAP,
  type CalendarEntry,
  type SortKey,
  sortEntries,
} from './EnrollmentCalendar';
import { fmtDay, fmtUSD } from './format';
import type { DrillBulk } from './EnrollmentCalendar';

// TriageList (S13 W1, decision A-22) — the OVERFLOW CONSOLE. This is NOT a second
// surface: it is the unscoped end of the calendar's own drill. ONE list, ONE
// ranking (recoverable-now everywhere), bulk ALWAYS attached, with a SCOPE DIAL —
// Day / Week / All — that just changes the stall-date WINDOW of the same active
// set. The wave doesn't respect day boundaries; neither does this — you pull the
// best-to-recover families from across every day and batch the top-N by value.
//
// The active set (the live recovery queue {stalled,working}) is fetched ONCE via
// GET /work-queue?scope=active (small/fast, ~hundreds). Scope is a PURE CLIENT
// FILTER over a stall_date window — there are NO per-scope backend calls:
//   · Day  — a single UTC day (the calendar drill: click a heat cell/chip).
//   · Week — the 7-day window containing the anchor (or the current week).
//   · All  — the whole active board.
//
// Date-sort and the S12 day-grouping are GONE: organizing by date is the
// calendar's job one level up; rebuilding it inside the list just rebuilt the
// calendar. The stall_date stays as an informational column on every row.
// History (recovered/dismissed) is NOT a scope here — it lives in its own tab.
// Read-only GET (INV-2); bulk writes delegate to the shared workspace handlers.

// One /work-queue row (backend WorkQueueItem, W1 shape). Adapted to the
// CalendarEntry-shaped object the shared sort + DrillRow read.
interface WorkQueueItem {
  family_id: string;
  display_name: string;
  current_stage: string;
  score: number;
  recoverability: number;
  value: number;
  stall_date: string;
  recoverable_now?: number;
  freshness?: number;
  contact_status: string;
  recovery_state: string;
  last_contact_at?: string | null;
}

// The triage scope dial: the stall-date window over the one active set.
export type TriageScope = 'day' | 'week' | 'all';
type Recency = 'all' | 'overdue' | 'fresh' | 'followed_up';

// The list-only sorts (date-sort REMOVED — A-22; the calendar owns "by date").
type TriageSort = Exclude<SortKey, 'date'>;

interface TriageListProps {
  // The active scope dial value + its anchor day (an ISO string within the
  // Day/Week window). Controlled by the workspace so the calendar can open the
  // list at Day scope for a specific day and the dial reflects it.
  scope: TriageScope;
  anchorDate?: string;
  onScopeChange: (scope: TriageScope, anchorDate?: string) => void;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  bulk: DrillBulk;
  sort: SortKey;
  onSort: (sort: SortKey) => void;
  // Bumped by the workspace after a bulk write so the list re-pulls the queue
  // and the moved families reflect their new recovery_state (no client write).
  refreshKey?: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: WorkQueueItem[] };

// Adapt a queue row to the shared CalendarEntry shape (the sort + rows read it).
function toEntry(item: WorkQueueItem): CalendarEntry & { recovery_state: string } {
  return {
    family_id: item.family_id,
    display_name: item.display_name,
    stall_date: item.stall_date,
    current_stage: item.current_stage,
    contact_status: item.contact_status,
    value: item.value,
    score: item.score,
    recoverable_now: item.recoverable_now,
    freshness: item.freshness,
    recovery_state: item.recovery_state,
  };
}

// The UTC ms at the start of the day an ISO instant falls on (NaN-safe → null).
function dayStartMs(iso: string): number | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

// The [start, end) UTC-ms window for a scope around an anchor. Day = one UTC day;
// Week = the 7-day window starting Sunday of the anchor's week; All = unbounded.
// A missing anchor (no day picked) → the current week / day relative to `now`.
function scopeWindow(
  scope: TriageScope,
  anchorDate: string | undefined,
  now: number,
): { start: number; end: number } | null {
  if (scope === 'all') return null;
  const DAY = 86_400_000;
  const baseMs = anchorDate ? dayStartMs(anchorDate) : null;
  const anchorMs =
    baseMs ??
    (() => {
      const d = new Date(now);
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
    })();
  if (scope === 'day') return { start: anchorMs, end: anchorMs + DAY };
  // Week: Sunday-anchored 7-day window containing the anchor day.
  const weekday = new Date(anchorMs).getUTCDay();
  const start = anchorMs - weekday * DAY;
  return { start, end: start + 7 * DAY };
}

export default function TriageList({
  scope,
  anchorDate,
  onScopeChange,
  selectedFamilyId,
  onSelectFamily,
  bulk,
  sort,
  onSort,
  refreshKey = 0,
}: TriageListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [recency, setRecency] = useState<Recency>('all');

  // Fetch the active set ONCE (and on a bulk write). Scoping is client-side over
  // this one pull — no per-scope endpoints (A-22).
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/work-queue?scope=active`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue request failed: ${res.status}`);
        return res.json() as Promise<WorkQueueItem[]>;
      })
      .then((items) => {
        if (!cancelled) setState({ status: 'ready', items });
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

  // The date-sort is removed — coerce any stray 'date' to the recoverable-now
  // default so the dropdown never date-sorts inside the list (A-22).
  const effectiveSort: TriageSort = sort === 'date' ? 'recoverable' : sort;

  // Scope filter (a stall_date window) THEN the recency filter, computed before
  // the early returns so the hook order is stable across loading/error/ready.
  const scoped = useMemo<WorkQueueItem[]>(() => {
    const items = state.status === 'ready' ? state.items : [];
    const win = scopeWindow(scope, anchorDate, Date.now());
    let out = items;
    if (win) {
      out = out.filter((it) => {
        const ms = dayStartMs(it.stall_date);
        return ms !== null && ms >= win.start && ms < win.end;
      });
    }
    if (recency !== 'all') out = out.filter((it) => it.contact_status === recency);
    return out;
  }, [state, scope, anchorDate, recency]);

  const ranked = useMemo(
    () => sortEntries(scoped.map(toEntry), effectiveSort),
    [scoped, effectiveSort],
  );
  const shown = useMemo(() => ranked.slice(0, ROW_CAP), [ranked]);
  const atRisk = useMemo(
    () => ranked.reduce((a, e) => a + e.value, 0),
    [ranked],
  );

  if (state.status === 'loading') {
    return (
      <p data-testid="triage-loading" className="lab">
        Loading the wave…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="triage-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the recovery queue: {state.message}
      </p>
    );
  }

  function switchScope(next: TriageScope): void {
    // Widen All → drop the anchor; Day/Week keep the current anchor (or default
    // to the current period via scopeWindow's now-fallback).
    onScopeChange(next, next === 'all' ? undefined : anchorDate);
    setRecency('all');
    bulk.onClear();
  }

  const scopeControls = (
    <div
      data-testid="triage-scope"
      style={{ display: 'inline-flex', gap: 'var(--s-2)', alignItems: 'center' }}
    >
      <ScopePill
        label="Day"
        on={scope === 'day'}
        onClick={() => switchScope('day')}
        testId="scope-day"
      />
      <ScopePill
        label="Week"
        on={scope === 'week'}
        onClick={() => switchScope('week')}
        testId="scope-week"
      />
      <ScopePill
        label="All"
        on={scope === 'all'}
        onClick={() => switchScope('all')}
        testId="scope-all"
      />
      <span style={{ display: 'inline-flex', gap: 4, marginLeft: 'var(--s-2)' }}>
        {(['all', 'overdue', 'fresh', 'followed_up'] as const).map((r) => (
          <ScopePill
            key={r}
            label={r === 'followed_up' ? 'working' : r}
            on={recency === r}
            onClick={() => setRecency(r)}
            testId={`recency-${r}`}
          />
        ))}
      </span>
    </div>
  );

  const scopeNote =
    scope === 'all'
      ? 'the whole active board'
      : scope === 'week'
        ? 'this week'
        : anchorDate
          ? fmtDay(anchorDate)
          : 'today';

  return (
    <section aria-label="Triage list" data-testid="triage-list">
      <Card pad={false}>
        <div
          data-testid="triage-banner"
          className="lab"
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 'var(--s-2)',
            padding: 'var(--s-2) var(--s-4)',
            borderBottom: '1px solid var(--line-2)',
            color: 'var(--muted)',
          }}
        >
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>
            Recover in priority order — the order to attack the wave
          </span>
          <span style={{ marginLeft: 'auto' }} className="mono">
            {ranked.length} in {scopeNote} · {fmtUSD(atRisk)} at risk
          </span>
        </div>
        <DrillToolbar
          count={shown.length}
          sort={effectiveSort}
          onSort={onSort}
          onSelectAll={() => bulk.onSelectAll(shown.map((e) => e.family_id))}
          scopeControls={scopeControls}
        />
        <DrillRowHead />
        {shown.length === 0 ? (
          <p
            data-testid="triage-empty"
            className="lab"
            style={{ padding: 'var(--s-4)', color: 'var(--muted)' }}
          >
            {scope === 'all'
              ? 'No active recovery work — the queue is clear.'
              : `No stalls in ${scopeNote} — widen the scope to see more.`}
          </p>
        ) : (
          shown.map((e, i) => (
            <DrillRow
              key={e.family_id}
              familyId={e.family_id}
              rank={i + 1}
              name={e.display_name}
              stuckStep={e.current_stage}
              stallDate={fmtDay(e.stall_date)}
              value={fmtUSD(e.value)}
              score={e.score.toFixed(2)}
              contactStatus={e.contact_status}
              selected={bulk.selected.has(e.family_id)}
              active={e.family_id === selectedFamilyId}
              onToggle={bulk.onToggle}
              onSelect={onSelectFamily}
            />
          ))
        )}
        {ranked.length > ROW_CAP && (
          <div
            className="lab"
            data-testid="triage-cap-footer"
            style={{ padding: 'var(--s-3) var(--s-4)', color: 'var(--muted)' }}
          >
            Showing top {ROW_CAP} of {ranked.length} by this sort — batch the top
            of the wave first.
          </div>
        )}
        <BulkBar
          count={bulk.selected.size}
          partition={bulk.partition}
          onNudge={bulk.onNudge}
          onCapture={bulk.onCapture}
          onClear={bulk.onClear}
          onDismissStart={bulk.onDismissStart}
          pendingDismiss={bulk.pendingDismiss}
          reasons={bulk.reasons}
          onDismiss={bulk.onDismiss}
          onCancelDismiss={bulk.onCancelDismiss}
        />
      </Card>
    </section>
  );
}

// A small scope/recency filter pill (aria-pressed for the acceptance test).
function ScopePill({
  label,
  on,
  onClick,
  testId,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  testId: string;
}): JSX.Element {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={on}
      onClick={onClick}
      style={{
        border: `1px solid ${on ? 'var(--ink)' : 'var(--line)'}`,
        background: on ? 'var(--ink)' : 'var(--surface)',
        color: on ? 'var(--on-ink)' : 'var(--ink)',
        borderRadius: 'var(--r-pill)',
        padding: '4px 11px',
        fontSize: 11,
        fontWeight: 600,
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {label}
    </button>
  );
}

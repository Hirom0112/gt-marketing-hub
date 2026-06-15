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
import { dayKey, fmtDay, fmtDayHeader, fmtUSD, shortDollars } from './format';
import type { DrillBulk } from './EnrollmentCalendar';

// Show-all list (S12 W4) — the ranked WORKING SET, the second face of the LEFT
// surface (Calendar ⇆ Show all). The master list is GET /work-queue (W1):
//   · SCOPE — Active (?scope=active, the live recovery queue {stalled,working},
//     now small/fast since the server pre-filters the recovered long tail) |
//     History (?scope=history&limit=200, {recovered,dismissed}, capped).
//   · 5 SORTS — recoverable-now, value, score, stall date, recency. On the STALL
//     DATE sort the list GROUPS rows under sticky day headers (newest day first,
//     each header = day · stall count · $ at risk); every other sort is a flat
//     ranked list. A mono stall-date column is ALWAYS shown on every row.
//   · RECENCY FILTER (active scope only) — all | overdue | fresh | working.
//   · ROW_CAP=80 with a "top 80 of N" footer; BulkBar in active scope.
// The calendar is the index into THIS list (the mock's framing). Read-only GET
// (INV-2); bulk writes are delegated to the shared workspace handlers.

// One /work-queue row (backend WorkQueueItem, W1 shape). We adapt it to the
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

type Scope = 'active' | 'history';
type Recency = 'all' | 'overdue' | 'fresh' | 'followed_up';

// The history scope's server-side row cap (never stream the recovered tail).
const HISTORY_LIMIT = 200;

interface ShowAllListProps {
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
// stall_date is the server's derived stall-anchor (the same key the calendar
// groups on) — no longer synthesized from last_contact_at.
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

// One sticky day-group: the UTC day key, its header label + count + $ at risk,
// and the day's rows already ranked by recoverable-now within the day.
interface DayGroup {
  key: string;
  label: string;
  count: number;
  atRisk: number;
  rows: CalendarEntry[];
}

// Bucket a (date-sorted, capped) row list into day groups, newest day first,
// rows within a day ranked by recoverable-now (NOT the date, which is constant
// inside a group). The input is already globally date-desc, so first-seen day
// order IS newest-first.
function groupByDay(rows: readonly CalendarEntry[]): DayGroup[] {
  const order: string[] = [];
  const byKey = new Map<string, CalendarEntry[]>();
  for (const row of rows) {
    const k = dayKey(row.stall_date);
    const bucket = byKey.get(k);
    if (bucket) bucket.push(row);
    else {
      byKey.set(k, [row]);
      order.push(k);
    }
  }
  return order.map((k) => {
    const dayRows = sortEntries(byKey.get(k) ?? [], 'recoverable');
    return {
      key: k,
      label: k === '' ? 'Undated' : fmtDayHeader(dayRows[0]?.stall_date ?? ''),
      count: dayRows.length,
      atRisk: dayRows.reduce((a, e) => a + e.value, 0),
      rows: dayRows,
    };
  });
}

export default function ShowAllList({
  selectedFamilyId,
  onSelectFamily,
  bulk,
  sort,
  onSort,
  refreshKey = 0,
}: ShowAllListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [scope, setScope] = useState<Scope>('active');
  const [recency, setRecency] = useState<Recency>('all');

  // Re-pull whenever the scope or a bulk write (refreshKey) changes. Active pulls
  // the small live recovery queue; History pulls the capped closed-out tail.
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    const url =
      scope === 'active'
        ? `${apiBaseUrl}/work-queue?scope=active`
        : `${apiBaseUrl}/work-queue?scope=history&limit=${HISTORY_LIMIT}`;
    fetch(url)
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
  }, [scope, refreshKey]);

  // The recency filter applies only in the active scope (history has no live
  // contact state to filter on). Computed before the early returns so the hook
  // order is stable across loading / error / ready renders.
  const grouped = sort === 'date';

  const filtered = useMemo<WorkQueueItem[]>(() => {
    const items = state.status === 'ready' ? state.items : [];
    if (scope !== 'active' || recency === 'all') return items;
    return items.filter((it) => it.contact_status === recency);
  }, [state, scope, recency]);

  const ranked = useMemo(
    () => sortEntries(filtered.map(toEntry), sort),
    [filtered, sort],
  );
  const shown = useMemo(() => ranked.slice(0, ROW_CAP), [ranked]);
  // Day groups are built off the SHOWN (capped) rows so the headers' counts and
  // $ at risk match exactly what is on screen.
  const dayGroups = useMemo(
    () => (grouped ? groupByDay(shown) : []),
    [grouped, shown],
  );

  if (state.status === 'loading') {
    return (
      <p data-testid="show-all-loading" className="lab">
        Loading the working set…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="show-all-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the working set: {state.message}
      </p>
    );
  }

  const canBulk = scope === 'active';

  function switchScope(next: Scope): void {
    setScope(next);
    setRecency('all');
    bulk.onClear();
  }

  const scopeControls = (
    <div
      data-testid="show-all-scope"
      style={{ display: 'inline-flex', gap: 'var(--s-2)', alignItems: 'center' }}
    >
      <ScopePill
        label="Active"
        on={scope === 'active'}
        onClick={() => switchScope('active')}
        testId="scope-active"
      />
      <ScopePill
        label="History"
        on={scope === 'history'}
        onClick={() => switchScope('history')}
        testId="scope-history"
      />
      {scope === 'active' && (
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
      )}
    </div>
  );

  // The flat (non-grouped) body — a single ranked list, date column on each row.
  const flatRows = shown.map((e, i) => (
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
      // History rows can't be bulk-acted — no checkbox.
      selected={canBulk ? bulk.selected.has(e.family_id) : undefined}
      active={e.family_id === selectedFamilyId}
      onToggle={canBulk ? bulk.onToggle : undefined}
      onSelect={onSelectFamily}
    />
  ));

  // The grouped (stall-date sort) body — sticky day headers, rows under each.
  let rankCursor = 0;
  const groupedBody = dayGroups.map((g) => (
    <div key={g.key} data-testid={`day-group-${g.key || 'undated'}`}>
      <DayGroupHeader
        label={g.label}
        count={g.count}
        atRisk={g.atRisk}
        groupKey={g.key}
      />
      {g.rows.map((e) => {
        rankCursor += 1;
        return (
          <DrillRow
            key={e.family_id}
            familyId={e.family_id}
            rank={rankCursor}
            name={e.display_name}
            stuckStep={e.current_stage}
            stallDate={fmtDay(e.stall_date)}
            value={fmtUSD(e.value)}
            score={e.score.toFixed(2)}
            contactStatus={e.contact_status}
            selected={canBulk ? bulk.selected.has(e.family_id) : undefined}
            active={e.family_id === selectedFamilyId}
            onToggle={canBulk ? bulk.onToggle : undefined}
            onSelect={onSelectFamily}
          />
        );
      })}
    </div>
  ));

  return (
    <section aria-label="Show all families" data-testid="show-all-list">
      <Card pad={false}>
        <DrillToolbar
          count={shown.length}
          sort={sort}
          onSort={onSort}
          onSelectAll={
            canBulk
              ? () => bulk.onSelectAll(shown.map((e) => e.family_id))
              : undefined
          }
          scopeControls={scopeControls}
        />
        <DrillRowHead />
        {shown.length === 0 ? (
          <p
            data-testid="show-all-empty"
            className="lab"
            style={{ padding: 'var(--s-4)', color: 'var(--muted)' }}
          >
            {scope === 'active'
              ? 'No active recovery work — the queue is clear.'
              : 'No closed-out families in history yet.'}
          </p>
        ) : grouped ? (
          groupedBody
        ) : (
          flatRows
        )}
        {ranked.length > ROW_CAP && (
          <div
            className="lab"
            data-testid="show-all-cap-footer"
            style={{ padding: 'var(--s-3) var(--s-4)', color: 'var(--muted)' }}
          >
            Showing top {ROW_CAP} of {ranked.length} by this sort.
          </div>
        )}
        {canBulk && (
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
        )}
      </Card>
    </section>
  );
}

// A sticky day-group header: the day label · stall count · day's $ at risk. Mono
// date, token-driven, a --line-2 divider — the cockpit grouping affordance.
function DayGroupHeader({
  label,
  count,
  atRisk,
  groupKey,
}: {
  label: string;
  count: number;
  atRisk: number;
  groupKey: string;
}): JSX.Element {
  return (
    <div
      data-testid={`day-group-head-${groupKey || 'undated'}`}
      className="lab"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 1,
        display: 'flex',
        alignItems: 'baseline',
        gap: 'var(--s-2)',
        padding: 'var(--s-2) var(--s-4)',
        background: 'var(--surface-2)',
        borderTop: '1px solid var(--line-2)',
        borderBottom: '1px solid var(--line-2)',
        color: 'var(--muted)',
      }}
    >
      <span className="mono" style={{ color: 'var(--ink)', fontWeight: 600 }}>
        {label}
      </span>
      <span>
        · {count} stall{count === 1 ? '' : 's'}
      </span>
      <span style={{ marginLeft: 'auto' }} className="mono">
        {shortDollars(atRisk)} at risk
      </span>
    </div>
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

import { useEffect, useState } from 'react';
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
import { fmtUSD } from './format';
import type { DrillBulk } from './EnrollmentCalendar';

// Show-all list (S12 W4) — the ranked WORKING SET, the second face of the LEFT
// surface (Calendar ⇆ Show all). The master list is GET /work-queue (W1), now
// sorted by recoverable_now desc and carrying recovery_state. This view:
//   · SCOPE — Active (recovery_state ∈ {stalled,working}) | History ({recovered,
//     dismissed}). History rows render WITHOUT checkboxes (can't bulk-act past).
//   · 5 SORTS — recoverable-now (default), value, score, stall date, recency.
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
  recoverable_now?: number;
  freshness?: number;
  contact_status: string;
  recovery_state: string;
  last_contact_at?: string | null;
}

type Scope = 'active' | 'history';
type Recency = 'all' | 'overdue' | 'fresh' | 'followed_up';

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

const ACTIVE_STATES = new Set(['stalled', 'working']);

// Adapt a queue row to the shared CalendarEntry shape (the sort + rows read it).
// stall_date is synthesized from last_contact_at when present (recency sort uses
// contact_status, not the date, so an absent stall date is harmless).
function toEntry(item: WorkQueueItem): CalendarEntry & { recovery_state: string } {
  return {
    family_id: item.family_id,
    display_name: item.display_name,
    stall_date: item.last_contact_at ?? '',
    current_stage: item.current_stage,
    contact_status: item.contact_status,
    value: item.value,
    score: item.score,
    recoverable_now: item.recoverable_now,
    freshness: item.freshness,
    recovery_state: item.recovery_state,
  };
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

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/work-queue`)
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

  // Scope partition off recovery_state, then (active only) the recency filter.
  const scoped = state.items.filter((it) =>
    scope === 'active'
      ? ACTIVE_STATES.has(it.recovery_state)
      : !ACTIVE_STATES.has(it.recovery_state),
  );
  const filtered =
    scope === 'active' && recency !== 'all'
      ? scoped.filter((it) => it.contact_status === recency)
      : scoped;
  const ranked = sortEntries(filtered.map(toEntry), sort);
  const shown = ranked.slice(0, ROW_CAP);
  const canBulk = scope === 'active';

  const scopeControls = (
    <div
      data-testid="show-all-scope"
      style={{ display: 'inline-flex', gap: 'var(--s-2)', alignItems: 'center' }}
    >
      <ScopePill label="Active" on={scope === 'active'} onClick={() => switchScope('active')} testId="scope-active" />
      <ScopePill label="History" on={scope === 'history'} onClick={() => switchScope('history')} testId="scope-history" />
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

  function switchScope(next: Scope): void {
    setScope(next);
    setRecency('all');
    bulk.onClear();
  }

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
            No families in this view.
          </p>
        ) : (
          shown.map((e, i) => (
            <DrillRow
              key={e.family_id}
              familyId={e.family_id}
              rank={i + 1}
              name={e.display_name}
              stuckStep={e.current_stage}
              value={fmtUSD(e.value)}
              score={e.score.toFixed(2)}
              contactStatus={e.contact_status}
              // History rows can't be bulk-acted — no checkbox.
              selected={canBulk ? bulk.selected.has(e.family_id) : undefined}
              active={e.family_id === selectedFamilyId}
              onToggle={canBulk ? bulk.onToggle : undefined}
              onSelect={onSelectFamily}
            />
          ))
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

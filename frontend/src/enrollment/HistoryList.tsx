import { useEffect, useMemo, useState } from 'react';
import { apiBaseUrl } from '../config';
import { Card } from '../ui';
import DrillRow, { DrillRowHead } from './DrillRow';
import {
  ROW_CAP,
  type CalendarEntry,
  sortEntries,
} from './EnrollmentCalendar';
import { fmtDay, fmtUSD } from './format';

// HistoryList (S13 W1, decision A-22) — recovered/dismissed families, EVICTED out
// of the triage list into their OWN clearly-separate view. This is an audit /
// lookback dataset, NOT the triage worklist at any scope — letting it ride along
// muddied the purpose. It reads GET /work-queue?scope=history&limit=200 (the
// closed-out tail, server-capped) and is strictly READ-ONLY: no scope dial, no
// bulk, no select-all, no recover/capture/dismiss — just a browsable list ranked
// by value so the biggest closed-out families are easy to find. Read-only GET
// (INV-2). The IA separation is the point: this is "what we closed", not "what to
// work next".

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

// The history scope's server-side row cap (never stream the recovered tail).
const HISTORY_LIMIT = 200;

interface HistoryListProps {
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  // Bumped after a write moves a family into history so this view re-pulls.
  refreshKey?: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: WorkQueueItem[] };

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

export default function HistoryList({
  selectedFamilyId,
  onSelectFamily,
  refreshKey = 0,
}: HistoryListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/work-queue?scope=history&limit=${HISTORY_LIMIT}`)
      .then((res) => {
        if (!res.ok) throw new Error(`history request failed: ${res.status}`);
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

  const ranked = useMemo(
    () =>
      sortEntries(
        (state.status === 'ready' ? state.items : []).map(toEntry),
        'value',
      ),
    [state],
  );
  const shown = useMemo(() => ranked.slice(0, ROW_CAP), [ranked]);

  if (state.status === 'loading') {
    return (
      <p data-testid="history-loading" className="lab">
        Loading history…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="history-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load history: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="History" data-testid="history-list">
      <Card pad={false}>
        <div
          data-testid="history-banner"
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
            Closed out — recovered &amp; dismissed
          </span>
          <span style={{ marginLeft: 'auto' }} className="mono">
            {ranked.length} in history · read-only audit
          </span>
        </div>
        <DrillRowHead />
        {shown.length === 0 ? (
          <p
            data-testid="history-empty"
            className="lab"
            style={{ padding: 'var(--s-4)', color: 'var(--muted)' }}
          >
            No closed-out families in history yet.
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
              // Read-only: no checkbox, no bulk.
              active={e.family_id === selectedFamilyId}
              onSelect={onSelectFamily}
            />
          ))
        )}
        {ranked.length > ROW_CAP && (
          <div
            className="lab"
            data-testid="history-cap-footer"
            style={{ padding: 'var(--s-3) var(--s-4)', color: 'var(--muted)' }}
          >
            Showing top {ROW_CAP} of {ranked.length} closed-out families.
          </div>
        )}
      </Card>
    </section>
  );
}

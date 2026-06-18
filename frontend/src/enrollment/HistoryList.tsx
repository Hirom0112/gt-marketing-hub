import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../config';
import { Card } from '../ui';
import HistoryRow, { recoveredOutcomeLabel } from './HistoryRow';
import { fmtDay, fmtUSD } from './format';

// HistoryList (S13 redesign) — the read-only ARCHIVE, rebuilt as a DELIBERATELY
// DIFFERENT surface from Triage: recessed --surface-2 ground, no red, no checkbox,
// no rank/score, no recoverable hero, no bulk, no scope dial. Its visual absence
// of controls is the "nothing to do here" cue. Sub-tabs (All / Recovered /
// Dismissed, with counts) swap the columns; a name search filters the loaded
// page; sort is most-recent-first by default. Read-only GET (INV-2).
//
// Backend contract (W-redesign): rows carry recovered_outcome / resolved_at /
// dismiss_reason / dismissed_by / dismissed_at. Active rows have these null. We
// DEGRADE GRACEFULLY: if a field is null (backend not merged yet) we fall back to
// the recovery_state enum for the outcome and stall_date for the date, so History
// still renders + is differentiated.

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
  // The W-redesign history fields (null on active rows / older server).
  recovered_outcome?:
    | 'stage_advanced'
    | 'forms_cleared'
    | 'deposit_received'
    | null;
  resolved_at?: string | null;
  dismiss_reason?: string | null;
  dismissed_by?: string | null;
  dismissed_at?: string | null;
}

const HISTORY_LIMIT = 200;

type HistoryTab = 'all' | 'recovered' | 'dismissed';
type HistorySort = 'recent' | 'value';

interface HistoryListProps {
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  refreshKey?: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: WorkQueueItem[] };

// The closed-out kind for a row. Only a genuinely RECOVERED (won) family is
// 'recovered'; every other closed-out/parked state — dismissed AND the rep
// close-loop's lost/dormant (A-35) — is bucketed 'dismissed' (closed-out, NOT a
// win), so a confirmed-lost family is never mis-counted as recovered. (A dedicated
// 'lost' sub-tab + backend lost_* fields is the proper follow-up — TODO close-loop
// History view.) Falls back to the dismiss fields only if recovery_state is absent.
function kindOf(it: WorkQueueItem): 'recovered' | 'dismissed' {
  if (it.recovery_state === 'recovered') return 'recovered';
  if (it.recovery_state != null) return 'dismissed';
  if (it.dismiss_reason || it.dismissed_at || it.dismissed_by)
    return 'dismissed';
  return 'recovered';
}

// The resolved/dismissed instant for a row (graceful fallback to stall_date).
function whenMs(it: WorkQueueItem): number {
  const iso = it.resolved_at ?? it.dismissed_at ?? it.stall_date;
  const ms = Date.parse(iso ?? '');
  return Number.isNaN(ms) ? 0 : ms;
}

function whenIso(it: WorkQueueItem): string {
  return it.resolved_at ?? it.dismissed_at ?? it.stall_date ?? '';
}

export default function HistoryList({
  selectedFamilyId,
  onSelectFamily,
  refreshKey = 0,
}: HistoryListProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [tab, setTab] = useState<HistoryTab>('all');
  const [sort, setSort] = useState<HistorySort>('recent');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/work-queue?scope=history&limit=${HISTORY_LIMIT}`)
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

  const items = useMemo<WorkQueueItem[]>(
    () => (state.status === 'ready' ? state.items : []),
    [state],
  );

  const counts = useMemo(() => {
    let recovered = 0;
    let dismissed = 0;
    for (const it of items) {
      if (kindOf(it) === 'recovered') recovered += 1;
      else dismissed += 1;
    }
    return { recovered, dismissed, all: items.length };
  }, [items]);

  const rows = useMemo(() => {
    let out = items;
    if (tab !== 'all') out = out.filter((it) => kindOf(it) === tab);
    const q = query.trim().toLowerCase();
    if (q) out = out.filter((it) => it.display_name.toLowerCase().includes(q));
    const copy = [...out];
    if (sort === 'value') copy.sort((a, b) => b.value - a.value);
    else copy.sort((a, b) => whenMs(b) - whenMs(a)); // most-recent first
    return copy;
  }, [items, tab, query, sort]);

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

  const subtabs: readonly { key: HistoryTab; label: string; n: number }[] = [
    { key: 'all', label: 'all', n: counts.all },
    { key: 'recovered', label: 'recovered', n: counts.recovered },
    { key: 'dismissed', label: 'dismissed', n: counts.dismissed },
  ];

  return (
    <section aria-label="History" data-testid="history-list">
      <Card pad={false} className="history-surface">
        <div className="history-head" data-testid="history-banner">
          <span className="lab">History · closed out this season</span>
          <span className="history-stat-line" data-testid="history-stat-line">
            <span className="history-stat">
              <span className="history-diamond-recovered" aria-hidden>
                ◆
              </span>
              <span className="mono" data-testid="history-recovered-count">
                {counts.recovered.toLocaleString('en-US')}
              </span>{' '}
              recovered
            </span>
            <span className="history-stat">
              <span className="history-diamond-dismissed" aria-hidden>
                ◆
              </span>
              <span className="mono" data-testid="history-dismissed-count">
                {counts.dismissed.toLocaleString('en-US')}
              </span>{' '}
              dismissed
            </span>
          </span>

          <div
            className="history-subtabs"
            role="tablist"
            data-testid="history-subtabs"
          >
            {subtabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                data-testid={`history-tab-${t.key}`}
                className={`history-subtab ${t.key}`}
                onClick={() => setTab(t.key)}
              >
                {t.label} · {t.n.toLocaleString('en-US')}
              </button>
            ))}
          </div>

          <div className="history-tools">
            <input
              type="search"
              className="history-search"
              data-testid="history-search"
              placeholder="find a family…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Find a family"
            />
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--s-1)',
                fontFamily: 'var(--mono)',
                fontSize: 11,
                color: 'var(--muted)',
              }}
            >
              sort
              <select
                className="history-sort"
                data-testid="history-sort"
                value={sort}
                onChange={(e) => setSort(e.target.value as HistorySort)}
              >
                <option value="recent">most recent</option>
                <option value="value">recovered value</option>
              </select>
            </label>
          </div>
        </div>

        {rows.length === 0 ? (
          <p
            data-testid="history-empty"
            className="lab"
            style={{ padding: 'var(--s-4)', color: 'var(--muted)' }}
          >
            {query.trim()
              ? 'No families match that search.'
              : 'No closed-out families in history yet.'}
          </p>
        ) : (
          rows.map((it) => {
            const kind = kindOf(it);
            return (
              <HistoryRow
                key={it.family_id}
                familyId={it.family_id}
                name={it.display_name}
                kind={kind}
                when={fmtDay(whenIso(it))}
                outcome={recoveredOutcomeLabel(it.recovered_outcome)}
                amount={fmtUSD(it.value)}
                reason={it.dismiss_reason ?? 'Dismissed'}
                operator={it.dismissed_by ?? 'operator'}
                stage={it.current_stage}
                active={it.family_id === selectedFamilyId}
                onSelect={onSelectFamily}
              />
            );
          })
        )}

        <div className="history-foot lab" data-testid="history-foot">
          Showing the {Math.min(rows.length, HISTORY_LIMIT)} most recently
          closed of {counts.all}.
        </div>
      </Card>
    </section>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { apiBaseUrl } from '../config';
import { Card, Button } from '../ui';
import DrillRow, { DrillRowHead } from './DrillRow';
import BulkBar from './BulkBar';
import {
  ROW_CAP,
  type CalendarEntry,
  type SortKey,
  sortEntries,
} from './EnrollmentCalendar';
import { fmtAge, fmtDay, fmtKids, fmtPct, fmtUSD, fundingLabel } from './format';
import type { DrillBulk } from './EnrollmentCalendar';

// TriageList (S13 redesign) — the active worklist, money-first. This is the
// unscoped end of the calendar's own drill: ONE list, recoverable-now ranking
// everywhere, bulk always attached, with a SCOPE DIAL (Day / Week / All) that
// only WIDENS/NARROWS the inherited drill in place. The calendar OWNS which date
// (it seeds the anchor); the dial never re-picks a date.
//
// The active set ({stalled,working}) is fetched ONCE via GET
// /work-queue?scope=active and scoped CLIENT-SIDE by a stall_date window — no
// per-scope endpoints. Read-only GET (INV-2); bulk writes delegate to the shared
// workspace handlers.
//
// Day-scope bug fix: a Day/Week scope with NO anchor anchors to the MOST-RECENT
// active stall_date in the loaded set (not the wall clock), so opening Triage on
// any clock can never fall outside the synthetic stall range and show 0 rows.

interface WorkQueueItem {
  family_id: string;
  display_name: string;
  current_stage: string;
  score: number;
  recoverability: number;
  value: number;
  // A-23 — value drivers: child count (scales value) + funding label.
  num_children: number;
  funding_type?: string | null;
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

// The list-only sorts. 'likely' (recoverability — the hero) is the default; the
// money axis is 'value' (children × tuition). The old composite 'recoverable'
// (recoverable_now) sort is dropped — it read as "just the money" but wasn't the
// plain value, which confused the order. date-sort/score-sort stay removed (the
// calendar owns date; score is a model internal).
type TriageSort = 'likely' | 'value' | 'recency';

interface TriageListProps {
  scope: TriageScope;
  anchorDate?: string;
  onScopeChange: (scope: TriageScope, anchorDate?: string) => void;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  bulk: DrillBulk;
  sort: SortKey;
  onSort: (sort: SortKey) => void;
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
    recoverability: item.recoverability,
    num_children: item.num_children,
    funding_type: item.funding_type,
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

const DAY_MS = 86_400_000;

// The most-recent active stall day in the loaded set (max dayStartMs). NULL when
// the set is empty / has no parseable dates. This is the Day/Week fallback anchor
// — NOT the wall clock (the Day-scope bug fix: anchor to DATA, never Date.now()).
function latestStallDay(items: readonly WorkQueueItem[]): number | null {
  let max: number | null = null;
  for (const it of items) {
    const ms = dayStartMs(it.stall_date);
    if (ms !== null && (max === null || ms > max)) max = ms;
  }
  return max;
}

// The [start, end) UTC-ms window for a scope around an anchor. Day = one UTC day;
// Week = the Sunday-anchored 7-day window containing the anchor. A missing anchor
// resolves to `fallbackAnchorMs` (the latest stall day in the set), NEVER the
// clock. All-scope → null (unbounded). Returns null too when there is no anchor
// and no fallback (an empty set) so the caller shows the right empty state.
function scopeWindow(
  scope: TriageScope,
  anchorDate: string | undefined,
  fallbackAnchorMs: number | null,
): { start: number; end: number } | null {
  if (scope === 'all') return null;
  const baseMs = anchorDate ? dayStartMs(anchorDate) : null;
  const anchorMs = baseMs ?? fallbackAnchorMs;
  if (anchorMs === null) return null;
  if (scope === 'day') return { start: anchorMs, end: anchorMs + DAY_MS };
  const weekday = new Date(anchorMs).getUTCDay();
  const start = anchorMs - weekday * DAY_MS;
  return { start, end: start + 7 * DAY_MS };
}

// The day the list is windowed around (anchor or the latest-stall fallback) — for
// the scope echo + the empty-state remedy copy ("No stalls in {real date}").
function resolvedAnchorMs(
  anchorDate: string | undefined,
  fallbackAnchorMs: number | null,
): number | null {
  const base = anchorDate ? dayStartMs(anchorDate) : null;
  return base ?? fallbackAnchorMs;
}

function fmtMs(ms: number): string {
  return fmtDay(new Date(ms).toISOString());
}

const RECENCY_FACETS: readonly { key: Recency; label: string }[] = [
  { key: 'all', label: 'all' },
  { key: 'overdue', label: 'overdue' },
  { key: 'fresh', label: 'fresh' },
  { key: 'followed_up', label: 'working' },
];

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

  // Coerce anything stray (incl. the dropped 'recoverable'/'score'/'date') to the
  // default hero axis, 'likely'.
  const effectiveSort: TriageSort =
    sort === 'value' || sort === 'recency' ? sort : 'likely';

  const allItems = useMemo<WorkQueueItem[]>(
    () => (state.status === 'ready' ? state.items : []),
    [state],
  );
  const fallbackAnchorMs = useMemo(
    () => latestStallDay(allItems),
    [allItems],
  );

  // Scope filter (a stall_date window) — anchored to DATA, not the clock.
  const scopedAll = useMemo<WorkQueueItem[]>(() => {
    const win = scopeWindow(scope, anchorDate, fallbackAnchorMs);
    if (!win) return scope === 'all' ? allItems : [];
    return allItems.filter((it) => {
      const ms = dayStartMs(it.stall_date);
      return ms !== null && ms >= win.start && ms < win.end;
    });
  }, [allItems, scope, anchorDate, fallbackAnchorMs]);

  // THEN the recency facet (kept separate so we can tell "scope empty" from
  // "filter empty" and offer the right one-tap remedy).
  const scoped = useMemo<WorkQueueItem[]>(
    () =>
      recency === 'all'
        ? scopedAll
        : scopedAll.filter((it) => it.contact_status === recency),
    [scopedAll, recency],
  );

  const ranked = useMemo(
    () => sortEntries(scoped.map(toEntry), effectiveSort),
    [scoped, effectiveSort],
  );
  const shown = useMemo(() => ranked.slice(0, ROW_CAP), [ranked]);

  // Tier-1 readout (A-23): the aggregate $ AT RISK = Σ face value (children ×
  // per-child tuition) — the honest total exposure, not a discounted composite.
  // The per-row magnitude bar uses each family's absolute recoverability (a [0,1]
  // likelihood), so no max-in-scope normalizer is needed.
  const atRiskSum = useMemo(
    () => ranked.reduce((a, e) => a + e.value, 0),
    [ranked],
  );
  const selectedAtRisk = useMemo(() => {
    if (bulk.selected.size === 0) return 0;
    return ranked.reduce(
      (a, e) => (bulk.selected.has(e.family_id) ? a + e.value : a),
      0,
    );
  }, [ranked, bulk.selected]);

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
    onScopeChange(next, next === 'all' ? undefined : anchorDate);
    setRecency('all');
    bulk.onClear();
  }

  const anchorMs = resolvedAnchorMs(anchorDate, fallbackAnchorMs);
  const scopeEcho =
    scope === 'all'
      ? 'across the whole board'
      : scope === 'week'
        ? anchorMs !== null
          ? `the week of ${fmtMs(anchorMs)}`
          : 'this week'
        : anchorMs !== null
          ? `on ${fmtMs(anchorMs)}`
          : 'this day';

  // ── Empty states — never a blank panel; distinguish scope-empty / filter-empty.
  function renderEmpty(): JSX.Element {
    // Filter empty: the scope has rows but the recency facet filtered them all.
    if (recency !== 'all' && scopedAll.length > 0) {
      return (
        <div className="worklist-empty" data-testid="triage-empty">
          <span className="worklist-empty-line" data-testid="triage-empty-filter">
            No {recency === 'followed_up' ? 'working' : recency} stalls {scopeEcho}.
          </span>
          <span className="worklist-empty-remedies">
            <Button
              data-testid="triage-clear-filter"
              onClick={() => setRecency('all')}
            >
              Clear filter
            </Button>
          </span>
        </div>
      );
    }
    // All-scope genuinely empty → the calm rest state.
    if (scope === 'all') {
      return (
        <div className="worklist-empty rest" data-testid="triage-empty">
          <span className="lab">The wave is clear</span>
          <span className="worklist-empty-line" data-testid="triage-empty-rest">
            No active recovery work on the board right now.
          </span>
        </div>
      );
    }
    // Scope empty (a Day/Week with no stalls) → widen remedies.
    return (
      <div className="worklist-empty" data-testid="triage-empty">
        <span className="worklist-empty-line" data-testid="triage-empty-scope">
          {anchorMs !== null
            ? `No stalls ${scope === 'week' ? `in ${scopeEcho}` : scopeEcho}.`
            : 'No stalls in this scope.'}
        </span>
        <span className="worklist-empty-remedies">
          {scope === 'day' && (
            <Button
              data-testid="triage-widen-week"
              onClick={() => switchScope('week')}
            >
              Widen to week
            </Button>
          )}
          <Button data-testid="triage-show-all" onClick={() => switchScope('all')}>
            Show all
          </Button>
        </span>
      </div>
    );
  }

  return (
    <section aria-label="Triage list" data-testid="triage-list">
      <Card pad={false}>
        <div className="triage-head" data-testid="triage-head">
          {/* Tier 1 — the loud readout. */}
          <div className="triage-head-readout" data-testid="triage-banner">
            <span
              className="triage-readout-money"
              data-testid="triage-readout-money"
            >
              {fmtUSD(atRiskSum)}
            </span>
            <span className="triage-readout-sub">at risk</span>
            <span className="triage-readout-sub">
              · <b data-testid="triage-stalled-count">{ranked.length}</b> stalled
            </span>
            <span className="triage-readout-scope lab" data-testid="triage-scope-echo">
              {scopeEcho}
            </span>
          </div>

          {/* Tier 2 — the quiet control cluster. */}
          <div className="triage-controls">
            <div
              className="scope-dial"
              data-testid="triage-scope"
              role="group"
              aria-label="Scope"
            >
              {(['day', 'week', 'all'] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  className="scope-dial-seg"
                  data-testid={`scope-${s}`}
                  aria-pressed={scope === s}
                  onClick={() => switchScope(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            <span className="triage-controls-divider" aria-hidden />
            <div
              style={{ display: 'inline-flex', gap: 'var(--s-1)' }}
              data-testid="triage-recency"
            >
              {RECENCY_FACETS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className="facet-pill"
                  data-testid={`recency-${f.key}`}
                  aria-pressed={recency === f.key}
                  onClick={() => setRecency(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <label
              style={{
                marginLeft: 'auto',
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
                data-testid="list-sort"
                value={effectiveSort}
                onChange={(ev) => onSort(ev.target.value as SortKey)}
                className="history-sort"
              >
                <option value="likely">likely</option>
                <option value="value">value</option>
                <option value="recency">recency</option>
              </select>
            </label>
          </div>
        </div>

        <DrillRowHead />
        {shown.length === 0
          ? renderEmpty()
          : shown.map((e) => (
              <DrillRow
                key={e.family_id}
                familyId={e.family_id}
                name={e.display_name}
                stuckStep={e.current_stage}
                funding={fundingLabel(e.funding_type)}
                stallDate={fmtDay(e.stall_date)}
                age={fmtAge(e.stall_date)}
                likelihood={fmtPct(e.recoverability ?? 0)}
                value={fmtUSD(e.value)}
                kids={fmtKids(e.num_children ?? 1)}
                magnitude={e.recoverability ?? 0}
                contactStatus={e.contact_status}
                selected={bulk.selected.has(e.family_id)}
                active={e.family_id === selectedFamilyId}
                onToggle={bulk.onToggle}
                onSelect={onSelectFamily}
              />
            ))}
        {ranked.length > ROW_CAP && (
          <div
            className="lab"
            data-testid="triage-cap-footer"
            style={{ padding: 'var(--s-3) var(--s-4)', color: 'var(--muted)' }}
          >
            Showing the top {ROW_CAP} of {ranked.length} by {effectiveSort} — batch
            the top of the wave first.
          </div>
        )}
        <BulkBar
          count={bulk.selected.size}
          viewCount={shown.length}
          recoverableLabel={
            selectedAtRisk > 0 ? fmtUSD(selectedAtRisk) : undefined
          }
          onSelectAll={() => bulk.onSelectAll(shown.map((e) => e.family_id))}
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

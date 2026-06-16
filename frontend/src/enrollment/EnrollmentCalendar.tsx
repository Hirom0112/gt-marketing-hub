import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarClock,
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  List,
} from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card } from '../ui';
import CalendarChip from './CalendarChip';
import HeatCell from './HeatCell';
import type { SendPartition } from './BulkBar';
import { shortDollars } from './format';

// Enrollment recovery calendar (S13 W1) — the PRIMARY "find" surface. Families
// land on the day they STALLED (stall_date); a day with ≤4 families lays them out
// as CalendarChips, a busier day COLLAPSES to a HeatCell tinted by volume × $ at
// risk. The calendar OWNS organizing "by date".
//
// The calendar is the INDEX into the triage list, not a driller itself (A-22):
// tapping a heat cell or chip opens the TriageList at DAY scope for that day; a
// "This week" affordance opens WEEK scope; "Show all" opens ALL scope. The
// scope dial (Day/Week/All) then lives on the triage list so the operator can
// widen a drill from one day → week → everything WITHOUT leaving. This surface
// makes NO writes and owns no drill list anymore.
//
// Read-only GETs (INV-2).

const HEAT_THRESHOLD = 4; // >4 families on a day collapse to a heat cell.
const HEAT_DIVISOR = 120; // intensity = min(1, count / 120) (mock parity).
const ROW_CAP = 80; // list cap (shared with the triage + history lists).

// One family on the calendar (backend CalendarEntry, A-16/W1).
export interface CalendarEntry {
  family_id: string;
  display_name: string;
  stall_date: string;
  current_stage: string;
  contact_status: string;
  value: number;
  score: number;
  recoverable_now?: number;
  freshness?: number;
  recovery_state?: string;
}

interface CalendarResponse {
  month: string;
  entries: CalendarEntry[];
}

// The bulk wiring the triage list shares with the workspace (one selection Set,
// one set of bulk handlers). Kept here as the shared contract both the calendar's
// consumers and the triage list import from one place.
export interface DrillBulk {
  selected: ReadonlySet<string>;
  onToggle: (familyId: string) => void;
  onSelectAll: (ids: readonly string[]) => void;
  onClear: () => void;
  onNudge: () => void;
  onCapture: () => void;
  onDismissStart: () => void;
  pendingDismiss: boolean;
  reasons: readonly string[];
  onDismiss: (reason: string) => void;
  onCancelDismiss: () => void;
  partition?: SendPartition;
}

interface EnrollmentCalendarProps {
  // Optional fixed opening month (YYYY-MM) — tests pin it. Omitted → the
  // server-resolved most-recent-stall month (A-16).
  initialMonth?: string;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  // Open the triage list at a scope. A heat cell / chip → ('day', dayISO);
  // "This week" → ('week', monthAnchorISO); "Show all" → ('all').
  onOpenScope: (scope: 'day' | 'week' | 'all', anchorDate?: string) => void;
}

export type SortKey = 'recoverable' | 'value' | 'score' | 'date' | 'recency';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CalendarResponse };

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

function shiftMonth(month: string, delta: number): string {
  const [yStr, mStr] = month.split('-');
  const y = Number(yStr);
  const m = Number(mStr);
  if (Number.isNaN(y) || Number.isNaN(m)) return month;
  const zero = y * 12 + (m - 1) + delta;
  const ny = Math.floor(zero / 12);
  const nm = (zero % 12) + 1;
  return `${String(ny).padStart(4, '0')}-${String(nm).padStart(2, '0')}`;
}

function dayOf(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return 0;
  return new Date(ms).getUTCDate();
}

// The ISO instant for a given day-of-month within the shown month (midnight UTC),
// used as the anchor the triage list windows its Day/Week scope around.
function dayAnchorIso(month: string, day: number): string {
  const [yStr, mStr] = month.split('-');
  const y = Number(yStr);
  const m = Number(mStr);
  return new Date(Date.UTC(y, m - 1, day)).toISOString();
}

function monthLabel(month: string): string {
  const [yStr, mStr] = month.split('-');
  const y = Number(yStr);
  const m = Number(mStr);
  if (Number.isNaN(y) || Number.isNaN(m)) return month;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleString('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

// The recoverable-now magnitude used for the default ranking. Prefers the
// backend's value (W1) and falls back to value×score when absent (older server).
function recoverableNow(e: CalendarEntry): number {
  if (typeof e.recoverable_now === 'number') return e.recoverable_now;
  return e.value * e.score;
}

const RECENCY_ORDER: Record<string, number> = {
  overdue: 0,
  fresh: 1,
  followed_up: 2,
  closed: 3,
};

// Sort a list of entries by the chosen key (shared with the triage + history
// lists). 'date' is retained in the union for back-compat but is no longer
// offered as a sort option in the list toolbar (A-22 — the calendar owns date).
// eslint-disable-next-line react-refresh/only-export-components
export function sortEntries<T extends CalendarEntry>(
  arr: readonly T[],
  sort: SortKey,
): T[] {
  const copy = [...arr];
  switch (sort) {
    case 'value':
      return copy.sort((a, b) => b.value - a.value);
    case 'score':
      return copy.sort((a, b) => b.score - a.score);
    case 'date':
      return copy.sort(
        (a, b) => Date.parse(b.stall_date) - Date.parse(a.stall_date),
      );
    case 'recency':
      return copy.sort(
        (a, b) =>
          (RECENCY_ORDER[a.contact_status] ?? 9) -
          (RECENCY_ORDER[b.contact_status] ?? 9),
      );
    case 'recoverable':
    default:
      return copy.sort((a, b) => recoverableNow(b) - recoverableNow(a));
  }
}

export default function EnrollmentCalendar({
  initialMonth,
  selectedFamilyId,
  onSelectFamily,
  onOpenScope,
}: EnrollmentCalendarProps): JSX.Element {
  const [month, setMonth] = useState<string | null>(initialMonth ?? null);
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const loadedMonth = useRef<string | null>(null);

  useEffect(() => {
    if (month !== null && loadedMonth.current === month) return;
    let cancelled = false;
    setState({ status: 'loading' });
    const url =
      month === null
        ? `${apiBaseUrl}/enrollment/calendar`
        : `${apiBaseUrl}/enrollment/calendar?month=${month}`;
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`calendar request failed: ${res.status}`);
        return res.json() as Promise<CalendarResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        loadedMonth.current = data.month;
        setState({ status: 'ready', data });
        if (month === null) setMonth(data.month);
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
  }, [month]);

  const shownMonth =
    month ?? (state.status === 'ready' ? state.data.month : null);

  // Open the triage list at Day scope for a calendar day (a heat cell / chip).
  const openDay = useCallback(
    (day: number): void => {
      if (shownMonth === null) return;
      onOpenScope('day', dayAnchorIso(shownMonth, day));
    },
    [shownMonth, onOpenScope],
  );

  const goMonth = useCallback((delta: number): void => {
    setMonth((mo) => (mo === null ? mo : shiftMonth(mo, delta)));
  }, []);

  const byDay = useMemo<Map<number, CalendarEntry[]>>(() => {
    const map = new Map<number, CalendarEntry[]>();
    if (state.status !== 'ready') return map;
    for (const entry of state.data.entries) {
      const d = dayOf(entry.stall_date);
      const bucket = map.get(d);
      if (bucket) bucket.push(entry);
      else map.set(d, [entry]);
    }
    return map;
  }, [state]);

  const cells = useMemo<Array<number | null>>(() => {
    if (shownMonth === null) return [];
    const [yStr, mStr] = shownMonth.split('-');
    const y = Number(yStr);
    const m = Number(mStr);
    if (Number.isNaN(y) || Number.isNaN(m)) return [];
    const firstWeekday = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
    const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const out: Array<number | null> = [];
    for (let i = 0; i < firstWeekday; i += 1) out.push(null);
    for (let d = 1; d <= daysInMonth; d += 1) out.push(d);
    return out;
  }, [shownMonth]);

  return (
    <section aria-label="Enrollment calendar" data-testid="enrollment-calendar">
      <div className="calendar-head">
        <div className="lab calendar-head-title">
          <CalendarClock size={12} aria-hidden /> Families by stall date · busy
          days collapse to heat
        </div>
        <div className="calendar-pager">
          <Button
            icon={ChevronLeft}
            data-testid="calendar-prev"
            onClick={() => goMonth(-1)}
            aria-label="Previous month"
            disabled={shownMonth === null}
          />
          <span
            className="mono calendar-month-label"
            data-testid="calendar-month-label"
          >
            {shownMonth === null ? '—' : monthLabel(shownMonth)}
          </span>
          <Button
            icon={ChevronRight}
            data-testid="calendar-next"
            onClick={() => goMonth(1)}
            aria-label="Next month"
            disabled={shownMonth === null}
          />
        </div>
      </div>

      {state.status === 'loading' && (
        <p data-testid="calendar-loading" className="lab">
          Loading calendar…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="calendar-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load calendar: {state.message}
        </p>
      )}

      {state.status === 'ready' && (
        <Card pad>
          <div className="calendar-weekdays">
            {WEEKDAYS.map((wd) => (
              <div key={wd} className="lab" style={{ textAlign: 'center' }}>
                {wd}
              </div>
            ))}
          </div>
          <div className="calendar-grid">
            {cells.map((day, i) => {
              if (day === null) {
                return <div key={`blank-${i}`} aria-hidden />;
              }
              const entries = byDay.get(day) ?? [];
              const hasMany = entries.length > HEAT_THRESHOLD;
              return (
                <div
                  key={`day-${day}`}
                  data-testid={`calendar-day-${day}`}
                  className={`calendar-cell${entries.length > 0 ? ' has-families' : ''}`}
                >
                  <span className="lab calendar-daynum">{day}</span>
                  {hasMany ? (
                    <HeatCell
                      count={entries.length}
                      atRisk={shortDollars(
                        entries.reduce((a, e) => a + e.value, 0),
                      )}
                      intensity={Math.min(1, entries.length / HEAT_DIVISOR)}
                      onClick={() => openDay(day)}
                    />
                  ) : (
                    entries.map((entry) => (
                      <CalendarChip
                        key={entry.family_id}
                        familyId={entry.family_id}
                        name={entry.display_name}
                        value={shortDollars(entry.value)}
                        score={entry.score}
                        contactStatus={entry.contact_status}
                        active={entry.family_id === selectedFamilyId}
                        onSelect={(id) => {
                          onSelectFamily?.(id);
                          // A chip is a single-day drill into the triage list.
                          openDay(dayOf(entry.stall_date));
                        }}
                      />
                    ))
                  )}
                </div>
              );
            })}
          </div>

          {/* Widen affordances — the scope dial proper lives on the triage list. */}
          <div
            className="calendar-scope-cta"
            data-testid="calendar-scope-cta"
            style={{
              display: 'flex',
              gap: 'var(--s-2)',
              padding: 'var(--s-3) var(--s-1) var(--s-1)',
              borderTop: '1px solid var(--line-2)',
              marginTop: 'var(--s-2)',
            }}
          >
            <Button
              icon={CalendarRange}
              data-testid="open-week"
              onClick={() =>
                onOpenScope(
                  'week',
                  shownMonth === null ? undefined : dayAnchorIso(shownMonth, 15),
                )
              }
            >
              This week
            </Button>
            <Button
              icon={List}
              data-testid="open-all"
              onClick={() => onOpenScope('all')}
            >
              Show all
            </Button>
          </div>
        </Card>
      )}
    </section>
  );
}

// The triage / list toolbar: a select-all (capped) + the sort selector. Reused by
// the triage list (exported) so the calendar's consumers and the list share the
// same control row. The 'date' sort is GONE (A-22 — the calendar owns "by date").
export function DrillToolbar({
  count,
  sort,
  onSort,
  onSelectAll,
  scopeControls,
}: {
  count: number;
  sort: SortKey;
  onSort?: (sort: SortKey) => void;
  onSelectAll?: () => void;
  scopeControls?: JSX.Element;
}): JSX.Element {
  return (
    <div
      className="lab"
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 'var(--s-3)',
        padding: 'var(--s-2) var(--s-4)',
        borderBottom: '1px solid var(--line-2)',
      }}
    >
      {scopeControls}
      {onSelectAll && (
        <button
          type="button"
          data-testid="select-all"
          onClick={onSelectAll}
          style={{
            border: '1px solid var(--line)',
            background: 'var(--surface)',
            borderRadius: 'var(--r-pill)',
            padding: '4px 10px',
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--ink)',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          select all ({count})
        </button>
      )}
      <label
        style={{
          marginLeft: 'auto',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
        }}
      >
        sort
        <select
          data-testid="list-sort"
          value={sort}
          onChange={(ev) => onSort?.(ev.target.value as SortKey)}
          style={{
            fontFamily: 'var(--mono)',
            fontSize: 11,
            padding: '3px 6px',
            borderRadius: 'var(--r-sm)',
            border: '1px solid var(--line)',
            background: 'var(--surface)',
            color: 'var(--ink)',
          }}
        >
          <option value="recoverable">recoverable now</option>
          <option value="value">value</option>
          <option value="score">score</option>
          <option value="recency">recency</option>
        </select>
      </label>
    </div>
  );
}

// The shared row cap (the triage + history lists reuse it so all surfaces agree).
// eslint-disable-next-line react-refresh/only-export-components
export { ROW_CAP };

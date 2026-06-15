import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CalendarClock, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card } from '../ui';
import CalendarChip from './CalendarChip';
import HeatCell from './HeatCell';
import DrillRow, { DrillRowHead } from './DrillRow';
import BulkBar, { type SendPartition } from './BulkBar';
import { fmtDay, fmtUSD, shortDollars } from './format';

// Enrollment recovery calendar (S12 W4) — the LEFT "find" surface. The mock's
// loop: families land on the day they STALLED (stall_date); a day with ≤4
// families lays them out as CalendarChips, a busier day COLLAPSES to a HeatCell
// tinted by volume × $ at risk. Tapping a heat cell DRILLS into that day — a
// fetch of `?day=N` returns the day's families, rendered as a ranked DrillRow
// list with a BulkBar. The calendar is the index into a ranked list.
//
// Read-only GETs (INV-2); bulk WRITES are delegated up to the workspace (the
// single owner of the bulk routes + toasts + the shared selection Set), so this
// surface stays a pure find/drill view with no client-side writes.

const HEAT_THRESHOLD = 4; // >4 families on a day collapse to a heat cell.
const HEAT_DIVISOR = 120; // intensity = min(1, count / 120) (mock parity).
const ROW_CAP = 80; // drill list cap (shared with the show-all list).

// One family on the calendar / in a day drill (backend CalendarEntry, A-16/W1).
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

interface DayResponse {
  // The drill endpoint returns the same envelope, narrowed to one day's entries.
  month: string;
  entries: CalendarEntry[];
}

// The bulk wiring the drill list shares with the workspace (one selection Set,
// one set of bulk handlers). Passing it down keeps the bulk routes in one place.
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
  // The shared bulk wiring for the day-drill list.
  bulk: DrillBulk;
  // The drill sort (shared with show-all so the operator's sort carries over).
  sort?: SortKey;
  onSort?: (sort: SortKey) => void;
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

// Sort a list of entries by the chosen key (shared with the show-all list).
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
  bulk,
  sort = 'recoverable',
  onSort,
}: EnrollmentCalendarProps): JSX.Element {
  const [month, setMonth] = useState<string | null>(initialMonth ?? null);
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const loadedMonth = useRef<string | null>(null);
  // The drilled day (a day-of-month within the shown month), or null for grid.
  const [drillDay, setDrillDay] = useState<number | null>(null);
  const [dayState, setDayState] = useState<LoadState | null>(null);

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

  // Open a day's drill — fetch that day's families (the ranked sub-list).
  const openDrill = useCallback(
    (day: number): void => {
      if (shownMonth === null) return;
      bulk.onClear();
      setDrillDay(day);
      setDayState({ status: 'loading' });
      fetch(`${apiBaseUrl}/enrollment/calendar?month=${shownMonth}&day=${day}`)
        .then((res) => {
          if (!res.ok) throw new Error(`drill request failed: ${res.status}`);
          return res.json() as Promise<DayResponse>;
        })
        .then((data) =>
          setDayState({
            status: 'ready',
            data: { month: data.month, entries: data.entries },
          }),
        )
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : 'unknown error';
          setDayState({ status: 'error', message });
        });
    },
    [shownMonth, bulk],
  );

  const closeDrill = useCallback((): void => {
    setDrillDay(null);
    setDayState(null);
    bulk.onClear();
  }, [bulk]);

  // Leaving the month (paging) closes any open drill.
  const goMonth = useCallback(
    (delta: number): void => {
      setDrillDay(null);
      setDayState(null);
      bulk.onClear();
      setMonth((mo) => (mo === null ? mo : shiftMonth(mo, delta)));
    },
    [bulk],
  );

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

  // The drill view replaces the month grid (the mock's "drill into a busy day").
  if (drillDay !== null) {
    return (
      <DayDrill
        day={drillDay}
        monthLabelText={shownMonth === null ? '' : monthLabel(shownMonth)}
        state={dayState ?? { status: 'loading' }}
        selectedFamilyId={selectedFamilyId}
        onSelectFamily={onSelectFamily}
        onBack={closeDrill}
        bulk={bulk}
        sort={sort}
        onSort={onSort}
      />
    );
  }

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
                      onClick={() => openDrill(day)}
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
                        onSelect={onSelectFamily}
                      />
                    ))
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </section>
  );
}

// The day-drill view — a back header + the day's ranked DrillRow list + a
// BulkBar. Replaces the month grid when a heat cell is tapped.
function DayDrill({
  day,
  monthLabelText,
  state,
  selectedFamilyId,
  onSelectFamily,
  onBack,
  bulk,
  sort,
  onSort,
}: {
  day: number;
  monthLabelText: string;
  state: LoadState;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  onBack: () => void;
  bulk: DrillBulk;
  sort: SortKey;
  onSort?: (sort: SortKey) => void;
}): JSX.Element {
  const entries =
    state.status === 'ready' ? sortEntries(state.data.entries, sort) : [];
  const shown = entries.slice(0, ROW_CAP);
  const atRisk = entries.reduce((a, e) => a + e.value, 0);

  return (
    <section aria-label="Day drill" data-testid="calendar-drill">
      <div className="drill-head">
        <button
          type="button"
          data-testid="drill-back"
          onClick={onBack}
          className="lab"
          style={{
            border: 0,
            background: 'none',
            color: 'var(--flow-ink)',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
          }}
        >
          <ChevronLeft size={12} aria-hidden /> back to calendar
        </button>
        <span className="mono drill-title" data-testid="drill-title">
          {monthLabelText.replace(/ \d{4}$/, '')} {day} · {entries.length} stalls
          · {fmtUSD(atRisk)} at risk
        </span>
      </div>

      {state.status === 'loading' && (
        <p data-testid="drill-loading" className="lab">
          Loading day…
        </p>
      )}
      {state.status === 'error' && (
        <p
          data-testid="drill-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load day: {state.message}
        </p>
      )}

      {state.status === 'ready' && (
        <Card pad={false}>
          <DrillToolbar
            count={shown.length}
            sort={sort}
            onSort={onSort}
            onSelectAll={() => bulk.onSelectAll(shown.map((e) => e.family_id))}
          />
          <DrillRowHead />
          {shown.map((e, i) => (
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
          ))}
          {entries.length > ROW_CAP && (
            <div
              className="lab"
              data-testid="drill-cap-footer"
              style={{ padding: 'var(--s-3) var(--s-4)', color: 'var(--muted)' }}
            >
              Showing top {ROW_CAP} of {entries.length} by this sort.
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
      )}
    </section>
  );
}

// The drill / list toolbar: a select-all (capped) + the sort selector. Reused by
// the show-all list (exported) so the two surfaces share the same control row.
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
          <option value="date">stall date</option>
          <option value="recency">recency</option>
        </select>
      </label>
    </div>
  );
}

// The shared row cap (the show-all list reuses it so both surfaces agree).
// eslint-disable-next-line react-refresh/only-export-components
export { ROW_CAP };

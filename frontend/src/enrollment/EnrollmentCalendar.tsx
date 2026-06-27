import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarClock,
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  List,
} from 'lucide-react';
import { apiFetch } from '../config';
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
  // A-23 — recoverability (likelihood, the triage HERO) + the value drivers
  // (child count + funding label). Optional so the calendar's own fetch (which
  // omits recoverability) still satisfies the type; the triage list populates them.
  recoverability?: number;
  num_children?: number;
  funding_type?: string | null;
  recoverable_now?: number;
  freshness?: number;
  recovery_state?: string;
  // M3 admin attribution (anchor=intake): the intake date the family lands on +
  // its owning agent (null ⇒ the unowned pool, feeding the cell's alarm line).
  intake_date?: string;
  assigned_rep_id?: string | null;
  agent_name?: string | null;
}

interface CalendarResponse {
  month: string;
  anchor?: CalendarAnchor;
  entries: CalendarEntry[];
}

// The calendar's anchoring: the rep/operator flavor lands families on their
// STALL date (the recovery find surface); the admin ATTRIBUTION flavor lands
// them on their INTAKE date with per-agent attribution (MULTI_AGENT_COCKPIT §5).
export type CalendarAnchor = 'stall' | 'intake';

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
  // M3: the anchoring flavor. 'stall' (default) is the rep/operator recovery
  // find surface; 'intake' is the admin ATTRIBUTION calendar (intake-by-day +
  // per-agent chips + an unowned-alarm line per cell). Drives ?anchor=intake.
  anchor?: CalendarAnchor;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  // Open the triage list at a scope. A heat cell / chip → ('day', dayISO);
  // "This week" → ('week', monthAnchorISO); "Show all" → ('all').
  onOpenScope: (scope: 'day' | 'week' | 'all', anchorDate?: string) => void;
}

export type SortKey =
  | 'likely'
  | 'recoverable'
  | 'value'
  | 'score'
  | 'date'
  | 'recency';

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

// The recoverable-now magnitude used for the default ranking + the triage hero
// cell / magnitude bar. Prefers the backend's value (W1) and falls back to
// value×score when absent (older server). Exported so the triage list reads the
// SAME recoverable-now everywhere (one canonical home).
// eslint-disable-next-line react-refresh/only-export-components
export function recoverableNow(e: CalendarEntry): number {
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
    case 'likely':
      // The HERO axis (A-23): recoverability/likelihood, highest first. Ties fall
      // back to value so the order is stable + sensible.
      return copy.sort(
        (a, b) =>
          (b.recoverability ?? 0) - (a.recoverability ?? 0) || b.value - a.value,
      );
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
  anchor = 'stall',
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
    // The admin attribution flavor anchors on intake (?anchor=intake); the
    // default rep/operator flavor omits it (server defaults to stall anchoring).
    const params = new URLSearchParams();
    if (month !== null) params.set('month', month);
    if (anchor === 'intake') params.set('anchor', 'intake');
    const qs = params.toString();
    const path = qs ? `/enrollment/calendar?${qs}` : `/enrollment/calendar`;
    apiFetch(path)
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
  }, [month, anchor]);

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
      // Attribution lands families on their INTAKE date; the recovery find
      // surface lands them on their STALL date.
      const dateIso =
        anchor === 'intake'
          ? (entry.intake_date ?? entry.stall_date)
          : entry.stall_date;
      const d = dayOf(dateIso);
      const bucket = map.get(d);
      if (bucket) bucket.push(entry);
      else map.set(d, [entry]);
    }
    return map;
  }, [state, anchor]);

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
                  {anchor === 'intake' ? (
                    <AttributionCell
                      entries={entries}
                      onOpen={() => openDay(day)}
                    />
                  ) : hasMany ? (
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

          {/* Widen affordances · the scope dial proper lives on the triage list. */}
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

// The admin ATTRIBUTION cell (anchor=intake): the day's intake COUNT + per-agent
// CHIPS (name + that agent's share of the day's intake) + an UNOWNED-ALARM line
// (the day's unowned pool — families with no owning agent). One cell per day; the
// chips are derived per cell so attribution is local to the day.
// (MULTI_AGENT_COCKPIT.md §5 admin lens.)
function AttributionCell({
  entries,
  onOpen,
}: {
  entries: readonly CalendarEntry[];
  onOpen: () => void;
}): JSX.Element | null {
  if (entries.length === 0) return null;
  const total = entries.length;

  // Per-agent tallies (owned only); the unowned pool feeds the alarm line.
  const byAgent = new Map<string, { name: string; count: number }>();
  let unowned = 0;
  for (const e of entries) {
    if (e.assigned_rep_id) {
      const name = e.agent_name ?? e.assigned_rep_id;
      const prev = byAgent.get(e.assigned_rep_id);
      if (prev) prev.count += 1;
      else byAgent.set(e.assigned_rep_id, { name, count: 1 });
    } else {
      unowned += 1;
    }
  }
  const agents = [...byAgent.values()].sort((a, b) => b.count - a.count);

  return (
    <button
      type="button"
      data-testid="intake-attribution"
      onClick={onOpen}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '2px',
        width: '100%',
        textAlign: 'left',
        background: 'transparent',
        border: 'none',
        padding: 0,
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      <span
        className="mono"
        data-testid="intake-count"
        title="Intakes this day"
        style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}
      >
        {total} in
      </span>
      <span style={{ display: 'flex', flexWrap: 'wrap', gap: '2px' }}>
        {agents.map((a) => (
          <span
            key={a.name}
            className="mono"
            data-testid="intake-agent-chip"
            title={`${a.name} · ${a.count} of ${total}`}
            style={{
              fontSize: 'var(--fs-chip)',
              lineHeight: 1.5,
              borderRadius: 'var(--r-xs)',
              padding: '1px 5px',
              color: 'var(--flow-ink)',
              background: 'var(--flow-wash)',
              border: '1px solid var(--flow)',
              whiteSpace: 'nowrap',
            }}
          >
            {a.name} {a.count}/{total}
          </span>
        ))}
      </span>
      {unowned > 0 && (
        <span
          className="mono"
          data-testid="intake-unowned-alarm"
          title="Unowned this day"
          style={{
            fontSize: 'var(--fs-chip)',
            color: 'var(--signal-ink)',
            fontWeight: 700,
          }}
        >
          ⚠ {unowned} unowned
        </span>
      )}
    </button>
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
          <option value="recency">recency</option>
        </select>
      </label>
    </div>
  );
}

// The shared row cap (the triage + history lists reuse it so all surfaces agree).
// eslint-disable-next-line react-refresh/only-export-components
export { ROW_CAP };

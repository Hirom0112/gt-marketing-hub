import { useEffect, useMemo, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card } from '../ui';
import {
  type ContactStatus,
  isContactStatus,
  recencyClass,
  recencyTitle,
  recencyVars,
} from './recency';

// Enrollment calendar / month view (S9 Wave 4; ANALYSIS item 4). Consumes
// GET /enrollment/calendar?month=YYYY-MM → { month, entries:[{ family_id,
// display_name, apply_date, current_stage, contact_status }] } and lays families
// out on the DAY THEY APPLIED in a month grid. Each family is a color-coded chip
// (the contact-recency tint, reused from `recency.ts`); clicking a chip selects
// that family in the deal panel (onSelectFamily). Native fetch only (≤12-dep
// budget). Read-only (INV-2) — it only reads the calendar + raises selection.

// One family on the calendar (backend CalendarEntry).
interface CalendarEntry {
  family_id: string;
  display_name: string;
  apply_date: string;
  current_stage: string;
  contact_status: string;
}

interface CalendarResponse {
  month: string;
  entries: CalendarEntry[];
}

interface EnrollmentCalendarProps {
  // Initial month (YYYY-MM). Defaults to a fixed synthetic-data month so the
  // surface always has data on first render; the operator can page months.
  initialMonth?: string;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CalendarResponse };

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

// The synthetic generator seeds applications around this month; default here so
// the calendar isn't empty on first open. (Not a tunable — a sensible UI default
// the operator immediately pages away from; the live data drives the content.)
const DEFAULT_MONTH = '2026-06';

// Shift a YYYY-MM string by ±1 month (pure string/number math, no Date tz traps).
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

// The day-of-month an apply_date falls on (UTC, matching the server's instant).
function dayOf(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return 0;
  return new Date(ms).getUTCDate();
}

// A pretty month header label, e.g. "June 2026".
function monthLabel(month: string): string {
  const [yStr, mStr] = month.split('-');
  const y = Number(yStr);
  const m = Number(mStr);
  if (Number.isNaN(y) || Number.isNaN(m)) return month;
  const date = new Date(Date.UTC(y, m - 1, 1));
  return date.toLocaleString('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export default function EnrollmentCalendar({
  initialMonth = DEFAULT_MONTH,
  selectedFamilyId,
  onSelectFamily,
}: EnrollmentCalendarProps): JSX.Element {
  const [month, setMonth] = useState(initialMonth);
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/enrollment/calendar?month=${month}`)
      .then((res) => {
        if (!res.ok) throw new Error(`calendar request failed: ${res.status}`);
        return res.json() as Promise<CalendarResponse>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
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

  // Group entries by day-of-month for the grid (memoized off the loaded data).
  const byDay = useMemo<Map<number, CalendarEntry[]>>(() => {
    const map = new Map<number, CalendarEntry[]>();
    if (state.status !== 'ready') return map;
    for (const entry of state.data.entries) {
      const d = dayOf(entry.apply_date);
      const bucket = map.get(d);
      if (bucket) bucket.push(entry);
      else map.set(d, [entry]);
    }
    return map;
  }, [state]);

  // The day cells for the month: leading blanks for the first weekday offset,
  // then one cell per day. Pure of the loaded entries (only the grid shape).
  const cells = useMemo<Array<number | null>>(() => {
    const [yStr, mStr] = month.split('-');
    const y = Number(yStr);
    const m = Number(mStr);
    if (Number.isNaN(y) || Number.isNaN(m)) return [];
    const firstWeekday = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
    const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const out: Array<number | null> = [];
    for (let i = 0; i < firstWeekday; i += 1) out.push(null);
    for (let d = 1; d <= daysInMonth; d += 1) out.push(d);
    return out;
  }, [month]);

  return (
    <section aria-label="Enrollment calendar" data-testid="enrollment-calendar">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--s-2)',
          marginBottom: 'var(--s-3)',
        }}
      >
        <div
          className="lab"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-1)' }}
        >
          <CalendarDays size={11} aria-hidden /> Calendar — families by apply date
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          <Button
            icon={ChevronLeft}
            data-testid="calendar-prev"
            onClick={() => setMonth((mo) => shiftMonth(mo, -1))}
            aria-label="Previous month"
          />
          <span
            className="mono"
            data-testid="calendar-month-label"
            style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, minWidth: 96, textAlign: 'center' }}
          >
            {monthLabel(month)}
          </span>
          <Button
            icon={ChevronRight}
            data-testid="calendar-next"
            onClick={() => setMonth((mo) => shiftMonth(mo, 1))}
            aria-label="Next month"
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
          <div
            className="calendar-weekdays"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: 'var(--s-1)',
              marginBottom: 'var(--s-1)',
            }}
          >
            {WEEKDAYS.map((wd) => (
              <div key={wd} className="lab" style={{ textAlign: 'center' }}>
                {wd}
              </div>
            ))}
          </div>
          <div
            className="calendar-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: 'var(--s-1)',
            }}
          >
            {cells.map((day, i) => {
              if (day === null) {
                return <div key={`blank-${i}`} aria-hidden />;
              }
              const entries = byDay.get(day) ?? [];
              return (
                <div
                  key={`day-${day}`}
                  data-testid={`calendar-day-${day}`}
                  style={{
                    minHeight: 64,
                    border: '1px solid var(--line)',
                    borderRadius: 'var(--r-sm)',
                    padding: 'var(--s-1)',
                    background: 'var(--surface-2)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 2,
                  }}
                >
                  <span className="lab" style={{ color: 'var(--muted)' }}>
                    {day}
                  </span>
                  {entries.map((entry) => (
                    <CalendarChip
                      key={entry.family_id}
                      entry={entry}
                      active={entry.family_id === selectedFamilyId}
                      onSelect={onSelectFamily}
                    />
                  ))}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </section>
  );
}

// One clickable family chip on a calendar day, tinted by its contact-recency
// status. Clicking raises onSelectFamily so the deal panel focuses this family.
function CalendarChip({
  entry,
  active,
  onSelect,
}: {
  entry: CalendarEntry;
  active: boolean;
  onSelect?: (familyId: string) => void;
}): JSX.Element {
  const known: ContactStatus | null = isContactStatus(entry.contact_status)
    ? entry.contact_status
    : null;
  const v = known === null ? null : recencyVars(known);
  const cls = known === null ? 'recency-unknown' : recencyClass(known);
  return (
    <button
      type="button"
      data-testid={`calendar-chip-${entry.family_id}`}
      className={`calendar-chip ${cls}${active ? ' active' : ''}`}
      data-recency={known ?? 'unknown'}
      title={
        known === null
          ? entry.display_name
          : `${entry.display_name} — ${recencyTitle(known)}`
      }
      onClick={() => onSelect?.(entry.family_id)}
      style={{
        font: 'inherit',
        fontSize: 'var(--fs-chip)',
        textAlign: 'left',
        cursor: onSelect ? 'pointer' : 'default',
        borderRadius: 'var(--r-xs)',
        padding: '1px 5px',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        color: v === null ? 'var(--muted)' : v.ink,
        background: v === null ? 'var(--paper)' : v.wash,
        border: `1px solid ${v === null ? 'var(--line)' : v.solid}`,
        outline: active ? '2px solid var(--ink)' : 'none',
      }}
    >
      {entry.display_name}
    </button>
  );
}

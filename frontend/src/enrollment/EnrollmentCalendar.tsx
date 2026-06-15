import { useEffect, useMemo, useRef, useState } from 'react';
import { CalendarClock, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card } from '../ui';
import {
  type ContactStatus,
  isContactStatus,
  recencyClass,
  recencyTitle,
  recencyVars,
} from './recency';

// Enrollment calendar / month view — the operator's primary "FIND" surface
// (S11 W2; A-16). Consumes GET /enrollment/calendar (month OPTIONAL) →
// { month, entries:[{ family_id, display_name, stall_date, apply_date,
// current_stage, contact_status, value, score }] } and lays families out on the
// DAY THEY WENT COLD (stall_date), not the day they applied — a recovery tool
// answers "when did this family stall?". Each family is a color-coded chip (the
// contact-recency tint, reused from `recency.ts`) that ALSO carries a compact
// recovery-value badge ($10k) so the chip encodes both "when" and "how urgent"
// at a glance. Clicking a chip selects the family in the deal panel
// (onSelectFamily). Native fetch only (≤12-dep budget). Read-only (INV-2).

// One family on the calendar (backend CalendarEntry, A-16 shape).
interface CalendarEntry {
  family_id: string;
  display_name: string;
  // The NEW grouping key — the day the family went cold (A-16).
  stall_date: string;
  // Retained for reference (when they originally applied).
  apply_date?: string;
  current_stage: string;
  contact_status: string;
  // Recovery dollars in play + the 0..1 work-queue score (so a chip encodes
  // urgency without N extra calls).
  value: number;
  score: number;
}

interface CalendarResponse {
  // Echoes the RESOLVED month — on a no-param first load this is the month of
  // the most-recent stall, so the surface always opens non-empty.
  month: string;
  entries: CalendarEntry[];
}

interface EnrollmentCalendarProps {
  // Optional fixed opening month (YYYY-MM) — tests pin it for determinism. When
  // omitted, the calendar opens on the SERVER-RESOLVED most-recent-stall month
  // (fetch with no `month` param, then read it back from the response).
  initialMonth?: string;
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CalendarResponse };

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

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

// The day-of-month an ISO instant falls on (UTC, matching the server's instant).
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

// A compact recovery-dollar badge, e.g. 10474 → "$10k", 2618.5 → "$2.6k",
// 900 → "$900". Kept terse so the chip reads at a glance.
function shortDollars(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '$0';
  if (value >= 1000) {
    const k = value / 1000;
    const rounded = k >= 10 ? Math.round(k) : Math.round(k * 10) / 10;
    return `$${rounded}k`;
  }
  return `$${Math.round(value)}`;
}

export default function EnrollmentCalendar({
  initialMonth,
  selectedFamilyId,
  onSelectFamily,
}: EnrollmentCalendarProps): JSX.Element {
  // `null` ⇒ no month chosen yet → open on the server-resolved most-recent-stall
  // month (fetch with no param, read `response.month` back). Never hardcode a
  // month here (A-16). After the first load we ADOPT the resolved month into
  // state so prev/next page from there — but we must NOT re-fetch that same
  // month (a redundant reload would detach the just-rendered chips). The
  // `loadedMonth` ref records the month we last fetched so the effect can skip a
  // no-op adopt.
  const [month, setMonth] = useState<string | null>(initialMonth ?? null);
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const loadedMonth = useRef<string | null>(null);

  useEffect(() => {
    // Skip the redundant fetch when we adopt the server-resolved month: the data
    // we already loaded (with no param) IS that month — re-fetching it would just
    // flash loading and detach the rendered chips mid-click.
    if (month !== null && loadedMonth.current === month) return;

    let cancelled = false;
    setState({ status: 'loading' });
    // Omit `month` until the operator pages (or a test pins it) — the server
    // resolves to the most-recent-stall month and echoes it back.
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
        // Adopt the server-resolved month so prev/next page from there (this
        // re-runs the effect, but the guard above makes it a no-op fetch).
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

  // The month actually shown in the grid: the chosen month, else (first load)
  // the month the server resolved in the loaded response.
  const shownMonth =
    month ?? (state.status === 'ready' ? state.data.month : null);

  // Group entries by stall-day-of-month for the grid (memoized off the data).
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

  // The day cells for the shown month: leading blanks for the first weekday
  // offset, then one cell per day. Pure of the loaded entries (only grid shape).
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
          <CalendarClock size={12} aria-hidden /> Families by stall date
        </div>
        <div className="calendar-pager">
          <Button
            icon={ChevronLeft}
            data-testid="calendar-prev"
            onClick={() =>
              setMonth((mo) => (mo === null ? mo : shiftMonth(mo, -1)))
            }
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
            onClick={() =>
              setMonth((mo) => (mo === null ? mo : shiftMonth(mo, 1)))
            }
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
              return (
                <div
                  key={`day-${day}`}
                  data-testid={`calendar-day-${day}`}
                  className={`calendar-cell${entries.length > 0 ? ' has-families' : ''}`}
                >
                  <span className="lab calendar-daynum">{day}</span>
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

// One clickable family chip on a calendar day. It carries TWO urgency signals:
// (1) the contact-recency tint (overdue=red, fresh=grey, …) and (2) a compact
// recovery-value badge ($10k) — together "when did they go cold" + "how much is
// on the table". An overdue, high-value chip reads as the loudest at a glance.
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
  const badge = shortDollars(entry.value);
  const title =
    known === null
      ? `${entry.display_name} — ${badge} recoverable`
      : `${entry.display_name} — ${recencyTitle(known)} · ${badge} recoverable`;
  return (
    <button
      type="button"
      data-testid={`calendar-chip-${entry.family_id}`}
      className={`calendar-chip ${cls}${active ? ' active' : ''}`}
      data-recency={known ?? 'unknown'}
      title={title}
      onClick={() => onSelect?.(entry.family_id)}
      style={{
        cursor: onSelect ? 'pointer' : 'default',
        color: v === null ? 'var(--muted)' : v.ink,
        background: v === null ? 'var(--paper)' : v.wash,
        borderColor: v === null ? 'var(--line)' : v.solid,
      }}
    >
      <span className="calendar-chip-name">{entry.display_name}</span>
      <span
        className="calendar-chip-value mono"
        data-testid={`calendar-chip-value-${entry.family_id}`}
        aria-hidden
        style={{
          color: v === null ? 'var(--muted)' : v.solid,
          borderColor: v === null ? 'var(--line)' : v.solid,
        }}
      >
        {badge}
      </span>
    </button>
  );
}

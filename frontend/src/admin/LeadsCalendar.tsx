import { useEffect, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card } from '../ui';
import type { LeadsCalendarResponse } from './types';

// The Leads-tab calendar (admin-dashboard redesign; D-3). Reads
// GET /enrollment/leads-calendar?month=YYYY-MM → per-day NEW-lead counts split by
// owning sales agent. The month is OPTIONAL on first load: the endpoint resolves
// it to the latest data month so the surface opens non-empty; we read the resolved
// `month` back from the response and drive prev/next from it. Each populated day
// shows one chip per agent (name + lead count) and an unowned indicator; a subtle
// heatmap tint weights high-volume days. Clicking an agent chip switches to the
// list view pre-filtered to that agent + that day. Read-only GETs (INV-2).

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

function gridCells(month: string): Array<number | null> {
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
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: LeadsCalendarResponse };

interface LeadsCalendarProps {
  // Pin the opening month (tests). Omitted → the server-resolved latest month.
  initialMonth?: string;
  // Clicking an agent chip → switch to the list, filtered to (agentId, day).
  onPickAgentDay: (agentId: string, day: number, month: string) => void;
}

export default function LeadsCalendar({
  initialMonth,
  onPickAgentDay,
}: LeadsCalendarProps): JSX.Element {
  const [month, setMonth] = useState<string | null>(initialMonth ?? null);
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    const qs = month !== null ? `?month=${month}` : '';
    apiFetch(`/enrollment/leads-calendar${qs}`)
      .then((res) => {
        if (!res.ok) throw new Error(`leads-calendar failed: ${res.status}`);
        return res.json() as Promise<LeadsCalendarResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        setState({ status: 'ready', data });
        // First load resolved the latest data month — adopt it so prev/next work.
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

  const byDay = new Map<number, LeadsCalendarResponse['days'][number]>();
  let maxTotal = 0;
  if (state.status === 'ready') {
    for (const d of state.data.days) {
      byDay.set(d.day, d);
      if (d.total > maxTotal) maxTotal = d.total;
    }
  }

  return (
    <section aria-label="Leads calendar" data-testid="leads-calendar">
      <div className="calendar-head">
        <div className="lab calendar-head-title">
          <CalendarDays size={12} aria-hidden /> New leads by day · per-agent share
          + unowned
        </div>
        <div className="calendar-pager">
          <Button
            icon={ChevronLeft}
            data-testid="leads-cal-prev"
            aria-label="Previous month"
            disabled={shownMonth === null}
            onClick={() =>
              setMonth((m) => (m === null ? m : shiftMonth(m, -1)))
            }
          />
          <span
            className="mono calendar-month-label"
            data-testid="leads-cal-month"
          >
            {shownMonth === null ? '—' : monthLabel(shownMonth)}
          </span>
          <Button
            icon={ChevronRight}
            data-testid="leads-cal-next"
            aria-label="Next month"
            disabled={shownMonth === null}
            onClick={() => setMonth((m) => (m === null ? m : shiftMonth(m, 1)))}
          />
        </div>
      </div>

      {state.status === 'loading' && (
        <p data-testid="leads-cal-loading" className="lab">
          Loading the leads calendar…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="leads-cal-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load the leads calendar: {state.message}
        </p>
      )}

      {state.status === 'ready' && shownMonth !== null && (
        <Card pad>
          <div className="calendar-weekdays">
            {WEEKDAYS.map((wd) => (
              <div key={wd} className="lab" style={{ textAlign: 'center' }}>
                {wd}
              </div>
            ))}
          </div>
          <div className="leads-cal-grid">
            {gridCells(shownMonth).map((day, i) => {
              if (day === null) return <div key={`blank-${i}`} aria-hidden />;
              const entry = byDay.get(day);
              const total = entry?.total ?? 0;
              // A subtle heat weight on busy days (opacity ramp, no new colour).
              const weight =
                maxTotal > 0 && total > 0
                  ? 0.12 + (total / maxTotal) * 0.18
                  : 0;
              return (
                <div
                  key={`day-${day}`}
                  data-testid={`leads-cal-day-${day}`}
                  className={`leads-cal-cell${total > 0 ? ' has-leads' : ''}`}
                  style={
                    weight > 0
                      ? {
                          background: `rgba(var(--heat-from), ${weight})`,
                        }
                      : undefined
                  }
                >
                  <span className="lab leads-cal-daynum">{day}</span>
                  {entry?.agents.map((a) => (
                    <button
                      key={a.agent_id}
                      type="button"
                      className="leads-agent-chip"
                      data-testid="leads-agent-chip"
                      title={`${a.synthetic_name} · ${a.count} of ${total}`}
                      onClick={() => onPickAgentDay(a.agent_id, day, shownMonth)}
                    >
                      <span
                        style={{
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {a.synthetic_name}
                      </span>
                      <span className="leads-agent-chip-count">{a.count}</span>
                    </button>
                  ))}
                  {entry && entry.unowned_count > 0 && (
                    <span
                      className="leads-cal-unowned"
                      data-testid="leads-cal-unowned"
                      title="Unowned this day"
                    >
                      ⚠ {entry.unowned_count} unowned
                    </span>
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

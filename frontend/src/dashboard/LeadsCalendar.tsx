import { useEffect, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card } from '../ui';
import HeatCell from '../enrollment/HeatCell';
import { shortDollars } from '../enrollment/format';
import type { LeadsCalendarResponse } from './types';

// The shared Leads-tab calendar (redesign R2; D-3). Reads
// GET /enrollment/leads-calendar?month=YYYY-MM[&owner=] → per-day NEW-lead counts
// split by owning sales agent. Owner-scoping is already enforced server-side by the
// demo principal; the optional `owner` prop only narrows further when a shell asks.
// Each day cell REUSES enrollment/HeatCell for the subtle heat weight (heat = that
// day's total lead volume, light→dark) and overlays one agent chip per agent
// (synthetic_name + count) plus an unowned indicator. Clicking a day emits {day};
// clicking an agent chip emits {day, agentId} so the parent can switch to the list
// pre-filtered to that day (+agent). Read-only GETs (INV-2).

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

// Heat saturates at this many leads on a single day — keeps the ramp subtle so a
// busy day reads darker without the grid turning into a wall of colour.
const HEAT_SATURATION = 12;

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
  // A day click emits {day}; an agent-chip click emits {day, agentId}. The parent
  // switches to the list pre-filtered to that day (+agent).
  onDrillToList: (filter: { day: number; agentId?: string }) => void;
  // Narrow to a single owner (agent shell). Omitted → the principal's own scope.
  owner?: string;
  // Tests pin the opening month; production resolves the server's latest month.
  initialMonth?: string;
}

export default function LeadsCalendar({
  onDrillToList,
  owner,
  initialMonth,
}: LeadsCalendarProps): JSX.Element {
  const [month, setMonth] = useState<string | null>(initialMonth ?? null);
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    const qs = new URLSearchParams();
    if (month !== null) qs.set('month', month);
    if (owner) qs.set('owner', owner);
    const suffix = qs.toString() === '' ? '' : `?${qs.toString()}`;
    apiFetch(`/enrollment/leads-calendar${suffix}`)
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
  }, [month, owner]);

  const shownMonth =
    month ?? (state.status === 'ready' ? state.data.month : null);

  const byDay = new Map<number, LeadsCalendarResponse['days'][number]>();
  if (state.status === 'ready') {
    for (const d of state.data.days) byDay.set(d.day, d);
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
              const dollarsRisk = entry?.unowned_count ?? 0;
              return (
                <div
                  key={`day-${day}`}
                  data-testid={`leads-cal-day-${day}`}
                  className={`leads-cal-cell${total > 0 ? ' has-leads' : ''}`}
                >
                  <span className="lab leads-cal-daynum">{day}</span>
                  {total > 0 ? (
                    <>
                      {/* The day's heat-weighted tile (reused HeatCell). A subtle
                          light→dark ramp on volume; clicking it drills to {day}. */}
                      <HeatCell
                        count={total}
                        atRisk={
                          dollarsRisk > 0
                            ? `${dollarsRisk} unowned`
                            : shortDollars(0)
                        }
                        intensity={Math.min(1, total / HEAT_SATURATION)}
                        onClick={() => onDrillToList({ day })}
                      />
                      <div
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 'var(--s-1)',
                          marginTop: 'var(--s-1)',
                        }}
                      >
                        {entry?.agents.map((a) => (
                          <button
                            key={a.agent_id}
                            type="button"
                            className="leads-agent-chip"
                            data-testid="leads-agent-chip"
                            data-agent={a.agent_id}
                            title={`${a.synthetic_name} · ${a.count} of ${total}`}
                            onClick={() =>
                              onDrillToList({ day, agentId: a.agent_id })
                            }
                          >
                            <span
                              style={{
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                              }}
                            >
                              {a.synthetic_name}
                            </span>
                            <span className="leads-agent-chip-count">
                              {a.count}
                            </span>
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
                    </>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </section>
  );
}

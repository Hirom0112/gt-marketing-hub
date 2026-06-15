import {
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EnrollmentCalendar, { type DrillBulk } from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2). The S12 W4 recovery calendar: families land on
// the day they STALLED; a day with ≤4 families lays them out as CalendarChips, a
// busier day COLLAPSES to a HeatCell tinted by volume × $; tapping a heat cell
// DRILLS into that day (a `?day=N` fetch) → a ranked DrillRow list + a BulkBar.
// Read-only GETs; bulk writes are delegated to the shared `bulk` prop.

const FAM_ONE = '11111111-1111-4111-8111-111111111111';
const FAM_TWO = '22222222-2222-4222-8222-222222222222';

// A few-family month (each day has ≤4 → chips).
const CALENDAR_SPARSE = {
  month: '2026-06',
  entries: [
    {
      family_id: FAM_ONE,
      display_name: 'The Alvarez Family',
      stall_date: '2026-06-10T09:00:00Z',
      current_stage: 'enroll',
      contact_status: 'overdue',
      value: 10474,
      score: 0.91,
      recoverable_now: 9000,
      freshness: 0.9,
      recovery_state: 'stalled',
    },
    {
      family_id: FAM_TWO,
      display_name: 'The Bauer Family',
      stall_date: '2026-06-18T09:00:00Z',
      current_stage: 'apply',
      contact_status: 'fresh',
      value: 30000,
      score: 0.74,
      recoverable_now: 20000,
      freshness: 0.95,
      recovery_state: 'stalled',
    },
  ],
};

// A busy day (>4 on the 10th → heat cell). The drill (?day=10) returns the day.
function busyDay(n: number) {
  return {
    month: '2026-06',
    entries: Array.from({ length: n }, (_, i) => ({
      family_id: `f-${i}`,
      display_name: `The Family ${i}`,
      stall_date: '2026-06-10T09:00:00Z',
      current_stage: 'enroll',
      contact_status: i % 2 ? 'overdue' : 'fresh',
      value: 1000 * (i + 1),
      score: 0.9,
      recoverable_now: 900 * (i + 1),
      freshness: 0.9,
      recovery_state: 'stalled',
    })),
  };
}

function noopBulk(overrides: Partial<DrillBulk> = {}): DrillBulk {
  return {
    selected: new Set(),
    onToggle: vi.fn(),
    onSelectAll: vi.fn(),
    onClear: vi.fn(),
    onNudge: vi.fn(),
    onCapture: vi.fn(),
    onDismissStart: vi.fn(),
    pendingDismiss: false,
    reasons: ['Declined'],
    onDismiss: vi.fn(),
    onCancelDismiss: vi.fn(),
    ...overrides,
  };
}

// Route the calendar month vs. the day drill (?day=) to the right payload.
function routedCalendar(month: unknown, day: unknown): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    const u = String(url);
    const payload = /day=/.test(u) ? day : month;
    return { ok: true, status: 200, json: async () => payload };
  });
}

describe('EnrollmentCalendar (S12 W4)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lays ≤4 families out as chips on their stall day with a compact value', async () => {
    vi.stubGlobal('fetch', routedCalendar(CALENDAR_SPARSE, {}));
    render(
      <EnrollmentCalendar initialMonth="2026-06" bulk={noopBulk()} />,
    );

    const alvarez = await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(alvarez).toHaveTextContent('The Alvarez Family');
    expect(alvarez).toHaveClass('recency-overdue');
    expect(
      screen.getByTestId(`calendar-chip-value-${FAM_ONE}`),
    ).toHaveTextContent('$10k');

    // Sits in the day-10 cell.
    expect(screen.getByTestId('calendar-day-10')).toContainElement(alvarez);
  });

  it('opens on the server-resolved month when none is given', async () => {
    vi.stubGlobal('fetch', routedCalendar(CALENDAR_SPARSE, {}));
    render(<EnrollmentCalendar bulk={noopBulk()} />);

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/enrollment\/calendar$/,
    );
    expect(screen.getByTestId('calendar-month-label')).toHaveTextContent(
      'June 2026',
    );
  });

  it('collapses a busy day to a heat cell that drills into a ranked list', async () => {
    const day = busyDay(12);
    vi.stubGlobal('fetch', routedCalendar(busyDay(12), day));
    const onSelectAll = vi.fn();
    render(
      <EnrollmentCalendar
        initialMonth="2026-06"
        bulk={noopBulk({ onSelectAll })}
      />,
    );

    // The 10th has 12 stalls → a heat cell, not chips.
    const heat = await screen.findByTestId('heat-cell');
    expect(heat).toHaveTextContent('12');
    expect(screen.queryByTestId('calendar-chip-f-0')).toBeNull();

    // Tap it → drill (a ?day= fetch) → a back header + a ranked DrillRow list.
    fireEvent.click(heat);
    expect(await screen.findByTestId('calendar-drill')).toBeInTheDocument();
    expect(screen.getByTestId('drill-back')).toBeInTheDocument();
    // Rows render (ranked) and the bulk select-all is wired.
    expect(await screen.findByTestId('drill-row-f-0')).toBeInTheDocument();

    // The drill fetched the day endpoint with ?day=10.
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(
      fetchMock.mock.calls.some((c) => /day=10/.test(String(c[0]))),
    ).toBe(true);
  });

  it('selects a family from a chip click', async () => {
    vi.stubGlobal('fetch', routedCalendar(CALENDAR_SPARSE, {}));
    const onSelect = vi.fn();
    render(
      <EnrollmentCalendar
        initialMonth="2026-06"
        onSelectFamily={onSelect}
        bulk={noopBulk()}
      />,
    );
    fireEvent.click(await screen.findByTestId(`calendar-chip-${FAM_TWO}`));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(FAM_TWO));
  });
});

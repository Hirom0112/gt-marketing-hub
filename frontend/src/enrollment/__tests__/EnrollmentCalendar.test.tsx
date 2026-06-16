import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EnrollmentCalendar from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2). The S13 recovery calendar is the PRIMARY find
// surface and the INDEX into the triage list — it no longer drills internally
// (A-22). Families land on the day they STALLED; ≤4 families → CalendarChips, a
// busier day → a HeatCell. Tapping a heat cell or chip OPENS the triage list at
// DAY scope for that day (onOpenScope('day', dayISO)); "This week" → Week scope;
// "Show all" → All scope. Read-only GETs; no bulk, no drill list here.

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

// A busy day (>4 on the 10th → heat cell).
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

// The calendar only fetches the month now (no ?day= drill).
function monthFetch(month: unknown): ReturnType<typeof vi.fn> {
  return vi.fn(async () => ({ ok: true, status: 200, json: async () => month }));
}

describe('EnrollmentCalendar (S13)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lays ≤4 families out as chips on their stall day with a compact value', async () => {
    vi.stubGlobal('fetch', monthFetch(CALENDAR_SPARSE));
    render(
      <EnrollmentCalendar initialMonth="2026-06" onOpenScope={vi.fn()} />,
    );

    const alvarez = await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(alvarez).toHaveTextContent('The Alvarez Family');
    expect(alvarez).toHaveClass('recency-overdue');
    expect(
      screen.getByTestId(`calendar-chip-value-${FAM_ONE}`),
    ).toHaveTextContent('$10k');
    expect(screen.getByTestId('calendar-day-10')).toContainElement(alvarez);
  });

  it('opens on the server-resolved month when none is given', async () => {
    vi.stubGlobal('fetch', monthFetch(CALENDAR_SPARSE));
    render(<EnrollmentCalendar onOpenScope={vi.fn()} />);

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/enrollment\/calendar$/,
    );
    expect(screen.getByTestId('calendar-month-label')).toHaveTextContent(
      'June 2026',
    );
  });

  it('collapses a busy day to a heat cell that opens the triage list at DAY scope', async () => {
    vi.stubGlobal('fetch', monthFetch(busyDay(12)));
    const onOpenScope = vi.fn();
    render(
      <EnrollmentCalendar initialMonth="2026-06" onOpenScope={onOpenScope} />,
    );

    // The 10th has 12 stalls → a heat cell, not chips.
    const heat = await screen.findByTestId('heat-cell');
    expect(heat).toHaveTextContent('12');
    expect(screen.queryByTestId('calendar-chip-f-0')).toBeNull();

    // Tapping it opens the triage list at Day scope anchored on Jun 10 (no
    // internal drill, no ?day= fetch).
    fireEvent.click(heat);
    expect(onOpenScope).toHaveBeenCalledTimes(1);
    const call = onOpenScope.mock.calls[0] ?? [];
    expect(call[0]).toBe('day');
    expect(String(call[1])).toMatch(/^2026-06-10T/);
    // The calendar made no day-drill fetch.
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.some((c) => /day=/.test(String(c[0])))).toBe(
      false,
    );
  });

  it('a chip click selects the family AND opens its day in the triage list', async () => {
    vi.stubGlobal('fetch', monthFetch(CALENDAR_SPARSE));
    const onSelect = vi.fn();
    const onOpenScope = vi.fn();
    render(
      <EnrollmentCalendar
        initialMonth="2026-06"
        onSelectFamily={onSelect}
        onOpenScope={onOpenScope}
      />,
    );
    fireEvent.click(await screen.findByTestId(`calendar-chip-${FAM_TWO}`));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(FAM_TWO));
    // Day scope anchored on FAM_TWO's stall day (Jun 18).
    expect(onOpenScope).toHaveBeenCalledWith(
      'day',
      expect.stringMatching(/^2026-06-18T/),
    );
  });

  it('the "This week" affordance opens WEEK scope; "Show all" opens ALL scope', async () => {
    vi.stubGlobal('fetch', monthFetch(CALENDAR_SPARSE));
    const onOpenScope = vi.fn();
    render(
      <EnrollmentCalendar initialMonth="2026-06" onOpenScope={onOpenScope} />,
    );

    fireEvent.click(await screen.findByTestId('open-week'));
    expect(onOpenScope).toHaveBeenLastCalledWith(
      'week',
      expect.stringMatching(/^2026-06-/),
    );

    fireEvent.click(screen.getByTestId('open-all'));
    expect(onOpenScope).toHaveBeenLastCalledWith('all');
  });
});

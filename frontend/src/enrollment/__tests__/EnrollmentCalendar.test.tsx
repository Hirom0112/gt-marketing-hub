import {
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EnrollmentCalendar from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2). The enrollment calendar (S11 W2; A-16) consumes
// GET /enrollment/calendar (month optional) and lays families out on the day
// they STALLED (stall_date) as color-coded chips (the recency tint) that ALSO
// carry a compact recovery-value badge; clicking a chip selects that family in
// the deal panel (onSelectFamily). On a no-param first load it opens on the
// server-resolved most-recent-stall month read back from the response.

const FAM_ONE = '11111111-1111-4111-8111-111111111111';
const FAM_TWO = '22222222-2222-4222-8222-222222222222';

const CALENDAR = {
  month: '2026-06',
  entries: [
    {
      family_id: FAM_ONE,
      display_name: 'The Alvarez Family',
      stall_date: '2026-06-10T09:00:00Z',
      apply_date: '2026-05-02T09:00:00Z',
      current_stage: 'enroll',
      contact_status: 'overdue',
      value: 10474,
      score: 0.91,
    },
    {
      family_id: FAM_TWO,
      display_name: 'The Bauer Family',
      stall_date: '2026-06-18T09:00:00Z',
      apply_date: '2026-05-09T09:00:00Z',
      current_stage: 'apply',
      contact_status: 'fresh',
      value: 30000,
      score: 0.74,
    },
  ],
};

function calendarFetchMock(): ReturnType<typeof vi.fn> {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => CALENDAR,
  }));
}

describe('EnrollmentCalendar', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders families on their stall date as color-coded chips with a value badge', async () => {
    vi.stubGlobal('fetch', calendarFetchMock());
    render(<EnrollmentCalendar initialMonth="2026-06" />);

    const alvarez = await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(alvarez).toHaveTextContent('The Alvarez Family');
    // Overdue family chip carries the overdue recency class (the red tint).
    expect(alvarez).toHaveClass('recency-overdue');
    // The chip also encodes recovery urgency: a compact dollar badge ($10k).
    expect(
      screen.getByTestId(`calendar-chip-value-${FAM_ONE}`),
    ).toHaveTextContent('$10k');

    const bauer = screen.getByTestId(`calendar-chip-${FAM_TWO}`);
    expect(bauer).toHaveClass('recency-fresh');
    expect(
      screen.getByTestId(`calendar-chip-value-${FAM_TWO}`),
    ).toHaveTextContent('$30k');

    // The fetch targeted the requested (pinned) month.
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/enrollment\/calendar\?month=2026-06$/,
    );
  });

  it('opens on the server-resolved most-recent-stall month when no month is given', async () => {
    vi.stubGlobal('fetch', calendarFetchMock());
    render(<EnrollmentCalendar />);

    // First load omits the month param entirely (no ?month=)...
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/enrollment\/calendar$/,
    );

    // ...and the header adopts the month echoed back by the server.
    expect(screen.getByTestId('calendar-month-label')).toHaveTextContent(
      'June 2026',
    );
  });

  it('places a family in the correct stall-day cell', async () => {
    vi.stubGlobal('fetch', calendarFetchMock());
    render(<EnrollmentCalendar initialMonth="2026-06" />);

    // The Alvarez family stalled on the 10th — its chip sits in day-10's cell.
    await screen.findByTestId(`calendar-chip-${FAM_ONE}`);
    const dayCell = screen.getByTestId('calendar-day-10');
    expect(dayCell).toContainElement(
      screen.getByTestId(`calendar-chip-${FAM_ONE}`),
    );
  });

  it('selects the family when its chip is clicked', async () => {
    vi.stubGlobal('fetch', calendarFetchMock());
    const onSelect = vi.fn();
    render(
      <EnrollmentCalendar initialMonth="2026-06" onSelectFamily={onSelect} />,
    );

    const chip = await screen.findByTestId(`calendar-chip-${FAM_TWO}`);
    fireEvent.click(chip);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(FAM_TWO));
  });
});

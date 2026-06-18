import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LeadsCalendar from '../LeadsCalendar';

// Acceptance test (CLAUDE §4.2). The shared Leads calendar (redesign R2; D-3)
// reads GET /enrollment/leads-calendar?month=YYYY-MM → per-day NEW-lead counts
// split by owning agent. Each populated day REUSES enrollment/HeatCell for a
// subtle volume heat and overlays one agent chip (name + count) per agent.
// Clicking a day emits {day}; clicking an agent chip emits {day, agentId}.
// Prev/next refetches the shifted month. Read-only GETs (INV-2).

const AGENT_ONE = 'a0000000-0000-4000-8000-000000000001';
const AGENT_TWO = 'a0000000-0000-4000-8000-000000000002';

const JUNE = {
  month: '2026-06',
  days: [
    {
      day: 16,
      agents: [
        { agent_id: AGENT_ONE, synthetic_name: 'Riley Carter', count: 3 },
        { agent_id: AGENT_TWO, synthetic_name: 'Jordan Avery', count: 1 },
      ],
      unowned_count: 2,
      total: 6,
    },
    {
      day: 4,
      agents: [
        { agent_id: AGENT_ONE, synthetic_name: 'Riley Carter', count: 1 },
      ],
      unowned_count: 0,
      total: 1,
    },
  ],
};

const MAY = {
  month: '2026-05',
  days: [
    {
      day: 9,
      agents: [
        { agent_id: AGENT_TWO, synthetic_name: 'Jordan Avery', count: 5 },
      ],
      unowned_count: 0,
      total: 5,
    },
  ],
};

// A fetch mock keyed on the month query so prev/next return different payloads.
function calendarFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    const month = /month=([\d-]+)/.exec(url)?.[1] ?? '2026-06';
    const body = month.startsWith('2026-05') ? MAY : JUNE;
    return { ok: true, status: 200, json: async () => body };
  });
}

describe('LeadsCalendar (redesign R2)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders a HeatCell per populated day with a subtle volume heat', async () => {
    vi.stubGlobal('fetch', calendarFetch());
    render(<LeadsCalendar initialMonth="2026-06" onDrillToList={vi.fn()} />);

    // The 16th (6 leads) and the 4th (1 lead) each get a HeatCell; an empty day
    // gets none. The heat tile carries the day's total lead volume.
    const cells = await screen.findAllByTestId('heat-cell');
    expect(cells).toHaveLength(2);
    const busy = screen.getByTestId('leads-cal-day-16');
    expect(within(busy).getByTestId('heat-cell-count')).toHaveTextContent('6');
    // Subtle heat: the busier day's intensity custom prop (--i) is higher than
    // the quiet day's, and both stay below saturation (light→dark, never flat 1).
    const intensities = cells.map((c) =>
      Number((c as HTMLElement).style.getPropertyValue('--i')),
    );
    expect(Math.max(...intensities)).toBeLessThanOrEqual(1);
    expect(Math.max(...intensities)).toBeGreaterThan(Math.min(...intensities));
  });

  it('renders an agent chip with its count for each agent on a day', async () => {
    vi.stubGlobal('fetch', calendarFetch());
    render(<LeadsCalendar initialMonth="2026-06" onDrillToList={vi.fn()} />);

    const day16 = await screen.findByTestId('leads-cal-day-16');
    const chips = within(day16).getAllByTestId('leads-agent-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent('Riley Carter');
    expect(chips[0]).toHaveTextContent('3');
    expect(chips[1]).toHaveTextContent('Jordan Avery');
    expect(chips[1]).toHaveTextContent('1');
  });

  it('prev/next month nav refetches the shifted month', async () => {
    vi.stubGlobal('fetch', calendarFetch());
    render(<LeadsCalendar initialMonth="2026-06" onDrillToList={vi.fn()} />);

    await screen.findByTestId('leads-cal-day-16');
    expect(screen.getByTestId('leads-cal-month')).toHaveTextContent('June 2026');

    fireEvent.click(screen.getByTestId('leads-cal-prev'));
    await waitFor(() =>
      expect(screen.getByTestId('leads-cal-month')).toHaveTextContent(
        'May 2026',
      ),
    );
    // The May payload landed (its only populated day is the 9th).
    expect(await screen.findByTestId('leads-cal-day-9')).toBeInTheDocument();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(
      fetchMock.mock.calls.some((c) => /month=2026-05/.test(String(c[0]))),
    ).toBe(true);
  });

  it('clicking an agent chip emits {day, agentId}; a day heat-cell emits {day}', async () => {
    vi.stubGlobal('fetch', calendarFetch());
    const onDrill = vi.fn();
    render(<LeadsCalendar initialMonth="2026-06" onDrillToList={onDrill} />);

    const day16 = await screen.findByTestId('leads-cal-day-16');
    const [chip] = within(day16).getAllByTestId('leads-agent-chip');
    fireEvent.click(chip as HTMLElement);
    expect(onDrill).toHaveBeenLastCalledWith({ day: 16, agentId: AGENT_ONE });

    // The day's heat tile drills to the day alone (no agent).
    fireEvent.click(within(day16).getByTestId('heat-cell'));
    expect(onDrill).toHaveBeenLastCalledWith({ day: 16 });
  });
});

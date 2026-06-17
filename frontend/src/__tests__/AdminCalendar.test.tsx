import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EnrollmentCalendar from '../enrollment/EnrollmentCalendar';

// M3 acceptance (MULTI_AGENT_COCKPIT.md §5 admin lens). The admin's calendar is
// the ATTRIBUTION flavor (anchor=intake): instead of the rep's stall-date heat,
// each day cell shows the day's INTAKE COUNT + per-agent CHIPS (name + share of
// that day's intake) + an UNOWNED-ALARM line (the day's unowned pool). The
// calendar reads GET /enrollment/calendar?anchor=intake — the same endpoint, the
// intake anchoring.
//
// The cell families land on their INTAKE date (intake_date), attributed to their
// owning agent; an unowned family contributes to the day's unowned-alarm line.

const RILEY = 'a0000000-0000-4000-8000-000000000001';
const JORDAN = 'a0000000-0000-4000-8000-000000000002';

// Day 12 (2026-06-12) has 3 intakes: 2 owned by Riley, 1 unowned (alarm).
const ATTRIBUTION_PAYLOAD = {
  month: '2026-06',
  anchor: 'intake',
  entries: [
    {
      family_id: 'd0000000-0000-4000-8000-000000000001',
      display_name: 'Fam A',
      stall_date: '2026-06-12T09:00:00Z',
      intake_date: '2026-06-12T09:00:00Z',
      current_stage: 'interest',
      contact_status: 'fresh',
      value: 10474,
      score: 0.8,
      assigned_rep_id: RILEY,
      agent_name: 'Riley Carter',
    },
    {
      family_id: 'd0000000-0000-4000-8000-000000000002',
      display_name: 'Fam B',
      stall_date: '2026-06-12T09:00:00Z',
      intake_date: '2026-06-12T09:00:00Z',
      current_stage: 'apply',
      contact_status: 'fresh',
      value: 30000,
      score: 0.7,
      assigned_rep_id: RILEY,
      agent_name: 'Riley Carter',
    },
    {
      family_id: 'd0000000-0000-4000-8000-000000000003',
      display_name: 'Fam C',
      stall_date: '2026-06-12T09:00:00Z',
      intake_date: '2026-06-12T09:00:00Z',
      current_stage: 'interest',
      contact_status: 'fresh',
      value: 10474,
      score: 0.6,
      assigned_rep_id: null,
      agent_name: null,
    },
    // A different day, owned by Jordan, to prove per-agent chips are per-cell.
    {
      family_id: 'd0000000-0000-4000-8000-000000000004',
      display_name: 'Fam D',
      stall_date: '2026-06-20T09:00:00Z',
      intake_date: '2026-06-20T09:00:00Z',
      current_stage: 'interest',
      contact_status: 'fresh',
      value: 10474,
      score: 0.5,
      assigned_rep_id: JORDAN,
      agent_name: 'Jordan Avery',
    },
  ],
};

function installFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/enrollment/calendar')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(ATTRIBUTION_PAYLOAD),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    }),
  );
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe('AdminCalendar (attribution / anchor=intake)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('adminCalendarReadsIntakeAnchor', async () => {
    installFetch();
    render(
      <EnrollmentCalendar
        anchor="intake"
        initialMonth="2026-06"
        onOpenScope={() => {}}
      />,
    );
    await waitFor(() => {
      expect(urlsCalled().some((u) => /anchor=intake/.test(u))).toBe(true);
    });
  });

  it('adminCalendarCellShowsIntakeCountPerAgentChipsAndUnownedAlarm', async () => {
    installFetch();
    render(
      <EnrollmentCalendar
        anchor="intake"
        initialMonth="2026-06"
        onOpenScope={() => {}}
      />,
    );

    // Day 12's attribution cell.
    const cell = await screen.findByTestId('calendar-day-12');

    // The day's intake COUNT (3 families landed on 2026-06-12).
    const count = within(cell).getByTestId('intake-count');
    expect(count).toHaveTextContent('3');

    // Per-agent chips: name + share. Riley owns 2 of 3 → "Riley Carter" + a
    // share readout for this cell.
    const chips = within(cell).getAllByTestId('intake-agent-chip');
    const rileyChip = chips.find((c) => /Riley Carter/.test(c.textContent ?? ''));
    expect(rileyChip).toBeDefined();
    // The chip carries the agent's share of the day's intake (2 of 3).
    expect(rileyChip).toHaveTextContent('2');

    // The unowned-alarm line — 1 of the 3 is unowned.
    const alarm = within(cell).getByTestId('intake-unowned-alarm');
    expect(alarm).toHaveTextContent('1');
  });

  it('adminCalendarAttributesPerCell', async () => {
    installFetch();
    render(
      <EnrollmentCalendar
        anchor="intake"
        initialMonth="2026-06"
        onOpenScope={() => {}}
      />,
    );

    // Day 20 is Jordan's single intake — its chip is Jordan, not Riley.
    const cell20 = await screen.findByTestId('calendar-day-20');
    const chips20 = within(cell20).getAllByTestId('intake-agent-chip');
    expect(chips20.some((c) => /Jordan Avery/.test(c.textContent ?? ''))).toBe(
      true,
    );
    expect(chips20.some((c) => /Riley Carter/.test(c.textContent ?? ''))).toBe(
      false,
    );
  });
});

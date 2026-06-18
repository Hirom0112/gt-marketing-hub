import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LeadsTab from '../LeadsTab';

// Acceptance test (CLAUDE §4.2). The shared Leads tab (redesign R2) toggles between
// Calendar (default) and List. Clicking an agent chip (or a day) in the calendar
// switches to the list pre-filtered to that day (+agent). Selecting a row lifts the
// family id to the shell. Read-only GETs (INV-2).

const AGENT_ONE = 'a0000000-0000-4000-8000-000000000001';
const FAM_A = 'fam-aaaa';
const FAM_B = 'fam-bbbb';

const CALENDAR = {
  month: '2026-06',
  days: [
    {
      day: 16,
      agents: [
        { agent_id: AGENT_ONE, synthetic_name: 'Riley Carter', count: 3 },
      ],
      unowned_count: 0,
      total: 3,
    },
  ],
};

// FAM_A assigned to AGENT_ONE, stalls Jun 16 (matches a chip drill to day 16 +
// AGENT_ONE). FAM_B assigned to AGENT_ONE but stalls Jun 02 (filtered out by the
// day pin), proving the pre-filter narrows the list.
const QUEUE = [
  {
    family_id: FAM_A,
    display_name: 'The Alvarez Family',
    value: 10474,
    contact_status: 'overdue',
    recovery_state: 'stalled',
    current_stage: 'enroll',
    assigned_rep_id: AGENT_ONE,
    stall_date: '2026-06-16T09:00:00Z',
    num_children: 1,
    funding_type: 'tefa_standard',
    recoverable_now: 9000,
    last_contact_at: null,
  },
  {
    family_id: FAM_B,
    display_name: 'The Bauer Family',
    value: 30000,
    contact_status: 'fresh',
    recovery_state: 'stalled',
    current_stage: 'apply',
    assigned_rep_id: AGENT_ONE,
    stall_date: '2026-06-02T09:00:00Z',
    num_children: 1,
    funding_type: 'self_pay',
    recoverable_now: 20000,
    last_contact_at: null,
  },
];

const STUDENTS = {
  households: [
    { family_id: FAM_A, students: [{ synthetic_first_name: 'Mateo' }] },
    { family_id: FAM_B, students: [{ synthetic_first_name: 'Sophie' }] },
  ],
};

function tabFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    if (/leads-calendar/.test(url))
      return { ok: true, status: 200, json: async () => CALENDAR };
    if (/\/students/.test(url))
      return { ok: true, status: 200, json: async () => STUDENTS };
    return { ok: true, status: 200, json: async () => QUEUE };
  });
}

describe('LeadsTab (redesign R2)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('defaults to the calendar view', async () => {
    vi.stubGlobal('fetch', tabFetch());
    render(
      <LeadsTab onSelectFamily={vi.fn()} initialMonth="2026-06" />,
    );
    expect(await screen.findByTestId('leads-calendar')).toBeInTheDocument();
    expect(screen.queryByTestId('leads-list')).toBeNull();
  });

  it('the toggle switches calendar ↔ list', async () => {
    vi.stubGlobal('fetch', tabFetch());
    render(<LeadsTab onSelectFamily={vi.fn()} initialMonth="2026-06" />);
    await screen.findByTestId('leads-calendar');

    const toggle = screen.getByTestId('leads-view-toggle');
    fireEvent.click(within(toggle).getByText('List'));
    expect(await screen.findByTestId('leads-list')).toBeInTheDocument();
    expect(screen.queryByTestId('leads-calendar')).toBeNull();

    fireEvent.click(within(toggle).getByText('Calendar'));
    expect(await screen.findByTestId('leads-calendar')).toBeInTheDocument();
  });

  it('a calendar chip click lands on a list pre-filtered to that day + agent', async () => {
    vi.stubGlobal('fetch', tabFetch());
    render(<LeadsTab onSelectFamily={vi.fn()} initialMonth="2026-06" />);

    const day16 = await screen.findByTestId('leads-cal-day-16');
    const [chip] = within(day16).getAllByTestId('leads-agent-chip');
    fireEvent.click(chip as HTMLElement);

    // We land on the list, pre-filtered to Jun 16 + AGENT_ONE → only FAM_A shows
    // (FAM_B stalls Jun 02, so the day pin filters it out).
    await screen.findByTestId('leads-list');
    await waitFor(() => {
      const rows = screen.getAllByTestId('lead-row');
      expect(rows).toHaveLength(1);
      expect(rows[0]).toHaveTextContent('The Alvarez Family');
    });
    // The agent select adopted AGENT_ONE from the chip.
    expect(screen.getByTestId('leads-filter-agent')).toHaveValue(AGENT_ONE);
  });
});

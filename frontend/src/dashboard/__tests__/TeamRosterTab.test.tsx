import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import TeamRosterTab from '../TeamRosterTab';

// Acceptance test (CLAUDE §4.2) for the admin Team Roster tab (R5, D-15). It
// reads GET /enrollment/agents?window=… → per-agent rows (queue / stall% /
// close% / load) and an unowned bucket. The Day/Week/Month/All window control
// passes ?window= to the endpoint (default 'all'); changing it refetches. The
// test mocks the backend (native fetch stub, apiFetch wraps it), so it does not
// depend on the backend agent finishing.
const AGENTS_PAYLOAD = {
  agents: [
    {
      agent_id: 'a0000000-0000-4000-8000-000000000001',
      synthetic_name: 'Riley Carter',
      tier: 'closer',
      queue_size: 8,
      stall_rate: 0.25,
      close_rate: 0.5,
      load: 0.2,
    },
    {
      agent_id: 'a0000000-0000-4000-8000-000000000002',
      synthetic_name: 'Jordan Avery',
      tier: 'setter',
      queue_size: 6,
      stall_rate: 0.5,
      close_rate: 0.333,
      load: 0.15,
    },
  ],
  unowned: {
    agent_id: null,
    synthetic_name: null,
    tier: null,
    queue_size: 10,
    stall_rate: 0.4,
    close_rate: 0.1,
    load: 0.25,
  },
};

function lastUrl(): string {
  const mock = fetch as unknown as ReturnType<typeof vi.fn>;
  const calls = mock.mock.calls;
  return calls[calls.length - 1]![0] as string;
}

describe('TeamRosterTab (admin per-agent KPIs)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(AGENTS_PAYLOAD),
        } as Response),
      ),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders one row per agent from a mocked /enrollment/agents response', async () => {
    render(<TeamRosterTab />);
    const rows = await screen.findAllByTestId('roster-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByTestId('roster-name')).toHaveTextContent(
      'Riley Carter',
    );
    expect(within(rows[0]!).getByTestId('roster-queue')).toHaveTextContent('8');
    // The unowned bucket renders its queue size from the rollup object.
    expect(await screen.findByTestId('roster-unowned')).toHaveTextContent('10');
  });

  it('fetches the default (all) window on mount', async () => {
    render(<TeamRosterTab />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(lastUrl()).toContain('/enrollment/agents');
    expect(lastUrl()).toContain('window=all');
  });

  it('refetches with the new window param when the window filter changes', async () => {
    render(<TeamRosterTab />);
    await screen.findAllByTestId('roster-row');

    fireEvent.click(screen.getByTestId('roster-window-week'));
    await waitFor(() => expect(lastUrl()).toContain('window=week'));

    fireEvent.click(screen.getByTestId('roster-window-month'));
    await waitFor(() => expect(lastUrl()).toContain('window=month'));

    fireEvent.click(screen.getByTestId('roster-window-day'));
    await waitFor(() => expect(lastUrl()).toContain('window=day'));
  });
});

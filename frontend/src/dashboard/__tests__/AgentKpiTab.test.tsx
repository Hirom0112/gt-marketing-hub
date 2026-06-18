import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AgentKpiTab from '../AgentKpiTab';

// Acceptance test (CLAUDE §4.2) for the sales-agent personal KPI Dashboard
// (R6 / D-14, Tab 5). The tab fetches GET /enrollment/agent-kpis?window=… and
// renders the seven metrics; changing the window control refetches with the new
// window param. The endpoint is owner-scoped server-side via the principal
// header; the test mocks the backend, so it does not depend on the backend
// agent finishing. Native fetch is stubbed (apiFetch wraps it).
const KPIS_PAYLOAD = {
  leads_assigned: 42,
  contacts_made: 30,
  follow_ups_completed: 18,
  appointments_booked: 9,
  applications_started: 7,
  applications_completed: 4,
  conversion_rate: 0.25,
};

function lastUrl(): string {
  const mock = fetch as unknown as ReturnType<typeof vi.fn>;
  const calls = mock.mock.calls;
  return calls[calls.length - 1]![0] as string;
}

describe('AgentKpiTab (personal performance dashboard)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(KPIS_PAYLOAD),
        } as Response),
      ),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders all seven metrics from a mocked /enrollment/agent-kpis response', async () => {
    render(<AgentKpiTab />);
    const grid = await screen.findByTestId('kpi-grid');

    expect(grid).toHaveTextContent('Leads Assigned');
    expect(grid).toHaveTextContent('42');
    expect(grid).toHaveTextContent('Contacts Made');
    expect(grid).toHaveTextContent('30');
    expect(grid).toHaveTextContent('Follow-Ups Completed');
    expect(grid).toHaveTextContent('18');
    expect(grid).toHaveTextContent('Appointments Booked');
    expect(grid).toHaveTextContent('9');
    expect(grid).toHaveTextContent('Applications Started');
    expect(grid).toHaveTextContent('7');
    expect(grid).toHaveTextContent('Applications Completed');
    expect(grid).toHaveTextContent('4');
    // Conversion Rate renders as a percentage (0.25 → 25%).
    expect(grid).toHaveTextContent('Conversion Rate');
    expect(grid).toHaveTextContent('25%');
  });

  it('fetches the default (all) window on mount', async () => {
    render(<AgentKpiTab />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(lastUrl()).toContain('/enrollment/agent-kpis');
    expect(lastUrl()).toContain('window=all');
  });

  it('refetches with the new window param when the window control changes', async () => {
    render(<AgentKpiTab />);
    await screen.findByTestId('kpi-grid');

    fireEvent.click(screen.getByTestId('kpi-window-week'));
    await waitFor(() => expect(lastUrl()).toContain('window=week'));

    fireEvent.click(screen.getByTestId('kpi-window-month'));
    await waitFor(() => expect(lastUrl()).toContain('window=month'));

    fireEvent.click(screen.getByTestId('kpi-window-day'));
    await waitFor(() => expect(lastUrl()).toContain('window=day'));
  });

  it('passes owner when an explicit agentId prop is provided', async () => {
    render(<AgentKpiTab agentId="agent-xyz" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(lastUrl()).toContain('owner=agent-xyz');
  });
});

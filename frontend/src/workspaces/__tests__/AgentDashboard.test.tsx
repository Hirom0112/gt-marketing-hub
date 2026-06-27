import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// Acceptance test (CLAUDE §4.2) for the sales-agent shell composition: a 4-metric
// KPI strip (BOOKED/CONTACTED/OVERDUE/ACTIVE), a daily-motivation banner, a 5-tab
// work area (Leads/Triage/Students/Reconcile/KPI Dashboard), and the right detail
// panel (empty until a row is selected). Children + session are stubbed to isolate
// the shell wiring; the tabs have their own tests.

const ROWS = [
  { value: 1000, contact_status: 'overdue', recovery_state: 'stalled' },
  { value: 500, contact_status: 'followed_up', recovery_state: 'working' },
];
const KPIS = { appointments_booked: 3, contacts_made: 7 };

vi.mock('../../config', () => ({
  apiFetch: vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url.includes('/work-queue')
          ? ROWS
          : url.includes('agent-kpis')
            ? KPIS
            : [],
    }),
  ),
}));

vi.mock('../../session/SessionContext', () => ({
  useSession: () => ({ session: { role: 'operator', agentId: 'agent-1' } }),
}));

vi.mock('../../dashboard/MotivationBanner', () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="stub-banner">{agentId}</div>
  ),
}));
vi.mock('../../dashboard/LeadsTab', () => ({
  default: ({ onSelectFamily }: { onSelectFamily: (id: string) => void }) => (
    <button data-testid="stub-leads" onClick={() => onSelectFamily('fam-1')}>
      leads
    </button>
  ),
}));
vi.mock('../../dashboard/TriageTab', () => ({
  default: () => <div data-testid="stub-triage" />,
}));
vi.mock('../../dashboard/StudentsTab', () => ({
  default: () => <div data-testid="stub-students" />,
}));
vi.mock('../../dashboard/ReconcileTab', () => ({
  default: () => <div data-testid="stub-reconcile" />,
}));
vi.mock('../../dashboard/AgentKpiTab', () => ({
  default: () => <div data-testid="stub-kpis" />,
}));
vi.mock('../../dashboard/DetailPanel', () => ({
  default: ({ familyId }: { familyId: string | null }) => (
    <div data-testid="stub-detail">{familyId ?? 'empty'}</div>
  ),
}));
vi.mock('../../dashboard/ReconcileDetail', () => ({
  default: () => <div data-testid="stub-reconcile-detail" />,
}));

import AgentDashboard from '../AgentDashboard';

describe('AgentDashboard shell', () => {
  it('renders 4 KPI metrics with the agent labels', async () => {
    render(<AgentDashboard />);
    await waitFor(() =>
      expect(screen.getAllByTestId('kpi-metric')).toHaveLength(4),
    );
    for (const label of ['BOOKED', 'CONTACTED', 'OVERDUE', 'ACTIVE']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('renders the daily motivation banner', () => {
    render(<AgentDashboard />);
    expect(screen.getByTestId('stub-banner')).toHaveTextContent('agent-1');
  });

  it('renders the 5 work-area tabs and defaults to Leads', () => {
    render(<AgentDashboard />);
    for (const label of [
      'Leads',
      'Triage',
      'Students',
      'Reconcile',
      'KPI Dashboard',
    ]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByTestId('stub-leads')).toBeInTheDocument();
  });

  it('switches to the Triage and KPI tabs', () => {
    render(<AgentDashboard />);
    fireEvent.click(screen.getByRole('tab', { name: 'Triage' }));
    expect(screen.getByTestId('stub-triage')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'KPI Dashboard' }));
    expect(screen.getByTestId('stub-kpis')).toBeInTheDocument();
  });

  it('right panel is empty until a row is selected', () => {
    render(<AgentDashboard />);
    expect(screen.getByTestId('stub-detail')).toHaveTextContent('empty');
    fireEvent.click(screen.getByTestId('stub-leads'));
    expect(screen.getByTestId('stub-detail')).toHaveTextContent('fam-1');
  });
});
